"""Tool definitions, Pydantic parameter schemas, and security validators."""
from __future__ import annotations

import os
import re
import shlex
from typing import Any, Dict, List, Set, Type
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

APPROVED_TOOLS: Set[str] = {
    "list_files",
    "read_file",
    "write_file",
    "search_files",
    "run_command",
}

ALLOWED_COMMANDS: Set[str] = {
    "python",
    "python3",
    "pytest",
    "go",
    "npm",
}

SHELL_INJECTION_PATTERNS = re.compile(r"[;&|`><$()]|\r|\n")


class ToolValidationError(ValueError):
    """Raised when a tool name, parameter schema, or security constraint fails validation."""


def _is_safe_relative_path(path_value: str) -> bool:
    """Return True only if path_value is a relative path that does not traverse outside root."""
    if not isinstance(path_value, str) or not path_value.strip():
        return False
    cleaned = path_value.strip()
    # Reject drive letters or UNC paths or leading slashes
    if re.match(r"^[a-zA-Z]:", cleaned) or cleaned.startswith("/") or cleaned.startswith("\\"):
        return False
    normalized = cleaned.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return False
    norm_os = os.path.normpath(cleaned)
    if norm_os.startswith("..") or os.path.isabs(norm_os):
        return False
    return True


class ListFilesArgs(BaseModel):
    """Schema for list_files(path: str = '.')."""

    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(default=".", description="Relative directory path inside workspace")

    @field_validator("path", mode="before")
    @classmethod
    def normalize_empty_path(cls, val: Any) -> Any:
        if val == "":
            return "."
        return val


class ReadFileArgs(BaseModel):
    """Schema for read_file(path: str)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(..., min_length=1, description="Relative file path inside workspace")

    @field_validator("path")
    @classmethod
    def check_non_whitespace(cls, val: str) -> str:
        if not val.strip():
            raise ValueError("path must not be empty or whitespace")
        return val.strip()


class WriteFileArgs(BaseModel):
    """Schema for write_file(path: str, content: str)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(..., min_length=1, description="Relative file path inside workspace")
    content: str = Field(..., description="Content to write to the file")

    @field_validator("path")
    @classmethod
    def check_non_whitespace(cls, val: str) -> str:
        if not val.strip():
            raise ValueError("path must not be empty or whitespace")
        return val.strip()


class SearchFilesArgs(BaseModel):
    """Schema for search_files(query: str, path: str = '.')."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(..., min_length=1, description="Search query string")
    path: str = Field(default=".", description="Optional relative path inside workspace")

    @field_validator("query")
    @classmethod
    def check_query(cls, val: str) -> str:
        if not val.strip():
            raise ValueError("query must not be empty")
        return val

    @field_validator("path", mode="before")
    @classmethod
    def normalize_empty_path(cls, val: Any) -> Any:
        if val == "" or val is None:
            return "."
        return val


class RunCommandArgs(BaseModel):
    """Schema for run_command(command: str, args: list[str] = [])."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1, description="Executable command")
    args: List[str] = Field(default_factory=list, description="Command arguments")

    @field_validator("command")
    @classmethod
    def check_command(cls, val: Any) -> str:
        if not isinstance(val, str) or not val.strip():
            raise ValueError("command must be a non-empty string")
        return val.strip()

    @field_validator("args", mode="before")
    @classmethod
    def check_args(cls, val: Any) -> List[str]:
        if val is None:
            return []
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise ValueError("args must be a list of strings")
        return val


TOOL_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "list_files": ListFilesArgs,
    "read_file": ReadFileArgs,
    "write_file": WriteFileArgs,
    "search_files": SearchFilesArgs,
    "run_command": RunCommandArgs,
}


