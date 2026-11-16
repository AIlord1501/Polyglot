# Polyglot AI Developer Platform (MVP)

A multi-service AI developer platform featuring a **Go** orchestrator & security gate, a **Python FastAPI + gRPC** agent runtime with real LLM integration, **SQLite** persistence, and a **React + TypeScript + Vite + Tailwind CSS** developer dashboard.

---

## Architecture

```text
React + TS + Vite Frontend (:5173)
        │  HTTP REST + SSE (/api/v1/*)
        ▼
Go Backend Orchestrator (:8080)
  ├── SQLite Persistence (workspaces, tasks, task_events, tool_calls)
  ├── Workspace Path Jail (canonical path & symlink containment)
  └── Tool Authorization Gate (POST /internal/v1/tools/execute)
        │  gRPC Stream (ExecuteTask / HealthCheck)        ▲
        ▼                                                 │ Tool Callback
Python Agent Runtime (:50051 gRPC / :8001 HTTP) ──────────┘
  ├── Real LLM Provider (Ollama, Gemini, OpenAI-compatible)
  ├── Structured Step Output & Bounded Loop (max_iterations)
  └── Pydantic Tool Registry (list_files, read_file, write_file, search_files, run_command)
```

---

## Repository Structure

- `proto/agent.proto` — Canonical Protobuf / gRPC service contract (`AgentRuntimeService`)
- `backend/` — Go HTTP/SSE server, pure-Go SQLite store (`modernc.org/sqlite`), workspace sandbox, and tool execution gate
- `agent-runtime/` — Python gRPC server, FastAPI `/health` diagnostics, LLM provider abstraction, tool registry, and bounded ReAct loop
- `frontend/` — React + TypeScript + Vite + Tailwind CSS dashboard (workspace explorer, task launcher, live SSE execution timeline, tool audit panel)
- `tests/` — End-to-end MVP demonstration, security boundary, and edge-case test suites (`verify_mvp.py`, `test_mvp.py`, `test_phase2_edge_cases.py`)

---

## Prerequisites

- **Go** (1.22+)
- **Python** (3.10+) with packages from `agent-runtime/requirements.txt`
- **Node.js** (18+) & **npm**
- **Ollama** (default local LLM provider on `http://127.0.0.1:11434` with `deepseek-coder:6.7b`), or a `GEMINI_API_KEY` / `OPENAI_API_KEY`

---

## Configuration (`.env`)

Copy `agent-runtime/.env.example` to `agent-runtime/.env` and configure your provider:

```ini
# Provider selection: "ollama" (default), "gemini", or "openai"
LLM_PROVIDER=ollama

# Local Ollama configuration (default)
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=deepseek-coder:6.7b

# Google Gemini configuration (optional)
# GEMINI_API_KEY=your-gemini-api-key
# GEMINI_MODEL=gemini-2.5-flash

# OpenAI-compatible configuration (optional)
# OPENAI_API_KEY=your-openai-api-key
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o-mini

# Server ports
GRPC_PORT=50051
HTTP_PORT=8001
```

---

## Running Locally (Windows PowerShell)

Start each service in its own PowerShell terminal:

```powershell
# 1. Start Ollama (if not already running) and pull the default model
ollama serve
ollama pull deepseek-coder:6.7b

# 2. Start Python Agent Runtime (gRPC :50051 + FastAPI :8001)
cd agent-runtime
pip install -r requirements.txt
python -m app.main

# 3. Build & Start Go Backend (HTTP/SSE :8080)
cd backend
go build -o server.exe ./cmd/server
.\server.exe

# 4. Start React + Vite Frontend (:5173)
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Open **`http://localhost:5173`** in your browser.

---

## Security Boundaries

- **Workspace Isolation**: All file operations (`list_files`, `read_file`, `write_file`, `search_files`) and REST file endpoints resolve canonical paths and reject `..` traversal, volume escapes, symlinks escaping the root, and outside absolute paths with HTTP `403` / `DENIED`.
- **Controlled Command Execution**: `run_command` executes with `cmd.Dir` locked to the workspace root, a 20-second timeout, and an explicit allowlist (`python`, `python3`, `pytest`, `go test`, `npm test`), rejecting shell metacharacters (`;`, `&&`, `|`), `-c`, and unauthorized binaries.
- **Iteration Guard**: Tasks enforce `max_iterations` (default `8`, hard cap `12`).

---

## Running Tests

```powershell
# Go Backend unit tests
cd backend
go test -count=1 ./...

# Python Agent Runtime unit tests
python -m pytest agent-runtime/tests -v

# Frontend typecheck & production build
cd frontend
npm run build

# End-to-End MVP & Security Suite (requires backend :8080 and agent-runtime :50051 running)
python tests/verify_mvp.py
python -m pytest tests/test_mvp.py tests/test_phase2_edge_cases.py -v
```
