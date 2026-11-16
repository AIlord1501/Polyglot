"""LLM Provider Abstraction supporting Ollama, Gemini, and OpenAI-compatible APIs."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx

from .schemas import AgentStep, parse_agent_step

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an autonomous software engineering agent operating inside a sandboxed workspace.
You must respond ONLY with a single valid JSON object on every turn. Do not include markdown fences or extra prose outside the JSON object.

Approved tools:
1. list_files: {"path": "."}
2. read_file: {"path": "<relative_path>"}
3. write_file: {"path": "<relative_path>", "content": "<file_content>"}
4. search_files: {"query": "<search_string>", "path": "."}
5. run_command: {"command": "python", "args": ["-m", "unittest", "discover", "-v"]}

Required JSON schema when calling a tool:
{
  "thought": "Brief explanation of why you are calling this tool",
  "action": "tool_call",
  "tool_name": "list_files | read_file | write_file | search_files | run_command",
  "tool_args": { ... }
}

Required JSON schema when finishing the task (ONLY after all required files are created and tests are executed and verified):
{
  "thought": "All files have been created and unit tests executed and passed.",
  "action": "final_answer",
  "final_report": {
    "summary": "Summary of what was built and verified",
    "files_created": ["calculator.py", "test_calculator.py"],
    "tests_run": true,
    "tests_executed": "python -m unittest discover -v",
    "test_results": "Output from unit test run showing all tests passed",
    "passed": true,
    "status": "PASSED",
    "details": "Detailed explanation of implementation and unit test results"
  }
}

Standard workflow for coding tasks:
Step 1: Call list_files with {"path": "."} to inspect the workspace.
Step 2: Call write_file to create the source code module (e.g., calculator.py with add, subtract, multiply, divide).
Step 3: Call write_file to create the unit test module (e.g., test_calculator.py using unittest.TestCase).
Step 4: Call run_command with {"command": "python", "args": ["-m", "unittest", "discover", "-v"]} to run the unit tests.
Step 5: Inspect the test output, fix any issues if needed, or return action="final_answer" with final_report once tests pass.
"""


def _build_workflow_guidance(task_prompt: str, history: List[Dict[str, Any]]) -> str:
    """Build concise state summary from tool execution history to keep the LLM focused."""
    called_tools = [h.get("tool_name") for h in history if h.get("type") == "tool_call"]
    written_files = [
        h.get("tool_args", {}).get("path", "")
        for h in history
        if h.get("type") == "tool_call" and h.get("tool_name") == "write_file"
    ]
    ran_commands = [
        h for h in history if h.get("type") == "tool_result" and h.get("tool_name") == "run_command"
    ]

    lines = [f"User Task Prompt: {task_prompt}", ""]
    if history:
        lines.append("Execution History so far:")
        for item in history[-8:]:
            itype = item.get("type")
            if itype == "tool_call":
                lines.append(
                    f"- Step {item.get('iteration')}: Called {item.get('tool_name')} with args {json.dumps(item.get('tool_args', {}))}"
                )
            elif itype == "tool_result":
                status = item.get("status", "COMPLETED")
                out = str(item.get("output", ""))
                if len(out) > 600:
                    out = out[:600] + "...(truncated)"
                lines.append(
                    f"- Step {item.get('iteration')} Result ({item.get('tool_name')} -> {status}): {out}"
                )
        lines.append("")

    # Provide clear structured guidance on the current phase of the workflow
    has_list = "list_files" in called_tools
    has_source = any(
        f and not os.path.basename(f).startswith("test_") and f.endswith(".py")
        for f in written_files
    )
    has_tests = any(
        f and ("test" in os.path.basename(f).lower()) and f.endswith(".py")
        for f in written_files
    )
    has_test_run = len(ran_commands) > 0
    last_test_output = ran_commands[-1].get("output", "") if ran_commands else ""
    last_test_status = ran_commands[-1].get("status", "") if ran_commands else ""

    if not has_list and len(history) == 0:
        lines.append(
            'Current Phase: Inspect the workspace first. Respond with JSON: {"thought": "Inspecting the workspace directory to see existing files.", "action": "tool_call", "tool_name": "list_files", "tool_args": {"path": "."}}'
        )
    elif not has_source and "calculator" in task_prompt.lower():
        lines.append(
            "Current Phase: Workspace inspected. Now create `calculator.py` using `write_file` with `add(a, b)`, `subtract(a, b)`, `multiply(a, b)`, and `divide(a, b)` (raise ValueError on division by zero)."
        )
    elif has_source and not has_tests and "calculator" in task_prompt.lower():
        lines.append(
            "Current Phase: `calculator.py` is created. Now create `test_calculator.py` using `write_file` with `unittest.TestCase` testing `add`, `subtract`, `multiply`, `divide`, and division by zero."
        )
    elif has_source and has_tests and not has_test_run:
        lines.append(
            'Current Phase: Source and unit test files are created. Now execute the unit tests using `run_command` with `{"command": "python", "args": ["-m", "unittest", "discover", "-v"]}`.'
        )
    elif has_test_run and last_test_status == "COMPLETED" and ("OK" in last_test_output or "passed" in last_test_output.lower()):
        lines.append(
            f"Current Phase: Unit tests executed and passed! Files created: {json.dumps(written_files)}. Test output: {json.dumps(last_test_output.strip())}. Now return `action`: `\"final_answer\"` with `final_report` containing `summary`, `files_created`: {json.dumps(written_files)}, `tests_run`: true, `tests_executed`: \"python -m unittest discover -v\", `test_results`: {json.dumps(last_test_output.strip())}, `passed`: true, `status`: \"PASSED\"."
        )
    else:
        lines.append("Respond with the next required JSON step (`tool_call` or `final_answer`).")

    return "\n".join(lines)


