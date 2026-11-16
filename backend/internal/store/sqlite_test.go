package store

import (
	"path/filepath"
	"testing"
)

func TestSQLiteStoreCRUD(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "test.db")
	st, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatalf("NewSQLiteStore failed: %v", err)
	}
	defer st.Close()

	ws, err := st.CreateWorkspace("ws-1", "Demo Workspace", "/tmp/demo")
	if err != nil {
		t.Fatalf("CreateWorkspace: %v", err)
	}
	if ws.ID != "ws-1" || ws.Name != "Demo Workspace" {
		t.Fatalf("unexpected workspace: %+v", ws)
	}

	workspaces, err := st.ListWorkspaces()
	if err != nil || len(workspaces) != 1 {
		t.Fatalf("ListWorkspaces failed: %v len=%d", err, len(workspaces))
	}

	task, err := st.CreateTask(ws.ID, "Build a calculator", 8)
	if err != nil {
		t.Fatalf("CreateTask: %v", err)
	}
	if task.Status != "PENDING" || task.MaxIterations != 8 {
		t.Fatalf("unexpected task: %+v", task)
	}

	_, err = st.CreateTaskEvent(TaskEvent{
		TaskID:    task.ID,
		Iteration: 1,
		EventType: "THOUGHT",
		Content:   "I will inspect the files first.",
	})
	if err != nil {
		t.Fatalf("CreateTaskEvent: %v", err)
	}

	_, err = st.CreateToolCall(ToolCall{
		ID:            "tc-1",
		TaskID:        task.ID,
		Iteration:     1,
		ToolName:      "list_files",
		ArgumentsJSON: `{"path":"."}`,
		Status:        "COMPLETED",
		Output:        "[]",
	})
	if err != nil {
		t.Fatalf("CreateToolCall: %v", err)
	}

	err = st.UpdateTaskStatus(task.ID, "COMPLETED", 2, "Done!", `{"summary":"All good","passed":true}`, "")
	if err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	loaded, err := st.GetTask(task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if loaded.Status != "COMPLETED" || loaded.CurrentIteration != 2 || len(loaded.Events) != 1 || len(loaded.ToolCalls) != 1 {
		t.Fatalf("unexpected loaded task: %+v", loaded)
	}
	if loaded.StructuredReport == nil {
		t.Fatalf("expected StructuredReport to be parsed")
	}
}
