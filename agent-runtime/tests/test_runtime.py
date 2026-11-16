"""Unit tests for tool validation, structured LLM output parsing, and iteration limit enforcement."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import grpc
import pytest

# Ensure agent-runtime and app/pb are importable
RUNTIME_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PB_DIR = os.path.join(RUNTIME_ROOT, "app", "pb")
if RUNTIME_ROOT not in sys.path:
    sys.path.insert(0, RUNTIME_ROOT)
if PB_DIR not in sys.path:
    sys.path.insert(0, PB_DIR)

from app.agent.loop import MAX_ITERATIONS_CAP, normalize_max_iterations, run_agent_loop
from app.grpc_server import create_grpc_server
from app.llm.schemas import AgentStep, FinalReport, parse_agent_step
from app.pb import agent_pb2, agent_pb2_grpc
from app.tools.executor import ToolExecutionResult
from app.tools.registry import ToolValidationError, validate_tool_call


# ---------------------------------------------------------------------------
# 1. Tool Registry & Schema / Security Validation Tests
# ---------------------------------------------------------------------------


def test_validate_list_files_defaults_and_valid_path():
    assert validate_tool_call("list_files", {}) == {"path": "."}
    assert validate_tool_call("list_files", {"path": ""}) == {"path": "."}
    assert validate_tool_call("list_files", {"path": "src/utils"}) == {"path": "src/utils"}


def test_validate_read_and_write_file_valid():
    assert validate_tool_call("read_file", {"path": "calculator.py"}) == {"path": "calculator.py"}
    res = validate_tool_call(
        "write_file", {"path": "calculator.py", "content": "def add(a, b): return a + b\n"}
    )
    assert res["path"] == "calculator.py"
    assert "def add" in res["content"]


def test_validate_search_files_valid():
    res = validate_tool_call("search_files", {"query": "def divide"})
    assert res == {"query": "def divide", "path": "."}


def test_validate_run_command_valid_and_split():
    res1 = validate_tool_call("run_command", {"command": "python", "args": ["-m", "unittest"]})
    assert res1 == {"command": "python", "args": ["-m", "unittest"]}

    res2 = validate_tool_call("run_command", {"command": "python -m unittest discover -v"})
    assert res2 == {"command": "python", "args": ["-m", "unittest", "discover", "-v"]}

    res3 = validate_tool_call("run_command", {"command": "pytest", "args": ["-q"]})
    assert res3 == {"command": "pytest", "args": ["-q"]}


def test_reject_unknown_tool():
    with pytest.raises(ToolValidationError, match="Unsupported or invalid tool"):
        validate_tool_call("delete_file", {"path": "calculator.py"})

    with pytest.raises(ToolValidationError, match="Unsupported or invalid tool"):
        validate_tool_call("bash", {"command": "ls"})


def test_reject_missing_or_malformed_arguments():
    with pytest.raises(ToolValidationError):
        validate_tool_call("read_file", {})

    with pytest.raises(ToolValidationError):
        validate_tool_call("write_file", {"path": "only_path.py"})

    with pytest.raises(ToolValidationError):
        validate_tool_call("search_files", {"query": "   "})

    with pytest.raises(ToolValidationError):
        validate_tool_call("run_command", {"command": "python", "args": "not-a-list"})

    with pytest.raises(ToolValidationError):
        validate_tool_call("list_files", {"path": ".", "unexpected_field": 123})


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../outside.txt",
        "..\\..\\Windows\\win.ini",
        "subdir/../../secret.env",
        "/etc/passwd",
        "\\Windows\\System32\\cmd.exe",
        "C:\\Windows\\win.ini",
    ],
)
def test_reject_path_traversal_and_absolute_paths(malicious_path: str):
    with pytest.raises(ToolValidationError, match="Security violation"):
        validate_tool_call("read_file", {"path": malicious_path})

    with pytest.raises(ToolValidationError, match="Security violation"):
        validate_tool_call("write_file", {"path": malicious_path, "content": "hack"})

    with pytest.raises(ToolValidationError, match="Security violation"):
        validate_tool_call("list_files", {"path": malicious_path})


@pytest.mark.parametrize(
    "bad_cmd,bad_args",
    [
        ("powershell", ["-Command", "Get-Process"]),
        ("cmd", ["/c", "dir"]),
        ("curl", ["http://example.com"]),
        ("rm", ["-rf", "/"]),
        ("python", ["-m", "unittest; whoami"]),
        ("python && calc.exe", []),
        ("python | more", []),
        ("python", ["-c", "import os; os.system('whoami')"]),
        ("python", ["../outside_script.py"]),
    ],
)
def test_reject_unauthorized_commands_and_shell_injection(bad_cmd: str, bad_args: List[str]):
    with pytest.raises(ToolValidationError, match="Security violation"):
        validate_tool_call("run_command", {"command": bad_cmd, "args": bad_args})


# ---------------------------------------------------------------------------
# 2. Structured LLM Output Parsing Tests
# ---------------------------------------------------------------------------


def test_parse_valid_tool_call_step():
    raw = json.dumps(
        {
            "thought": "Inspecting workspace files first.",
            "action": "tool_call",
            "tool_name": "list_files",
            "tool_args": {"path": "."},
        }
    )
    step = parse_agent_step(raw)
    assert isinstance(step, AgentStep)
    assert step.action == "tool_call"
    assert step.tool_name == "list_files"
    assert step.tool_args == {"path": "."}
    assert step.final_report is None


def test_parse_markdown_fenced_final_answer_step():
    raw = """```json
    {
      "thought": "All tests passed.",
      "action": "final_answer",
      "final_report": {
        "summary": "Created calculator and unit tests.",
        "files_created": ["calculator.py", "test_calculator.py"],
        "tests_run": true,
        "test_results": "Ran 5 tests in 0.002s\\n\\nOK",
        "status": "PASSED"
      }
    }
    ```"""
    step = parse_agent_step(raw)
    assert step.action == "final_answer"
    assert step.final_report is not None
    report_dict = step.final_report.to_report_dict()
    assert report_dict["passed"] is True
    assert report_dict["status"] == "PASSED"
    assert "calculator.py" in report_dict["files_created"]


def test_parse_rejects_invalid_json_or_missing_fields():
    with pytest.raises(ValueError):
        parse_agent_step("This is plain text without any JSON object.")

    with pytest.raises(ValueError):
        parse_agent_step(json.dumps({"thought": "Missing tool name", "action": "tool_call"}))


# ---------------------------------------------------------------------------
# 3. Iteration Limit Enforcement & Agent Loop Tests
# ---------------------------------------------------------------------------


class FakeSequenceLLM:
    """Deterministic fake LLM for unit-testing loop bounds and event sequencing."""

    def __init__(self, steps: List[AgentStep]) -> None:
        self.steps = steps
        self.calls = 0

    def generate_step(
        self, task_prompt: str, history: List[Dict[str, Any]], iteration: int, max_iterations: int
    ) -> AgentStep:
        idx = min(self.calls, len(self.steps) - 1)
        self.calls += 1
        return self.steps[idx]


def _fake_executor(
    tool_name: str,
    arguments: Dict[str, Any],
    task_id: str,
    workspace_id: str,
    iteration: int,
    callback_url=None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id=f"call-{iteration}",
        tool_name=tool_name,
        status="COMPLETED",
        output=f"Executed {tool_name} at iteration {iteration}",
    )


def test_normalize_max_iterations_cap():
    assert normalize_max_iterations(0) == 8
    assert normalize_max_iterations(-5) == 8
    assert normalize_max_iterations(1) == 1
    assert normalize_max_iterations(99) == MAX_ITERATIONS_CAP


def test_strict_max_iterations_one_terminates_with_error():
    infinite_tool_step = AgentStep(
        thought="Still listing files",
        action="tool_call",
        tool_name="list_files",
        tool_args={"path": "."},
    )
    fake_llm = FakeSequenceLLM([infinite_tool_step])
    req = agent_pb2.TaskRequest(
        task_id="task-limit-1",
        workspace_id="ws-1",
        prompt="Do many steps",
        max_iterations=1,
    )

    events = list(run_agent_loop(req, llm_provider=fake_llm, tool_executor=_fake_executor))
    event_types = [e.event_type for e in events]

    assert fake_llm.calls == 1
    assert event_types == ["THOUGHT", "TOOL_CALL", "TOOL_RESULT", "ERROR"]
    assert "Max iterations limit (1) reached" in events[-1].content


def test_loop_completes_cleanly_on_final_answer():
    steps = [
        AgentStep(
            thought="List files in workspace",
            action="tool_call",
            tool_name="list_files",
            tool_args={"path": "."},
        ),
        AgentStep(
            thought="Write calculator.py",
            action="tool_call",
            tool_name="write_file",
            tool_args={"path": "calculator.py", "content": "def add(a, b): return a + b\n"},
        ),
        AgentStep(
            thought="Done!",
            action="final_answer",
            final_report=FinalReport(
                summary="Implemented calculator.py",
                files_created=["calculator.py"],
                tests_run=True,
                test_results="OK",
                status="PASSED",
            ),
        ),
    ]
    fake_llm = FakeSequenceLLM(steps)
    req = agent_pb2.TaskRequest(
        task_id="task-success",
        workspace_id="ws-1",
        prompt="Create calculator",
        max_iterations=5,
    )

    events = list(run_agent_loop(req, llm_provider=fake_llm, tool_executor=_fake_executor))
    event_types = [e.event_type for e in events]

    assert fake_llm.calls == 3
    assert event_types == [
        "THOUGHT",
        "TOOL_CALL",
        "TOOL_RESULT",
        "THOUGHT",
        "TOOL_CALL",
        "TOOL_RESULT",
        "THOUGHT",
        "FINAL_ANSWER",
    ]
    final_evt = events[-1]
    report = json.loads(final_evt.structured_report_json)
    assert report["passed"] is True
    assert report["files_created"] == ["calculator.py"]


def test_grpc_server_health_check_and_execute_task():
    steps = [
        AgentStep(
            thought="Complete immediately",
            action="final_answer",
            final_report=FinalReport(
                summary="Completed via gRPC",
                files_created=[],
                tests_run=False,
                test_results="",
                status="COMPLETED",
            ),
        )
    ]
    fake_llm = FakeSequenceLLM(steps)
    fake_llm.provider = "ollama"
    fake_llm.model = "deepseek-coder:6.7b"
    fake_llm.is_configured = lambda: True

    server = create_grpc_server(port=50059, llm_provider=fake_llm)
    server.start()
    try:
        with grpc.insecure_channel("127.0.0.1:50059") as channel:
            stub = agent_pb2_grpc.AgentRuntimeServiceStub(channel)
            health = stub.HealthCheck(agent_pb2.HealthRequest())
            assert health.status == "healthy"
            assert health.llm_provider == "ollama"
            assert health.llm_configured is True

            stream_events = list(
                stub.ExecuteTask(
                    agent_pb2.TaskRequest(
                        task_id="grpc-test-1",
                        workspace_id="ws-1",
                        prompt="Quick test",
                        max_iterations=3,
                    )
                )
            )
            assert [e.event_type for e in stream_events] == ["THOUGHT", "FINAL_ANSWER"]
    finally:
        server.stop(grace=0)


# ---------------------------------------------------------------------------
# 4. Phase 2 Acceptance: Provider Config, Malformed JSON, Timeout & Recovery
# ---------------------------------------------------------------------------


def test_provider_env_detection_gemini_and_openai(monkeypatch):
    from app.llm.provider import LLMProvider

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    p_gem = LLMProvider()
    assert p_gem.provider == "gemini"
    assert p_gem.api_key == "fake-gemini-key"
    assert p_gem.is_configured() is True

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    p_oai = LLMProvider()
    assert p_oai.provider == "openai"
    assert p_oai.base_url == "https://api.example.com/v1"
    assert p_oai.is_configured() is True


def test_provider_invalid_api_key_and_timeout_emit_graceful_error(monkeypatch):
    import httpx
    from app.llm.provider import LLMProvider

    # 1. Invalid API key (401 Unauthorized)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "invalid-key")
    p_bad_key = LLMProvider()

    def mock_unauthorized(*args, **kwargs):
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        resp = httpx.Response(401, json={"error": {"message": "Incorrect API key"}}, request=req)
        raise httpx.HTTPStatusError("401 Unauthorized", request=req, response=resp)

    monkeypatch.setattr(p_bad_key, "_call_llm", mock_unauthorized)
    task_req = agent_pb2.TaskRequest(
        task_id="task-bad-key", workspace_id="ws-1", prompt="Create calculator", max_iterations=3
    )
    events = list(run_agent_loop(task_req, llm_provider=p_bad_key, tool_executor=_fake_executor))
    assert len(events) == 1
    assert events[0].event_type == "ERROR"
    assert "401" in events[0].content

    # 2. Timeout
    def mock_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(p_bad_key, "_call_llm", mock_timeout)
    events_timeout = list(run_agent_loop(task_req, llm_provider=p_bad_key, tool_executor=_fake_executor))
    assert len(events_timeout) == 1
    assert events_timeout[0].event_type == "ERROR"
    assert "timed out" in events_timeout[0].content


def test_malformed_llm_json_retries_and_emits_error_event(monkeypatch):
    from app.llm.provider import LLMProvider

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    p = LLMProvider()
    calls = {"n": 0}

    def mock_bad_json(sys_p, user_p):
        calls["n"] += 1
        return "not json at all {{{"

    monkeypatch.setattr(p, "_call_llm", mock_bad_json)
    task_req = agent_pb2.TaskRequest(
        task_id="task-bad-json", workspace_id="ws-1", prompt="Create calculator", max_iterations=3
    )
    events = list(run_agent_loop(task_req, llm_provider=p, tool_executor=_fake_executor))
    assert calls["n"] == 2  # initial attempt + 1 retry
    assert len(events) == 1
    assert events[0].event_type == "ERROR"
    assert "failed to produce a valid AgentStep" in events[0].content


def test_malformed_tool_call_recovers_on_next_iteration():
    steps = [
        AgentStep(
            thought="Try invalid tool first",
            action="tool_call",
            tool_name="rm_rf",
            tool_args={"path": "/"},
        ),
        AgentStep(
            thought="Recover by finishing cleanly",
            action="final_answer",
            final_report=FinalReport(
                summary="Recovered after denied tool",
                files_created=[],
                tests_run=False,
                test_results="",
                status="COMPLETED",
            ),
        ),
    ]
    fake_llm = FakeSequenceLLM(steps)
    req = agent_pb2.TaskRequest(
        task_id="task-recover", workspace_id="ws-1", prompt="Test recovery", max_iterations=3
    )
    events = list(run_agent_loop(req, llm_provider=fake_llm, tool_executor=_fake_executor))
    event_types = [e.event_type for e in events]
    assert event_types == ["THOUGHT", "TOOL_CALL", "TOOL_RESULT", "THOUGHT", "FINAL_ANSWER"]
    assert "DENIED / Validation Error" in events[2].tool_output
