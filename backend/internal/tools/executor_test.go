package tools

import (
	"context"
	"encoding/json"
	"testing"

	"polyglot/backend/internal/workspace"
)

func TestExecutorSecurityAndValidation(t *testing.T) {
	tmpBase := t.TempDir()
	mgr, err := workspace.NewManager(tmpBase)
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	wsRoot, err := mgr.PrepareWorkspaceDir("exec-test", "")
	if err != nil {
		t.Fatalf("PrepareWorkspaceDir: %v", err)
	}
	exec := NewExecutor(mgr)
	ctx := context.Background()

	// 1. Unknown tool -> DENIED (403)
	res := exec.Execute(ctx, wsRoot, ExecuteRequest{
		ToolName:  "delete_all_files",
		Arguments: json.RawMessage(`{"path": "."}`),
	})
	if res.Status != "DENIED" || res.HTTPStatus != 403 {
		t.Fatalf("expected DENIED/403 for unknown tool, got %s/%d", res.Status, res.HTTPStatus)
	}

	// 2. Malformed arguments -> DENIED (400)
	res = exec.Execute(ctx, wsRoot, ExecuteRequest{
		ToolName:  "read_file",
		Arguments: json.RawMessage(`{"wrong_field": 123}`),
	})
	if res.Status != "DENIED" || res.HTTPStatus != 400 {
		t.Fatalf("expected DENIED/400 for missing path, got %s/%d", res.Status, res.HTTPStatus)
	}

	// 3. Path traversal in read_file / write_file -> DENIED (403)
	for _, toolName := range []string{"read_file", "write_file"} {
		res = exec.Execute(ctx, wsRoot, ExecuteRequest{
			ToolName:  toolName,
			Arguments: json.RawMessage(`{"path": "../secret.txt", "content": "x"}`),
		})
		if res.Status != "DENIED" || res.HTTPStatus != 403 {
			t.Fatalf("expected DENIED/403 for %s traversal, got %s/%d", toolName, res.Status, res.HTTPStatus)
		}
	}

	// 4. Unauthorized commands & shell injection -> DENIED (403)
	forbiddenCmds := []struct {
		cmd  string
		args []string
	}{
		{"powershell", []string{"-Command", "Get-Process"}},
		{"cmd", []string{"/c", "dir"}},
		{"curl", []string{"http://example.com"}},
		{"rm", []string{"-rf", "/"}},
		{"python", []string{"-m", "unittest; whoami"}},
		{"python && calc.exe", nil},
		{"pytest | Out-File pwn.txt", nil},
		{"python", []string{"../outside.py"}},
		{"python", []string{"C:\\Windows\\win.ini"}},
		{"python", []string{"-c", "import os; os.system('whoami')"}},
		{"go", []string{"run", "main.go"}},
		{"npm", []string{"install", "malicious-pkg"}},
	}

	for _, fc := range forbiddenCmds {
		rawArgs, _ := json.Marshal(map[string]any{
			"command": fc.cmd,
			"args":    fc.args,
		})
		res = exec.Execute(ctx, wsRoot, ExecuteRequest{
			ToolName:  "run_command",
			Arguments: rawArgs,
		})
		if res.Status != "DENIED" || res.HTTPStatus != 403 {
			t.Errorf("expected DENIED/403 for command=%q args=%v, got status=%s http=%d err=%s", fc.cmd, fc.args, res.Status, res.HTTPStatus, res.Error)
		}
	}

	// 5. Authorized write_file, read_file, search_files, list_files, and run_command
	res = exec.Execute(ctx, wsRoot, ExecuteRequest{
		ToolName:  "write_file",
		Arguments: json.RawMessage(`{"path": "test_sample.py", "content": "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(2+2, 4)\n"}`),
	})
	if res.Status != "COMPLETED" {
		t.Fatalf("write_file failed: %+v", res)
	}

	res = exec.Execute(ctx, wsRoot, ExecuteRequest{
		ToolName:  "search_files",
		Arguments: json.RawMessage(`{"query": "assertEqual"}`),
	})
	if res.Status != "COMPLETED" || res.Output == "No matches found." {
		t.Fatalf("search_files failed: %+v", res)
	}

	res = exec.Execute(ctx, wsRoot, ExecuteRequest{
		ToolName:  "run_command",
		Arguments: json.RawMessage(`{"command": "python", "args": ["-m", "unittest", "test_sample.py"]}`),
	})
	if res.Status != "COMPLETED" {
		t.Fatalf("expected COMPLETED for python unittest, got status=%s err=%s out=%s", res.Status, res.Error, res.Output)
	}
}
