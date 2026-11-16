package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"polyglot/backend/internal/store"
	"polyglot/backend/internal/workspace"
)

func TestServerEndpointsAndSecurityGate(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "api_test.db")
	st, err := store.NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatalf("NewSQLiteStore: %v", err)
	}
	defer st.Close()

	wsMgr, err := workspace.NewManager(filepath.Join(tmpDir, "workspaces"))
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}

	srv := NewServer(st, wsMgr, "127.0.0.1:59999", "")
	ts := httptest.NewServer(srv)
	defer ts.Close()

	// 1. Create Workspace
	resp, err := http.Post(ts.URL+"/api/v1/workspaces", "application/json", bytes.NewBufferString(`{"name":"calc-ws"}`))
	if err != nil {
		t.Fatalf("POST /workspaces: %v", err)
	}
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201, got %d", resp.StatusCode)
	}
	var ws store.Workspace
	_ = json.NewDecoder(resp.Body).Decode(&ws)
	resp.Body.Close()

	// 2. Write file via tool execution gate
	toolPayload := map[string]any{
		"workspace_id": ws.ID,
		"iteration":    1,
		"tool_name":    "write_file",
		"arguments": map[string]any{
			"path":    "calc.py",
			"content": "def add(a, b): return a + b\n",
		},
	}
	b, _ := json.Marshal(toolPayload)
	resp, err = http.Post(ts.URL+"/internal/v1/tools/execute", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatalf("POST /internal/v1/tools/execute: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	resp.Body.Close()

	// 3. Verify path traversal via GET /api/v1/workspaces/{id}/file?path=../outside.txt returns 403
	resp, err = http.Get(ts.URL + "/api/v1/workspaces/" + ws.ID + "/file?path=../outside.txt")
	if err != nil {
		t.Fatalf("GET file traversal: %v", err)
	}
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403 Forbidden for traversal, got %d", resp.StatusCode)
	}
	resp.Body.Close()

	// 4. Verify tool gate rejects path traversal with 403 and status DENIED
	badTool := map[string]any{
		"workspace_id": ws.ID,
		"iteration":    1,
		"tool_name":    "read_file",
		"arguments": map[string]any{
			"path": "..\\..\\Windows\\win.ini",
		},
	}
	b, _ = json.Marshal(badTool)
	resp, err = http.Post(ts.URL+"/internal/v1/tools/execute", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatalf("POST traversal tool: %v", err)
	}
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("expected 403 for tool traversal, got %d", resp.StatusCode)
	}
	var toolRes map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&toolRes)
	resp.Body.Close()
	if toolRes["status"] != "DENIED" {
		t.Fatalf("expected status DENIED, got %v", toolRes["status"])
	}
}
