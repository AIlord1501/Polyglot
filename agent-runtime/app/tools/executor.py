"""Tool execution delegation to the Go Backend tool authorization gate."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CALLBACK_URL = "http://127.0.0.1:8080/internal/v1/tools/execute"


class ToolExecutionResult(BaseModel):
    """Structured result returned from the Go Backend tool execution endpoint."""

    call_id: str
    tool_name: str
    status: str  # "COMPLETED", "DENIED", "FAILED"
    output: str = ""
    error: str = ""


def execute_tool_via_backend(
    tool_name: str,
    arguments: Dict[str, Any],
    task_id: str,
    workspace_id: str,
    iteration: int,
    callback_url: Optional[str] = None,
    call_id: Optional[str] = None,
    timeout: float = 30.0,
) -> ToolExecutionResult:
    """Delegate tool authorization and sandboxed execution to the Go Backend."""
    url = (callback_url or DEFAULT_CALLBACK_URL).strip()
    cid = call_id or str(uuid.uuid4())

    payload = {
        "task_id": task_id,
        "workspace_id": workspace_id,
        "call_id": cid,
        "iteration": iteration,
        "tool_name": tool_name,
        "arguments": arguments,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {}

            if resp.status_code == 403 or data.get("status") == "DENIED":
                err_msg = data.get("error") or data.get("output") or f"HTTP {resp.status_code}: Tool call denied"
                return ToolExecutionResult(
                    call_id=data.get("call_id", cid),
                    tool_name=tool_name,
                    status="DENIED",
                    output=data.get("output", ""),
                    error=err_msg,
                )

            if resp.status_code >= 400 and not data.get("status"):
                err_msg = data.get("error") or resp.text or f"HTTP {resp.status_code}"
                return ToolExecutionResult(
                    call_id=cid,
                    tool_name=tool_name,
                    status="FAILED",
                    output=data.get("output", ""),
                    error=err_msg,
                )

            status = data.get("status", "COMPLETED")
            output = data.get("output", "")
            error = data.get("error", "")
            return ToolExecutionResult(
                call_id=data.get("call_id", cid),
                tool_name=tool_name,
                status=status,
                output=output if isinstance(output, str) else str(output),
                error=error if isinstance(error, str) else str(error),
            )
    except Exception as exc:
        logger.error("Error calling Go backend tool gate (%s): %s", url, exc)
        return ToolExecutionResult(
            call_id=cid,
            tool_name=tool_name,
            status="FAILED",
            output="",
            error=f"Tool callback request failed: {exc}",
        )
