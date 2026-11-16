"""Core agent execution loop with strict iteration enforcement and gRPC event streaming."""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.llm.provider import LLMProvider, get_llm_provider
from app.pb import agent_pb2
from app.tools.executor import ToolExecutionResult, execute_tool_via_backend
from app.tools.registry import ToolValidationError, validate_tool_call

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 8
MAX_ITERATIONS_CAP = 12


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def normalize_max_iterations(requested: int) -> int:
    """Enforce strict max_iterations bounds: default 8 if <= 0, capped at 12."""
    if requested is None or requested <= 0:
        return DEFAULT_MAX_ITERATIONS
    return min(int(requested), MAX_ITERATIONS_CAP)


def run_agent_loop(
    request: agent_pb2.TaskRequest,
    llm_provider: Optional[LLMProvider] = None,
    tool_executor: Optional[Callable[..., ToolExecutionResult]] = None,
) -> Iterator[agent_pb2.AgentEvent]:
    """Execute the bounded agent loop, yielding AgentEvent messages for gRPC streaming."""
    task_id = request.task_id or "unknown-task"
    workspace_id = request.workspace_id or "default"
    prompt = (request.prompt or "").strip()
    callback_url = request.tool_callback_url or None
    max_iterations = normalize_max_iterations(request.max_iterations)

    if not prompt:
        yield agent_pb2.AgentEvent(
            task_id=task_id,
            iteration=0,
            event_type="ERROR",
            content="Task prompt must not be empty.",
            timestamp=_utc_now_iso(),
        )
        return

    provider = llm_provider or get_llm_provider()
    executor_fn = tool_executor or execute_tool_via_backend

    history: List[Dict[str, Any]] = []
    files_created_tracker: List[str] = []
    last_test_output: str = ""
    tests_were_run: bool = False

    for iteration in range(1, max_iterations + 1):
        try:
            step = provider.generate_step(
                task_prompt=prompt,
                history=history,
                iteration=iteration,
                max_iterations=max_iterations,
            )
        except Exception as exc:
            logger.error("Task %s iteration %d LLM error: %s", task_id, iteration, exc)
            yield agent_pb2.AgentEvent(
                task_id=task_id,
                iteration=iteration,
                event_type="ERROR",
                content=f"LLM execution error at iteration {iteration}: {exc}",
                timestamp=_utc_now_iso(),
            )
            return

        # 1. Always emit THOUGHT event for the current iteration
        yield agent_pb2.AgentEvent(
            task_id=task_id,
            iteration=iteration,
            event_type="THOUGHT",
            content=step.thought,
            timestamp=_utc_now_iso(),
        )

        # 2. Handle tool_call action
        if step.action == "tool_call":
            raw_tool_name = (step.tool_name or "").strip()
            raw_tool_args = step.tool_args if isinstance(step.tool_args, dict) else {}

            try:
                validated_args = validate_tool_call(raw_tool_name, raw_tool_args, check_security=True)
            except ToolValidationError as val_err:
                args_json_str = json.dumps(raw_tool_args)
                yield agent_pb2.AgentEvent(
                    task_id=task_id,
                    iteration=iteration,
                    event_type="TOOL_CALL",
                    content=f"Invoking {raw_tool_name}",
                    tool_name=raw_tool_name,
                    tool_args_json=args_json_str,
                    timestamp=_utc_now_iso(),
                )
                err_msg = f"DENIED / Validation Error: {val_err}"
                yield agent_pb2.AgentEvent(
                    task_id=task_id,
                    iteration=iteration,
                    event_type="TOOL_RESULT",
                    content=err_msg,
                    tool_name=raw_tool_name,
                    tool_args_json=args_json_str,
                    tool_output=err_msg,
                    timestamp=_utc_now_iso(),
                )
                history.append(
                    {
                        "iteration": iteration,
                        "type": "tool_call",
                        "tool_name": raw_tool_name,
                        "tool_args": raw_tool_args,
                    }
                )
                history.append(
                    {
                        "iteration": iteration,
                        "type": "tool_result",
                        "tool_name": raw_tool_name,
                        "status": "DENIED",
                        "output": err_msg,
                    }
                )
                continue

            args_json_str = json.dumps(validated_args)

            # Emit TOOL_CALL event
            yield agent_pb2.AgentEvent(
                task_id=task_id,
                iteration=iteration,
                event_type="TOOL_CALL",
                content=f"Calling {raw_tool_name} with {args_json_str}",
                tool_name=raw_tool_name,
                tool_args_json=args_json_str,
                timestamp=_utc_now_iso(),
            )

            # Execute via Go backend authorization gate
            result = executor_fn(
                tool_name=raw_tool_name,
                arguments=validated_args,
                task_id=task_id,
                workspace_id=workspace_id,
                iteration=iteration,
                callback_url=callback_url,
            )

            output_text = result.output if result.output else result.error
            if result.status == "DENIED":
                output_text = f"DENIED: {result.error or result.output}"
            elif result.status == "FAILED" and result.error and result.error not in output_text:
                output_text = f"{output_text}\nError: {result.error}".strip()

            if raw_tool_name == "write_file" and result.status == "COMPLETED":
                written_path = validated_args.get("path", "")
                if written_path and written_path not in files_created_tracker:
                    files_created_tracker.append(written_path)

            if raw_tool_name == "run_command":
                tests_were_run = True
                last_test_output = output_text

            # Emit TOOL_RESULT event
            yield agent_pb2.AgentEvent(
                task_id=task_id,
                iteration=iteration,
                event_type="TOOL_RESULT",
                content=output_text,
                tool_name=raw_tool_name,
                tool_args_json=args_json_str,
                tool_output=output_text,
                timestamp=_utc_now_iso(),
            )

            history.append(
                {
                    "iteration": iteration,
                    "type": "tool_call",
                    "tool_name": raw_tool_name,
                    "tool_args": validated_args,
                }
            )
            history.append(
                {
                    "iteration": iteration,
                    "type": "tool_result",
                    "tool_name": raw_tool_name,
                    "status": result.status,
                    "output": output_text,
                }
            )
            continue

        # 3. Handle final_answer action
        if step.action == "final_answer" and step.final_report is not None:
            report = step.final_report
            if not report.files_created and files_created_tracker:
                report.files_created = list(files_created_tracker)
            if not report.test_results and last_test_output:
                report.test_results = last_test_output
            if tests_were_run and not report.tests_run:
                report.tests_run = True

            report_dict = report.to_report_dict()
            report_json_str = json.dumps(report_dict)

            yield agent_pb2.AgentEvent(
                task_id=task_id,
                iteration=iteration,
                event_type="FINAL_ANSWER",
                content=report.summary,
                structured_report_json=report_json_str,
                timestamp=_utc_now_iso(),
            )
            return

    # 4. If loop finishes without returning a final_answer, max_iterations was reached
    yield agent_pb2.AgentEvent(
        task_id=task_id,
        iteration=max_iterations,
        event_type="ERROR",
        content=f"Max iterations limit ({max_iterations}) reached without completing the task.",
        timestamp=_utc_now_iso(),
    )
