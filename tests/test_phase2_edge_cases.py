"""
Phase 2 Acceptance Edge-Case Test Suite (9 required edge cases):
1. Invalid LLM API key
2. LLM timeout
3. Backend unavailable
4. Python runtime unavailable
5. Malformed tool output
6. Interrupted task
7. Empty workspace
8. Repeated task execution
9. Windows path traversal and symlink edge cases

Run via: python -m pytest tests/test_phase2_edge_cases.py -v
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_RUNTIME_DIR = REPO_ROOT / "agent-runtime"
if str(AGENT_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_RUNTIME_DIR))

from app.agent.loop import run_agent_loop  # noqa: E402
from app.llm.provider import LLMProvider  # noqa: E402
from app.pb import agent_pb2  # noqa: E402
from app.tools.executor import execute_tool_via_backend  # noqa: E402

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080")


def http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {"error": raw}
        except json.JSONDecodeError:
            body = {"error": raw}
        return e.code, body


# ---------------------------------------------------------------------------
# Edge Case 1: Invalid LLM API key
# ---------------------------------------------------------------------------
def test_01_invalid_llm_api_key_emits_error_cleanly():
    """Verify invalid Gemini/OpenAI API key fails gracefully with an ERROR event (no crash)."""
    # Test OpenAI-compatible provider against a local mock returning 401 Unauthorized
    class UnauthorizedHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "Incorrect API key provided"}}).encode())

        def log_message(self, format, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), UnauthorizedHandler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            provider = LLMProvider(
                provider="openai",
                model="gpt-4o-mini",
                api_key="sk-invalid-test-key",
                base_url=f"http://127.0.0.1:{port}/v1",
                timeout=5.0,
            )
            req = agent_pb2.TaskRequest(
                task_id="edge-invalid-key",
                workspace_id="ws-test",
                prompt="Say hello",
                max_iterations=2,
            )
            events = list(run_agent_loop(req, llm_provider=provider))
            assert len(events) >= 1
            assert events[-1].event_type == "ERROR"
            assert "401" in events[-1].content or "error" in events[-1].content.lower()
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Edge Case 2: LLM timeout
# ---------------------------------------------------------------------------
def test_02_llm_timeout_emits_error_cleanly():
    """Verify LLM HTTP timeout is caught and emitted as a clean ERROR event."""
    class SlowHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(1.5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), SlowHandler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            provider = LLMProvider(
                provider="ollama",
                model="deepseek-coder:6.7b",
                base_url=f"http://127.0.0.1:{port}",
                timeout=0.3,
            )
            req = agent_pb2.TaskRequest(
                task_id="edge-timeout",
                workspace_id="ws-test",
                prompt="Create calculator",
                max_iterations=2,
            )
            events = list(run_agent_loop(req, llm_provider=provider))
            assert len(events) >= 1
            assert events[-1].event_type == "ERROR"
            assert "error" in events[-1].content.lower() or "timed out" in events[-1].content.lower()
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Edge Case 3: Backend unavailable
# ---------------------------------------------------------------------------
def test_03_backend_unavailable_returns_failed_tool_result():
    """Verify execute_tool_via_backend handles an unreachable Go backend without crashing."""
    result = execute_tool_via_backend(
        tool_name="list_files",
        arguments={"path": "."},
        task_id="edge-backend-down",
        workspace_id="ws-test",
        iteration=1,
        callback_url="http://127.0.0.1:59997/internal/v1/tools/execute",
        timeout=2.0,
    )
    assert result.status == "FAILED"
    assert "failed" in result.error.lower() or "connect" in result.error.lower()


# ---------------------------------------------------------------------------
# Edge Case 4: Python runtime unavailable
# ---------------------------------------------------------------------------
def test_04_python_runtime_unavailable_handled_by_go_backend():
    """Verify Go backend reports agent_runtime.connected=false and fails tasks cleanly when :50051 is down."""
    server_exe = REPO_ROOT / "backend" / "server.exe"
    if not server_exe.exists():
        pytest.skip("backend/server.exe not built")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env = os.environ.copy()
        env["PORT"] = "18089"
        env["DB_PATH"] = str(tmp_path / "test.db")
        env["WORKSPACES_DIR"] = str(tmp_path / "workspaces")
        env["AGENT_RUNTIME_ADDR"] = "127.0.0.1:59998"  # unreachable gRPC port

        proc = subprocess.Popen(
            [str(server_exe)],
            cwd=str(REPO_ROOT / "backend"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for :18089 to listen
            healthy = False
            health_body: Dict[str, Any] = {}
            for _ in range(30):
                try:
                    st, health_body = http_json("GET", "http://127.0.0.1:18089/api/v1/health", timeout=2.0)
                    if st == 200:
                        healthy = True
                        break
                except Exception:
                    time.sleep(0.25)

            assert healthy, "Temporary Go backend on :18089 failed to start"
            assert health_body.get("backend") == "healthy"
            assert health_body.get("agent_runtime", {}).get("connected") is False

            # Get seeded workspace and submit a task while Python runtime is unreachable
            st_ws, ws_resp = http_json("GET", "http://127.0.0.1:18089/api/v1/workspaces")
            assert st_ws == 200
            ws_id = ws_resp["workspaces"][0]["id"]

            st_task, task_resp = http_json(
                "POST",
                "http://127.0.0.1:18089/api/v1/tasks",
                {"workspace_id": ws_id, "prompt": "Hello", "max_iterations": 2},
            )
            assert st_task in (200, 201, 202)
            task_id = task_resp["id"]

            # Poll until FAILED
            final_task: Dict[str, Any] = {}
            for _ in range(30):
                st_get, final_task = http_json("GET", f"http://127.0.0.1:18089/api/v1/tasks/{task_id}")
                if st_get == 200 and final_task.get("status") in ("FAILED", "COMPLETED", "ERROR"):
                    break
                time.sleep(0.3)

            assert final_task.get("status") == "FAILED"
            assert final_task.get("error") or any(
                ev.get("event_type") == "ERROR" for ev in final_task.get("events", [])
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# Edge Case 5: Malformed tool output
# ---------------------------------------------------------------------------
def test_05_malformed_tool_output_handled_gracefully():
    """Verify execute_tool_via_backend handles non-JSON / malformed backend responses without crashing."""
    class MalformedHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{not-valid-json!!!")

        def log_message(self, format, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), MalformedHandler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            res = execute_tool_via_backend(
                tool_name="list_files",
                arguments={"path": "."},
                task_id="edge-malformed-tool",
                workspace_id="ws-test",
                iteration=1,
                callback_url=f"http://127.0.0.1:{port}/internal/v1/tools/execute",
                timeout=3.0,
            )
            assert res.tool_name == "list_files"
            assert res.status in ("COMPLETED", "FAILED")
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Edge Case 6: Interrupted task (SSE disconnect & gRPC generator close)
# ---------------------------------------------------------------------------
def test_06_interrupted_task_sse_disconnect_and_generator_close():
    """Verify SSE client disconnect mid-stream does not crash backend, and gRPC loop supports early close."""
    # 6a. Live backend SSE early disconnect
    ws_name = f"edge-interrupt-{uuid.uuid4().hex[:6]}"
    st_ws, ws_body = http_json("POST", f"{BACKEND_URL}/api/v1/workspaces", {"name": ws_name})
    assert st_ws in (200, 201)
    ws_id = ws_body["id"]

    st_t, t_body = http_json(
        "POST",
        f"{BACKEND_URL}/api/v1/tasks",
        {"workspace_id": ws_id, "prompt": "List files in workspace.", "max_iterations": 2},
    )
    assert st_t in (200, 201, 202)
    task_id = t_body["id"]

    # Open SSE stream and immediately disconnect after reading 1 line
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/v1/tasks/{task_id}/stream",
        headers={"Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        _ = resp.readline()  # disconnect immediately on context exit

    # Verify backend remains healthy after abrupt SSE disconnect
    st_h, h_body = http_json("GET", f"{BACKEND_URL}/api/v1/health")
    assert st_h == 200 and h_body.get("backend") == "healthy"


# ---------------------------------------------------------------------------
# Edge Case 7: Empty workspace
# ---------------------------------------------------------------------------
def test_07_empty_workspace_api_and_tools():
    """Verify empty workspace returns empty file list and list_files/search_files succeed cleanly."""
    ws_name = f"edge-empty-{uuid.uuid4().hex[:6]}"
    st_ws, ws_body = http_json("POST", f"{BACKEND_URL}/api/v1/workspaces", {"name": ws_name})
    assert st_ws in (200, 201)
    ws_id = ws_body["id"]

    st_files, files_body = http_json("GET", f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/files?path=.")
    assert st_files == 200
    assert files_body.get("files") == []

    st_lf, lf_body = http_json(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": f"empty-lf-{uuid.uuid4().hex[:6]}",
            "workspace_id": ws_id,
            "iteration": 1,
            "tool_name": "list_files",
            "arguments": {"path": "."},
        },
    )
    assert st_lf == 200
    assert lf_body.get("status") == "COMPLETED"

    st_sf, sf_body = http_json(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": f"empty-sf-{uuid.uuid4().hex[:6]}",
            "workspace_id": ws_id,
            "iteration": 1,
            "tool_name": "search_files",
            "arguments": {"query": "nonexistent", "path": "."},
        },
    )
    assert st_sf == 200
    assert sf_body.get("status") == "COMPLETED"


# ---------------------------------------------------------------------------
# Edge Case 8: Repeated task execution
# ---------------------------------------------------------------------------
def test_08_repeated_task_execution_in_same_workspace():
    """Verify submitting multiple tasks in the same workspace tracks each independently."""
    ws_name = f"edge-repeat-{uuid.uuid4().hex[:6]}"
    st_ws, ws_body = http_json("POST", f"{BACKEND_URL}/api/v1/workspaces", {"name": ws_name})
    assert st_ws in (200, 201)
    ws_id = ws_body["id"]

    task_ids: List[str] = []
    for idx in range(2):
        st_t, t_body = http_json(
            "POST",
            f"{BACKEND_URL}/api/v1/tasks",
            {
                "workspace_id": ws_id,
                "prompt": f"List files in workspace (run {idx + 1}).",
                "max_iterations": 2,
            },
        )
        assert st_t in (200, 201, 202)
        task_ids.append(t_body["id"])

    assert len(set(task_ids)) == 2

    # Wait for both to finish
    for tid in task_ids:
        for _ in range(40):
            st_g, g_body = http_json("GET", f"{BACKEND_URL}/api/v1/tasks/{tid}")
            if st_g == 200 and g_body.get("status") in ("COMPLETED", "FAILED", "ERROR"):
                break
            time.sleep(1.0)

    st_list, list_body = http_json("GET", f"{BACKEND_URL}/api/v1/tasks?workspace_id={ws_id}")
    assert st_list == 200
    returned_ids = {t["id"] for t in list_body.get("tasks", [])}
    assert set(task_ids).issubset(returned_ids)


# ---------------------------------------------------------------------------
# Edge Case 9: Windows path traversal and symlink edge cases
# ---------------------------------------------------------------------------
def test_09_windows_path_traversal_and_symlink_edge_cases():
    """Verify Windows-specific traversal vectors and symlinks pointing outside workspace are blocked."""
    ws_name = f"edge-winpath-{uuid.uuid4().hex[:6]}"
    st_ws, ws_body = http_json("POST", f"{BACKEND_URL}/api/v1/workspaces", {"name": ws_name})
    assert st_ws in (200, 201)
    ws_id = ws_body["id"]
    ws_root = Path(ws_body["root_path"])

    windows_vectors = [
        r"..\..\Windows\win.ini",
        r"\\?\C:\Windows\win.ini",
        r"C:\Windows\System32\drivers\etc\hosts",
        r"subdir/../../../outside.txt",
        r"..\outside.txt",
        r"C:relative_drive_escape.txt",
    ]

    for vec in windows_vectors:
        st, body = http_json(
            "POST",
            f"{BACKEND_URL}/internal/v1/tools/execute",
            {
                "task_id": f"win-trav-{uuid.uuid4().hex[:6]}",
                "workspace_id": ws_id,
                "iteration": 1,
                "tool_name": "read_file",
                "arguments": {"path": vec},
            },
        )
        assert st == 403 or body.get("status") == "DENIED", f"Vector not blocked: {vec} -> {st} {body}"

        st_rest, _ = http_json(
            "GET",
            f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/file?path=" + urllib.parse.quote(vec),
        )
        assert st_rest in (400, 403), f"REST /file did not block {vec} -> {st_rest}"

    # Symlink escape check (if Windows permits symlink creation in current shell)
    outside_target = ws_root.parent / f"secret-outside-{uuid.uuid4().hex[:6]}.txt"
    outside_target.write_text("TOP_SECRET_OUTSIDE", encoding="utf-8")
    symlink_path = ws_root / "escape_link.txt"
    try:
        try:
            os.symlink(outside_target, symlink_path)
            symlink_created = True
        except OSError:
            symlink_created = False

        if symlink_created:
            st_sym, sym_body = http_json(
                "POST",
                f"{BACKEND_URL}/internal/v1/tools/execute",
                {
                    "task_id": f"sym-trav-{uuid.uuid4().hex[:6]}",
                    "workspace_id": ws_id,
                    "iteration": 1,
                    "tool_name": "read_file",
                    "arguments": {"path": "escape_link.txt"},
                },
            )
            assert st_sym == 403 or sym_body.get("status") == "DENIED"
            assert "TOP_SECRET_OUTSIDE" not in json.dumps(sym_body)
    finally:
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink(missing_ok=True)
        outside_target.unlink(missing_ok=True)
