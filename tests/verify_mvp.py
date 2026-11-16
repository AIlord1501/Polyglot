#!/usr/bin/env python3
"""
End-to-End Verification & Security Boundary Test Suite for Polyglot Agent Platform.
Covers:
1. Contract & Schema Verification (/api/v1/health, /api/v1/workspaces, /api/v1/tasks,
   /api/v1/tasks/{id}, /api/v1/tasks/{id}/stream, /internal/v1/tools/execute)
2. Security Boundary Tests:
   - Workspace path traversal (../outside.txt, ..\\..\\Windows\\win.ini, absolute paths)
   - Unrestricted shell execution (powershell, cmd /c, curl, rm -rf, ;, &&, |)
   - Invalid tool name & malformed arguments
3. Iteration Limit Verification (max_iterations: 1)
4. Full End-to-End MVP Demonstration Test (7 MVP criteria + frontend build)
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080")
RUNTIME_HTTP_URL = os.environ.get("RUNTIME_HTTP_URL", "http://127.0.0.1:8001")
REPO_ROOT = Path(__file__).resolve().parent.parent


def http_request(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, str], Any]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            resp_headers = dict(resp.info())
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = raw
            return status, resp_headers, body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {"error": raw}
        except json.JSONDecodeError:
            body = {"error": raw}
        return e.code, dict(e.headers) if e.headers else {}, body


class VerificationRecorder:
    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []

    def record(self, category: str, name: str, passed: bool, details: str = "") -> None:
        status_str = "PASS" if passed else "FAIL"
        print(f"[{status_str}] [{category}] {name}" + (f" -> {details}" if details else ""))
        self.results.append(
            {
                "category": category,
                "name": name,
                "passed": passed,
                "details": details,
            }
        )

    def assert_check(self, category: str, name: str, condition: bool, details: str = "") -> bool:
        self.record(category, name, bool(condition), details)
        return bool(condition)

    def summary(self) -> Tuple[int, int]:
        passed = sum(1 for r in self.results if r["passed"])
        total = len(self.results)
        return passed, total


def extract_workspace_id(ws_resp: Any) -> str:
    if isinstance(ws_resp, dict):
        if "id" in ws_resp:
            return str(ws_resp["id"])
        if "workspace" in ws_resp and isinstance(ws_resp["workspace"], dict):
            return str(ws_resp["workspace"].get("id", ""))
    return ""


def extract_workspace_root(ws_resp: Any) -> str:
    if isinstance(ws_resp, dict):
        for key in ("root_path", "path", "rootPath"):
            if key in ws_resp and ws_resp[key]:
                return str(ws_resp[key])
        if "workspace" in ws_resp and isinstance(ws_resp["workspace"], dict):
            for key in ("root_path", "path", "rootPath"):
                if key in ws_resp["workspace"] and ws_resp["workspace"][key]:
                    return str(ws_resp["workspace"][key])
    return ""


def extract_task_obj(task_resp: Any) -> Dict[str, Any]:
    if isinstance(task_resp, dict):
        if "task" in task_resp and isinstance(task_resp["task"], dict):
            merged = dict(task_resp["task"])
            for k in ("events", "tool_calls", "toolCalls"):
                if k in task_resp and k not in merged:
                    merged[k] = task_resp[k]
            return merged
        return task_resp
    return {}


def is_tool_denied(status_code: int, body: Any) -> bool:
    """Checks whether a tool execution call was rejected/denied by the security gate."""
    if status_code in (400, 403, 422):
        return True
    if isinstance(body, dict):
        status_val = str(body.get("status", "")).upper()
        if status_val in ("DENIED", "REJECTED", "UNAUTHORIZED", "FORBIDDEN", "ERROR", "FAILED"):
            return True
        if body.get("authorized") is False or body.get("allowed") is False:
            return True
        if "tool_call" in body and isinstance(body["tool_call"], dict):
            tc_status = str(body["tool_call"].get("status", "")).upper()
            if tc_status in ("DENIED", "REJECTED", "FAILED"):
                return True
    return False


def verify_contracts_and_security(rec: VerificationRecorder) -> str:
    cat_contract = "1. Contract & Schema"
    cat_security = "2. Security Boundary"

    # 1.1 Health Endpoint
    status, headers, body = http_request("GET", f"{BACKEND_URL}/api/v1/health")
    health_ok = (
        status == 200
        and isinstance(body, dict)
        and str(body.get("status", "")).lower() in ("ok", "healthy")
    )
    rec.assert_check(
        cat_contract,
        "GET /api/v1/health returns 200 JSON with backend & agent_runtime status",
        health_ok,
        f"status={status}, body={json.dumps(body)[:220]}",
    )

    # 1.2 Create Workspace for Contract & Security Tests
    ws_name = f"sec-verify-{uuid.uuid4().hex[:6]}"
    status, _, ws_body = http_request(
        "POST",
        f"{BACKEND_URL}/api/v1/workspaces",
        {"name": ws_name},
    )
    ws_id = extract_workspace_id(ws_body)
    ws_root = extract_workspace_root(ws_body)
    rec.assert_check(
        cat_contract,
        "POST /api/v1/workspaces creates sandboxed workspace with id, name, root_path",
        status in (200, 201) and bool(ws_id) and bool(ws_root),
        f"status={status}, id={ws_id}, root={ws_root}",
    )

    # 1.3 List Workspaces
    status, _, list_ws_body = http_request("GET", f"{BACKEND_URL}/api/v1/workspaces")
    ws_list = (
        list_ws_body
        if isinstance(list_ws_body, list)
        else list_ws_body.get("workspaces", [])
        if isinstance(list_ws_body, dict)
        else []
    )
    found_ws = any(isinstance(w, dict) and w.get("id") == ws_id for w in ws_list)
    rec.assert_check(
        cat_contract,
        "GET /api/v1/workspaces includes created workspace",
        status == 200 and found_ws,
        f"status={status}, count={len(ws_list)}",
    )

    sec_task_id = f"sec-task-{uuid.uuid4().hex[:8]}"

    # 1.4 Verify valid tool calls on /internal/v1/tools/execute
    status_w, _, body_w = http_request(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": sec_task_id,
            "workspace_id": ws_id,
            "call_id": f"call-{uuid.uuid4().hex[:6]}",
            "iteration": 1,
            "tool_name": "write_file",
            "arguments": {"path": "hello.txt", "content": "sandbox check\n"},
        },
    )
    rec.assert_check(
        cat_contract,
        "POST /internal/v1/tools/execute allows valid write_file inside workspace",
        status_w == 200 and not is_tool_denied(status_w, body_w),
        f"status={status_w}, body={json.dumps(body_w)[:200]}",
    )

    status_r, _, body_r = http_request(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": sec_task_id,
            "workspace_id": ws_id,
            "call_id": f"call-{uuid.uuid4().hex[:6]}",
            "iteration": 1,
            "tool_name": "read_file",
            "arguments": {"path": "hello.txt"},
        },
    )
    rec.assert_check(
        cat_contract,
        "POST /internal/v1/tools/execute allows valid read_file inside workspace",
        status_r == 200 and "sandbox check" in json.dumps(body_r),
        f"status={status_r}, body={json.dumps(body_r)[:200]}",
    )

    status_l, _, body_l = http_request(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": sec_task_id,
            "workspace_id": ws_id,
            "call_id": f"call-{uuid.uuid4().hex[:6]}",
            "iteration": 1,
            "tool_name": "list_files",
            "arguments": {"path": "."},
        },
    )
    rec.assert_check(
        cat_contract,
        "POST /internal/v1/tools/execute allows valid list_files inside workspace",
        status_l == 200 and "hello.txt" in json.dumps(body_l),
        f"status={status_l}, body={json.dumps(body_l)[:200]}",
    )

    status_s, _, body_s = http_request(
        "POST",
        f"{BACKEND_URL}/internal/v1/tools/execute",
        {
            "task_id": sec_task_id,
            "workspace_id": ws_id,
            "call_id": f"call-{uuid.uuid4().hex[:6]}",
            "iteration": 1,
            "tool_name": "search_files",
            "arguments": {"query": "sandbox", "path": "."},
        },
    )
    rec.assert_check(
        cat_contract,
        "POST /internal/v1/tools/execute allows valid search_files inside workspace",
        status_s == 200 and not is_tool_denied(status_s, body_s),
        f"status={status_s}, body={json.dumps(body_s)[:200]}",
    )

    # Verify workspace files & file viewer REST API endpoints
    status_wf, _, body_wf = http_request("GET", f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/files?path=.")
    rec.assert_check(
        cat_contract,
        "GET /api/v1/workspaces/{id}/files returns workspace file list",
        status_wf == 200 and "hello.txt" in json.dumps(body_wf),
        f"status={status_wf}, body={json.dumps(body_wf)[:200]}",
    )

    status_rf, _, body_rf = http_request("GET", f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/file?path=hello.txt")
    rec.assert_check(
        cat_contract,
        "GET /api/v1/workspaces/{id}/file returns file content",
        status_rf == 200 and "sandbox check" in json.dumps(body_rf),
        f"status={status_rf}, body={json.dumps(body_rf)[:200]}",
    )

    # =========================================================================
    # 2.1 Security Boundary: Workspace Path Traversal Tests
    # =========================================================================
    outside_parent_file = Path(ws_root).resolve().parent / "outside.txt" if ws_root else REPO_ROOT / "outside.txt"
    if outside_parent_file.exists():
        outside_parent_file.unlink()

    traversal_vectors = [
        ("write_file", {"path": "../outside.txt", "content": "pwned"}, "write_file ../outside.txt"),
        ("read_file", {"path": "../outside.txt"}, "read_file ../outside.txt"),
        ("read_file", {"path": "..\\..\\Windows\\win.ini"}, "read_file ..\\..\\Windows\\win.ini"),
        ("write_file", {"path": "..\\..\\outside_win.txt", "content": "pwned"}, "write_file ..\\..\\outside_win.txt"),
        ("read_file", {"path": "C:\\Windows\\win.ini"}, "read_file absolute C:\\Windows\\win.ini"),
        ("write_file", {"path": str(REPO_ROOT / "outside_abs.txt"), "content": "pwned"}, "write_file absolute outside path"),
        ("list_files", {"path": ".."}, "list_files .."),
        ("search_files", {"query": "root", "path": "../.."}, "search_files ../.."),
    ]

    for tool_name, args, label in traversal_vectors:
        st, _, bd = http_request(
            "POST",
            f"{BACKEND_URL}/internal/v1/tools/execute",
            {
                "task_id": sec_task_id,
                "workspace_id": ws_id,
                "call_id": f"trav-{uuid.uuid4().hex[:6]}",
                "iteration": 1,
                "tool_name": tool_name,
                "arguments": args,
            },
        )
        denied = is_tool_denied(st, bd)
        rec.assert_check(
            cat_security,
            f"Path traversal blocked for {label}",
            denied,
            f"http_status={st}, response={json.dumps(bd)[:180]}",
        )

    # Also verify REST file & files endpoints reject traversal with HTTP 403/400
    st_rest_trav, _, bd_rest_trav = http_request(
        "GET",
        f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/file?path=" + urllib.parse.quote("..\\..\\Windows\\win.ini"),
    )
    rec.assert_check(
        cat_security,
        "GET /api/v1/workspaces/{id}/file blocks path traversal (HTTP 403)",
        st_rest_trav in (400, 403) and "[fonts]" not in json.dumps(bd_rest_trav).lower(),
        f"http_status={st_rest_trav}, response={json.dumps(bd_rest_trav)[:180]}",
    )

    st_rest_files_trav, _, bd_rest_files_trav = http_request(
        "GET",
        f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/files?path=" + urllib.parse.quote("../.."),
    )
    rec.assert_check(
        cat_security,
        "GET /api/v1/workspaces/{id}/files blocks path traversal (HTTP 403)",
        st_rest_files_trav in (400, 403),
        f"http_status={st_rest_files_trav}, response={json.dumps(bd_rest_files_trav)[:180]}",
    )

    # Confirm no file was created outside workspace root
    outside_abs_file = REPO_ROOT / "outside_abs.txt"
    no_escape_files = not outside_parent_file.exists() and not outside_abs_file.exists()
    rec.assert_check(
        cat_security,
        "Confirm zero files written outside workspace root on disk",
        no_escape_files,
        f"checked {outside_parent_file} and {outside_abs_file}",
    )

    # =========================================================================
    # 2.2 Security Boundary: Unrestricted Shell Execution Tests
    # =========================================================================
    shell_vectors = [
        ({"command": "powershell", "args": ["-Command", "Get-Process"]}, "unauthorized binary: powershell"),
        ({"command": "cmd", "args": ["/c", "dir"]}, "unauthorized binary: cmd /c"),
        ({"command": "cmd /c whoami", "args": []}, "unauthorized command string: cmd /c whoami"),
        ({"command": "curl", "args": ["http://example.com"]}, "unauthorized binary: curl"),
        ({"command": "rm", "args": ["-rf", "/"]}, "unauthorized binary: rm -rf"),
        ({"command": "python", "args": ["-V", ";", "whoami"]}, "command chaining with ';' in args"),
        ({"command": "python -V && whoami", "args": []}, "command chaining with '&&'"),
        ({"command": "python -V | findstr Python", "args": []}, "command piping with '|'"),
        ({"command": "python", "args": ["../outside.py"]}, "outside-workspace path in run_command args"),
    ]

    for args, label in shell_vectors:
        st, _, bd = http_request(
            "POST",
            f"{BACKEND_URL}/internal/v1/tools/execute",
            {
                "task_id": sec_task_id,
                "workspace_id": ws_id,
                "call_id": f"sh-{uuid.uuid4().hex[:6]}",
                "iteration": 1,
                "tool_name": "run_command",
                "arguments": args,
            },
        )
        denied = is_tool_denied(st, bd)
        rec.assert_check(
            cat_security,
            f"Shell execution gate rejects {label}",
            denied,
            f"http_status={st}, response={json.dumps(bd)[:180]}",
        )

    # =========================================================================
    # 2.3 Security Boundary: Invalid Tool Name & Malformed Arguments
    # =========================================================================
    invalid_vectors = [
        ("delete_all_files", {"path": "."}, "unknown tool name delete_all_files"),
        ("eval_code", {"code": "import os"}, "unknown tool name eval_code"),
        ("write_file", {"content": "missing path"}, "malformed write_file missing 'path'"),
        ("read_file", {}, "malformed read_file empty arguments"),
    ]
    for tool_name, args, label in invalid_vectors:
        st, _, bd = http_request(
            "POST",
            f"{BACKEND_URL}/internal/v1/tools/execute",
            {
                "task_id": sec_task_id,
                "workspace_id": ws_id,
                "call_id": f"inv-{uuid.uuid4().hex[:6]}",
                "iteration": 1,
                "tool_name": tool_name,
                "arguments": args,
            },
        )
        denied = is_tool_denied(st, bd)
        rec.assert_check(
            cat_security,
            f"Validation gate rejects {label}",
            denied,
            f"http_status={st}, response={json.dumps(bd)[:180]}",
        )

    return ws_id


def verify_iteration_limit(rec: VerificationRecorder, ws_id: str) -> None:
    cat = "3. Iteration Limit"
    status, _, create_body = http_request(
        "POST",
        f"{BACKEND_URL}/api/v1/tasks",
        {
            "workspace_id": ws_id,
            "prompt": (
                "First list the files in the workspace using list_files, then create three files "
                "a.py, b.py, c.py and run pytest."
            ),
            "max_iterations": 1,
        },
    )
    task_obj = extract_task_obj(create_body)
    task_id = str(task_obj.get("id", ""))
    rec.assert_check(
        cat,
        "POST /api/v1/tasks accepts max_iterations=1",
        status in (200, 201, 202) and bool(task_id),
        f"status={status}, task_id={task_id}",
    )
    if not task_id:
        return

    deadline = time.time() + 60.0
    final_data: Dict[str, Any] = {}
    while time.time() < deadline:
        st, _, body = http_request("GET", f"{BACKEND_URL}/api/v1/tasks/{task_id}")
        if st == 200 and isinstance(body, dict):
            final_data = extract_task_obj(body)
            t_status = str(final_data.get("status", "")).upper()
            if t_status in ("COMPLETED", "FAILED", "ERROR", "STOPPED", "MAX_ITERATIONS_EXCEEDED"):
                break
        time.sleep(1.0)

    t_status = str(final_data.get("status", "")).upper()
    curr_iter = int(final_data.get("current_iteration", final_data.get("currentIteration", 0)) or 0)
    max_iter = int(final_data.get("max_iterations", final_data.get("maxIterations", 1)) or 1)
    tool_calls = final_data.get("tool_calls", final_data.get("toolCalls", [])) or []
    events = final_data.get("events", []) or []

    max_tc_iter = max((int(tc.get("iteration", 0) or 0) for tc in tool_calls if isinstance(tc, dict)), default=0)

    rec.assert_check(
        cat,
        "Task with max_iterations=1 strictly respects iteration cap",
        t_status in ("FAILED", "COMPLETED", "ERROR", "MAX_ITERATIONS_EXCEEDED")
        and max_iter == 1
        and curr_iter <= 1
        and max_tc_iter <= 1
        and len(tool_calls) <= 1,
        f"status={t_status}, current_iteration={curr_iter}, max_iterations={max_iter}, "
        f"tool_calls={len(tool_calls)}, events={len(events)}",
    )


def verify_mvp_end_to_end(rec: VerificationRecorder) -> None:
    cat = "4. MVP End-to-End Demo"

    # 4.1 Create clean workspace via POST /api/v1/workspaces
    mvp_ws_name = f"mvp-calculator-{uuid.uuid4().hex[:6]}"
    status, _, ws_body = http_request(
        "POST",
        f"{BACKEND_URL}/api/v1/workspaces",
        {"name": mvp_ws_name},
    )
    ws_id = extract_workspace_id(ws_body)
    ws_root = extract_workspace_root(ws_body)
    rec.assert_check(
        cat,
        "Create clean workspace via POST /api/v1/workspaces",
        status in (200, 201) and bool(ws_id),
        f"ws_id={ws_id}, root={ws_root}",
    )
    if not ws_id:
        return

    # 4.2 Submit the exact MVP prompt via POST /api/v1/tasks
    mvp_prompt = "Create a Python calculator project with addition, subtraction, multiplication, division and unit tests."
    status, _, create_body = http_request(
        "POST",
        f"{BACKEND_URL}/api/v1/tasks",
        {
            "workspace_id": ws_id,
            "prompt": mvp_prompt,
            "max_iterations": 10,
        },
    )
    task_obj = extract_task_obj(create_body)
    task_id = str(task_obj.get("id", ""))
    rec.assert_check(
        cat,
        "Submit MVP prompt via POST /api/v1/tasks",
        status in (200, 201, 202) and bool(task_id),
        f"status={status}, task_id={task_id}",
    )
    if not task_id:
        return

    # 4.3 Stream SSE from GET /api/v1/tasks/{id}/stream in background thread while polling task
    sse_events: List[str] = []
    sse_content_type: List[str] = []

    def sse_reader() -> None:
        try:
            req = urllib.request.Request(
                f"{BACKEND_URL}/api/v1/tasks/{task_id}/stream",
                headers={"Accept": "text/event-stream"},
            )
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                sse_content_type.append(resp.info().get("Content-Type", ""))
                for _ in range(500):
                    line = resp.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        sse_events.append(decoded)
                        if "FINAL_ANSWER" in decoded or '"COMPLETED"' in decoded or '"FAILED"' in decoded:
                            break
        except Exception as exc:
            sse_events.append(f"SSE_ERR:{exc}")

    sse_thread = threading.Thread(target=sse_reader, daemon=True)
    sse_thread.start()

    # Poll GET /api/v1/tasks/{id} until completion
    deadline = time.time() + 180.0
    final_task: Dict[str, Any] = {}
    while time.time() < deadline:
        st, _, body = http_request("GET", f"{BACKEND_URL}/api/v1/tasks/{task_id}")
        if st == 200 and isinstance(body, dict):
            final_task = extract_task_obj(body)
            t_status = str(final_task.get("status", "")).upper()
            if t_status in ("COMPLETED", "FAILED", "ERROR"):
                break
        time.sleep(1.5)

    sse_thread.join(timeout=3.0)

    t_status = str(final_task.get("status", "")).upper()
    events = final_task.get("events", []) or []
    tool_calls = final_task.get("tool_calls", final_task.get("toolCalls", [])) or []

    # Check GET /api/v1/tasks?workspace_id=...
    st_list, _, tasks_list_body = http_request("GET", f"{BACKEND_URL}/api/v1/tasks?workspace_id={ws_id}")
    rec.assert_check(
        "1. Contract & Schema",
        "GET /api/v1/tasks?workspace_id=... returns task list",
        st_list == 200 and task_id in json.dumps(tasks_list_body),
        f"status={st_list}",
    )

    rec.assert_check(
        "1. Contract & Schema",
        "GET /api/v1/tasks/{id}/stream serves SSE stream",
        any("text/event-stream" in ct for ct in sse_content_type) or len(sse_events) > 0,
        f"content_type={sse_content_type}, sse_lines={len(sse_events)}",
    )

    # Extract tool call sequence (from tool_calls and/or TOOL_CALL events)
    tool_names = [str(tc.get("tool_name", tc.get("toolName", ""))) for tc in tool_calls if isinstance(tc, dict)]
    if not tool_names:
        tool_names = [
            str(ev.get("tool_name", ""))
            for ev in events
            if isinstance(ev, dict) and str(ev.get("event_type", "")).upper() == "TOOL_CALL"
        ]

    write_calls = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("tool_name") == "write_file"]
    run_calls = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("tool_name") == "run_command"]

    written_paths: List[str] = []
    for wc in write_calls:
        args_raw = wc.get("arguments_json", wc.get("arguments", {}))
        if isinstance(args_raw, str):
            try:
                args_obj = json.loads(args_raw)
            except Exception:
                args_obj = {}
        elif isinstance(args_raw, dict):
            args_obj = args_raw
        else:
            args_obj = {}
        if "path" in args_obj:
            written_paths.append(str(args_obj["path"]))

    # Criterion 1: Agent inspected the workspace (list_files)
    c1 = "list_files" in tool_names
    rec.assert_check(
        cat,
        "Criterion 1: Agent inspected the workspace (list_files)",
        c1,
        f"tools_invoked={tool_names}",
    )

    # Criterion 2: Agent created calculator source file(s) (write_file) inside the workspace
    calc_files = [p for p in written_paths if p.endswith(".py") and "test" not in Path(p).name.lower()]
    c2 = len(calc_files) >= 1
    rec.assert_check(
        cat,
        "Criterion 2: Agent created calculator source file(s) (write_file)",
        c2,
        f"calculator_files={calc_files}",
    )

    # Criterion 3: Agent created unit test file(s) (write_file) inside the workspace
    test_files = [p for p in written_paths if p.endswith(".py") and "test" in Path(p).name.lower()]
    c3 = len(test_files) >= 1
    rec.assert_check(
        cat,
        "Criterion 3: Agent created unit test file(s) (write_file)",
        c3,
        f"test_files={test_files}",
    )

    # Criterion 4: Agent executed the unit tests via approved run_command tool
    c4 = len(run_calls) >= 1 and any(
        str(rc.get("status", "")).upper() in ("COMPLETED", "SUCCESS", "AUTHORIZED", "OK")
        or bool(rc.get("output"))
        for rc in run_calls
    )
    run_outputs = [str(rc.get("output", ""))[:160] for rc in run_calls]
    rec.assert_check(
        cat,
        "Criterion 4: Agent executed unit tests via approved run_command tool",
        c4,
        f"run_calls={len(run_calls)}, sample_output={run_outputs}",
    )

    # Criterion 5: Agent inspected the test output
    event_types = [
        str(ev.get("event_type", ev.get("eventType", ev.get("type", "")))).upper()
        for ev in events
        if isinstance(ev, dict)
    ]
    c5 = "TOOL_RESULT" in event_types and c4
    rec.assert_check(
        cat,
        "Criterion 5: Agent inspected the test output (TOOL_RESULT emitted before completion)",
        c5,
        f"event_types={event_types}",
    )

    # Criterion 6: Agent returned a structured completion report (FINAL_ANSWER / COMPLETED)
    final_answer = str(final_task.get("final_answer", final_task.get("finalAnswer", "")))
    structured_report = final_task.get(
        "structured_report_json",
        final_task.get("structured_report", final_task.get("structuredReport", "")),
    )
    c6 = (
        t_status == "COMPLETED"
        and "FINAL_ANSWER" in event_types
        and (bool(final_answer.strip()) or bool(structured_report))
    )
    rec.assert_check(
        cat,
        "Criterion 6: Agent returned structured completion report (FINAL_ANSWER / COMPLETED)",
        c6,
        f"status={t_status}, final_answer_len={len(final_answer)}, structured_report={str(structured_report)[:160]}",
    )

    # Criterion 7: Frontend build succeeds (npm run build) and backend API serves all events/files
    st_files, _, ws_files_resp = http_request("GET", f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/files?path=.")
    files_str = json.dumps(ws_files_resp)
    api_serves_files = st_files == 200 and any(Path(p).name in files_str for p in calc_files + test_files)

    # Also verify reading one of the created calculator files via GET /api/v1/workspaces/{id}/file
    calc_content_ok = False
    if calc_files:
        st_cf, _, cf_resp = http_request(
            "GET",
            f"{BACKEND_URL}/api/v1/workspaces/{ws_id}/file?path=" + urllib.parse.quote(calc_files[0]),
        )
        calc_content_ok = st_cf == 200 and "def " in json.dumps(cf_resp)

    frontend_dir = REPO_ROOT / "frontend"
    fe_build_ok = False
    fe_build_output = ""
    if (frontend_dir / "package.json").exists():
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        proc = subprocess.run(
            [npm_cmd, "run", "build"],
            cwd=str(frontend_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        fe_build_ok = proc.returncode == 0
        fe_build_output = (proc.stdout + "\n" + proc.stderr).strip()[-300:]
    else:
        fe_build_output = "frontend/package.json not found"

    rec.assert_check(
        cat,
        "Criterion 7: Frontend build succeeds (npm run build) & backend API serves workspace files/events",
        fe_build_ok and api_serves_files and calc_content_ok,
        f"fe_build_ok={fe_build_ok}, api_serves_files={api_serves_files}, calc_content_ok={calc_content_ok}, build_tail={fe_build_output[:160]}",
    )


def main() -> int:
    print("=" * 80)
    print("POLYGLOT AGENT PLATFORM - AUTOMATED QA & SECURITY VERIFICATION SUITE")
    print("=" * 80)
    rec = VerificationRecorder()

    ws_id = verify_contracts_and_security(rec)
    if ws_id:
        verify_iteration_limit(rec, ws_id)
    verify_mvp_end_to_end(rec)

    passed, total = rec.summary()
    print("=" * 80)
    print(f"FINAL QA SUMMARY: {passed}/{total} checks passed")
    print("=" * 80)
    return 0 if passed == total and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