def validate_tool_security(tool_name: str, validated_args: Dict[str, Any]) -> None:
    """Enforce path sandboxing and command allowlist rules on validated tool arguments."""
    if tool_name in ("list_files", "read_file", "write_file", "search_files"):
        path_val = validated_args.get("path", ".")
        if not _is_safe_relative_path(path_val):
            raise ToolValidationError(
                f"Security violation: path '{path_val}' traverses outside workspace root or is absolute"
            )

    if tool_name == "run_command":
        raw_cmd = validated_args.get("command", "")
        raw_args: List[str] = list(validated_args.get("args", []))

        # Reject shell metacharacters / command chaining in command or args
        if SHELL_INJECTION_PATTERNS.search(raw_cmd):
            raise ToolValidationError(
                f"Security violation: shell metacharacters or chaining forbidden in command '{raw_cmd}'"
            )
        for arg in raw_args:
            if SHELL_INJECTION_PATTERNS.search(arg):
                raise ToolValidationError(
                    f"Security violation: shell metacharacters forbidden in argument '{arg}'"
                )
            if ".." in arg.replace("\\", "/").split("/"):
                raise ToolValidationError(
                    f"Security violation: path traversal forbidden in command argument '{arg}'"
                )

        # If command contains space-separated args (e.g. 'python -m unittest'), split cleanly
        parts = shlex.split(raw_cmd, posix=False) if " " in raw_cmd else [raw_cmd]
        if not parts:
            raise ToolValidationError("Empty command")
        exe = parts[0].lower()
        if exe.endswith(".exe"):
            exe = exe[:-4]
        combined_args = parts[1:] + raw_args

        if exe not in ALLOWED_COMMANDS:
            raise ToolValidationError(
                f"Security violation: unauthorized command '{exe}'. Allowed commands: {sorted(ALLOWED_COMMANDS)}"
            )
        if exe == "go" and (not combined_args or combined_args[0] != "test"):
            raise ToolValidationError("Security violation: only 'go test' is allowed for 'go'")
        if exe == "npm" and (not combined_args or combined_args[0] != "test"):
            raise ToolValidationError("Security violation: only 'npm test' is allowed for 'npm'")

        for arg in combined_args:
            if arg in ("-c", "/c", "--command"):
                raise ToolValidationError(f"Security violation: inline code execution flag '{arg}' is not allowed")

        validated_args["command"] = exe
        validated_args["args"] = combined_args


def validate_tool_call(
    tool_name: str,
    tool_args: Any,
    check_security: bool = True,
) -> Dict[str, Any]:
    """Validate tool name and arguments against Pydantic schemas (and optionally security rules).

    Returns normalized dictionary of validated arguments.
    Raises ToolValidationError on any schema or security violation.
    """
    if not isinstance(tool_name, str) or tool_name not in APPROVED_TOOLS:
        raise ToolValidationError(
            f"Unsupported or invalid tool '{tool_name}'. Approved tools: {sorted(APPROVED_TOOLS)}"
        )

    if tool_args is None:
        tool_args = {}
    if not isinstance(tool_args, dict):
        raise ToolValidationError(f"Tool arguments for '{tool_name}' must be a JSON object/dict")

    schema_cls = TOOL_SCHEMAS[tool_name]
    try:
        model_instance = schema_cls.model_validate(tool_args)
    except ValidationError as exc:
        raise ToolValidationError(f"Invalid arguments for tool '{tool_name}': {exc}") from exc

    validated_dict = model_instance.model_dump()

    if check_security:
        validate_tool_security(tool_name, validated_dict)
    elif tool_name == "run_command":
        # Still normalize 'python -m unittest' into command='python', args=['-m', 'unittest'] if safe
        raw_cmd = validated_dict.get("command", "")
        raw_args = list(validated_dict.get("args", []))
        if " " in raw_cmd and not SHELL_INJECTION_PATTERNS.search(raw_cmd) and not raw_args:
            parts = shlex.split(raw_cmd, posix=False)
            if parts and parts[0].lower() in ALLOWED_COMMANDS:
                validated_dict["command"] = parts[0]
                validated_dict["args"] = parts[1:]

    return validated_dict