class LLMProvider:
    """Real LLM Provider supporting Ollama, Gemini, and OpenAI-compatible APIs."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 90.0,
    ) -> None:
        self.timeout = timeout
        self.provider, self.model, self.base_url, self.api_key = self._detect_config(
            provider, model, base_url, api_key
        )

    def _detect_config(
        self,
        provider: Optional[str],
        model: Optional[str],
        base_url: Optional[str],
        api_key: Optional[str],
    ):
        env_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        gemini_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        openai_key = api_key or os.getenv("OPENAI_API_KEY")

        if env_provider == "gemini" or (not env_provider and gemini_key):
            return (
                "gemini",
                model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                base_url or os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
                gemini_key or "",
            )

        if env_provider == "openai" or (not env_provider and openai_key):
            return (
                "openai",
                model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                openai_key or "",
            )

        # Default to local Ollama (e.g., deepseek-coder:6.7b)
        ollama_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        ollama_model = model or os.getenv("OLLAMA_MODEL") or "deepseek-coder:6.7b"
        self._ensure_ollama_running(ollama_url)
        return ("ollama", ollama_model, ollama_url, "")

    def _ensure_ollama_running(self, ollama_url: str) -> bool:
        """Verify Ollama server is reachable, starting it if installed locally."""
        try:
            resp = httpx.get(f"{ollama_url}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            default_win_path = os.path.expanduser(r"~\AppData\Local\Programs\Ollama\ollama.exe")
            if os.path.isfile(default_win_path):
                ollama_bin = default_win_path

        if ollama_bin:
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    [ollama_bin, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                for _ in range(10):
                    time.sleep(0.5)
                    try:
                        resp = httpx.get(f"{ollama_url}/api/tags", timeout=2.0)
                        if resp.status_code == 200:
                            return True
                    except Exception:
                        continue
            except Exception as exc:
                logger.warning("Failed to auto-start Ollama: %s", exc)
        return False

    def is_configured(self) -> bool:
        """Check whether the configured LLM provider is available and ready."""
        if self.provider == "gemini":
            return bool(self.api_key)
        if self.provider == "openai":
            return bool(self.api_key)
        if self.provider == "ollama":
            try:
                resp = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
                return resp.status_code == 200
            except Exception:
                return False
        return False

    def generate_step(
        self,
        task_prompt: str,
        history: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
    ) -> AgentStep:
        """Call the configured real LLM to generate the next structured AgentStep."""
        user_message = _build_workflow_guidance(task_prompt, history)
        last_error: Optional[Exception] = None

        for attempt in range(2):
            prompt_to_send = user_message
            if attempt > 0 and last_error is not None:
                prompt_to_send = (
                    f"{user_message}\n\n"
                    f"IMPORTANT: Your previous response failed validation with error: {last_error}. "
                    "You MUST output a single valid JSON object with 'thought', 'action' ('tool_call' or 'final_answer'), "
                    "and either ('tool_name' and 'tool_args') or 'final_report'."
                )
            try:
                raw_response = self._call_llm(SYSTEM_PROMPT, prompt_to_send)
                step = parse_agent_step(raw_response)
                return step
            except Exception as exc:
                last_error = exc
                logger.warning("LLM step generation attempt %d failed: %s", attempt + 1, exc)

        raise RuntimeError(f"LLM failed to produce a valid AgentStep after retries: {last_error}")

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "ollama":
            return self._call_ollama(system_prompt, user_prompt)
        if self.provider == "gemini":
            return self._call_gemini(system_prompt, user_prompt)
        if self.provider == "openai":
            return self._call_openai(system_prompt, user_prompt)
        raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "keep_alive": "60m",
            "options": {
                "temperature": 0.0,
                "num_ctx": 2048,
            },
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            if not content:
                raise ValueError(f"Empty response content from Ollama ({self.model})")
            return content

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates returned from Gemini API")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ValueError("No content parts returned from Gemini API")
            return parts[0].get("text", "")

    def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No choices returned from OpenAI API")
            return choices[0].get("message", {}).get("content", "")


_default_provider: Optional[LLMProvider] = None


def get_llm_provider() -> LLMProvider:
    """Return singleton LLMProvider instance."""
    global _default_provider
    if _default_provider is None:
        _default_provider = LLMProvider()
    return _default_provider
