"""Pydantic schemas and parser for structured LLM agent steps."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator

KNOWN_TOOLS = {"list_files", "read_file", "write_file", "search_files", "run_command"}


class FinalReport(BaseModel):
    """Structured completion report emitted on FINAL_ANSWER."""

    summary: str = Field(..., min_length=1, description="Human-readable summary of work done")
    files_created: List[str] = Field(default_factory=list, description="List of files created or modified")
    tests_run: Union[bool, str, List[str]] = Field(default=True, description="Whether unit tests were executed")
    test_results: str = Field(default="", description="Output or summary of test execution")
    status: str = Field(default="PASSED", description="Completion status: PASSED, COMPLETED, or FAILED")
    tests_executed: str = Field(default="", description="Command or description of tests executed")
    passed: bool = Field(default=True, description="Boolean indicator whether tests passed")
    details: str = Field(default="", description="Detailed explanation of implementation and verification")

    @model_validator(mode="after")
    def sync_fields(self) -> "FinalReport":
        status_upper = (self.status or "PASSED").upper()
        if status_upper in ("FAILED", "ERROR", "FAIL"):
            self.passed = False
        if not self.tests_executed:
            if isinstance(self.tests_run, str) and self.tests_run:
                self.tests_executed = self.tests_run
            elif isinstance(self.tests_run, list) and self.tests_run:
                self.tests_executed = ", ".join(str(x) for x in self.tests_run)
            elif self.test_results:
                self.tests_executed = self.test_results
            else:
                self.tests_executed = "python -m unittest"
        if not self.test_results and self.details:
            self.test_results = self.details
        if not self.details:
            self.details = self.test_results or self.summary
        return self

    def to_report_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "files_created": list(self.files_created),
            "tests_run": self.tests_run,
            "tests_executed": self.tests_executed,
            "test_results": self.test_results,
            "passed": self.passed,
            "status": self.status,
            "details": self.details,
        }


class AgentStep(BaseModel):
    """Structured step returned by the LLM on each agent loop iteration."""

    thought: str = Field(..., min_length=1, description="Reasoning for the current step")
    action: Literal["tool_call", "final_answer"] = Field(
        ..., description="Next action: 'tool_call' or 'final_answer'"
    )
    tool_name: Optional[str] = Field(default=None, description="Tool to invoke when action == 'tool_call'")
    tool_args: Optional[Dict[str, Any]] = Field(
        default=None, description="Arguments for the tool when action == 'tool_call'"
    )
    final_report: Optional[FinalReport] = Field(
        default=None, description="Structured completion report when action == 'final_answer'"
    )

    @model_validator(mode="after")
    def validate_action_payload(self) -> "AgentStep":
        if self.action == "tool_call":
            if not self.tool_name or not str(self.tool_name).strip():
                raise ValueError("tool_name is required when action is 'tool_call'")
            if self.tool_args is None:
                self.tool_args = {}
            if not isinstance(self.tool_args, dict):
                raise ValueError("tool_args must be a JSON object/dictionary")
        elif self.action == "final_answer":
            if self.final_report is None:
                raise ValueError("final_report is required when action is 'final_answer'")
        return self


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    """Extract and parse a JSON object from raw LLM text output."""
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty LLM response")

    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in LLM output: {exc}") from exc

    raise ValueError("No valid JSON object found in LLM output")


def parse_agent_step(raw_text: str) -> AgentStep:
    """Parse and normalize raw LLM JSON string into a validated AgentStep."""
    data = _extract_json_object(raw_text)

    if "thought" not in data or not data.get("thought"):
        for alias in ("reasoning", "rationale", "thinking", "explanation"):
            if alias in data and isinstance(data[alias], str) and data[alias].strip():
                data["thought"] = data[alias].strip()
                break

    if not data.get("tool_name") and isinstance(data.get("tool"), str) and data["tool"].strip():
        data["tool_name"] = data["tool"].strip()

    action = data.get("action")
    if isinstance(action, str):
        action_lower = action.strip().lower()
        if action_lower in ("tool", "call_tool", "use_tool", "tool_call"):
            data["action"] = "tool_call"
        elif action_lower in ("final", "finish", "complete", "done", "final_answer", "answer"):
            data["action"] = "final_answer"
        elif action_lower in KNOWN_TOOLS:
            if not data.get("tool_name"):
                data["tool_name"] = action_lower
            data["action"] = "tool_call"

    # If the LLM populated a known tool_name and did not provide final_report, treat as tool_call
    tool_name_val = str(data.get("tool_name") or "").strip()
    if tool_name_val in KNOWN_TOOLS and not data.get("final_report"):
        data["action"] = "tool_call"
        data["tool_name"] = tool_name_val

    if data.get("action") == "tool_call":
        if "tool_args" not in data or data.get("tool_args") is None:
            for alias in ("arguments", "args", "parameters", "params", "tool_input"):
                if alias in data and isinstance(data[alias], dict):
                    data["tool_args"] = data[alias]
                    break
        if isinstance(data.get("tool_args"), str):
            try:
                parsed_args = json.loads(data["tool_args"])
                if isinstance(parsed_args, dict):
                    data["tool_args"] = parsed_args
            except json.JSONDecodeError:
                pass
        if isinstance(data.get("tool_args"), dict):
            targs = dict(data["tool_args"])
            tname = data.get("tool_name")
            if tname in ("list_files", "read_file", "write_file", "search_files") and "path" not in targs:
                for p_alias in ("directory", "dir", "file_path", "filepath", "filename", "file"):
                    if p_alias in targs and isinstance(targs[p_alias], str):
                        targs["path"] = targs.pop(p_alias)
                        break
            if tname == "list_files" and not targs.get("path"):
                targs["path"] = "."
            data["tool_args"] = targs

    if data.get("action") == "final_answer" and not data.get("final_report"):
        summary = data.get("summary") or data.get("final_answer") or data.get("content") or data.get("thought")
        if isinstance(summary, str) and summary.strip():
            data["final_report"] = {
                "summary": summary.strip(),
                "files_created": data.get("files_created", []),
                "tests_run": data.get("tests_run", True),
                "test_results": data.get("test_results", ""),
                "status": data.get("status", "PASSED"),
                "tests_executed": data.get("tests_executed", ""),
                "passed": data.get("passed", True),
                "details": data.get("details", ""),
            }

    if not data.get("thought") and data.get("action") in ("tool_call", "final_answer"):
        if data.get("action") == "tool_call" and data.get("tool_name"):
            data["thought"] = f"Invoking tool {data['tool_name']}"
        elif data.get("action") == "final_answer" and isinstance(data.get("final_report"), dict):
            data["thought"] = data["final_report"].get("summary", "Completing task with final report.")

    return AgentStep.model_validate(data)
