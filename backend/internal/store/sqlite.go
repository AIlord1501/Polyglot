package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	_ "modernc.org/sqlite"
)

type Workspace struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	RootPath  string `json:"root_path"`
	Path      string `json:"path"`
	CreatedAt string `json:"created_at"`
}

type Task struct {
	ID                   string       `json:"id"`
	WorkspaceID          string       `json:"workspace_id"`
	Prompt               string       `json:"prompt"`
	Status               string       `json:"status"`
	FinalAnswer          string       `json:"final_answer"`
	StructuredReportJSON string       `json:"structured_report_json"`
	StructuredReport     any          `json:"structured_report,omitempty"`
	MaxIterations        int          `json:"max_iterations"`
	CurrentIteration     int          `json:"current_iteration"`
	Error                string       `json:"error"`
	CreatedAt            string       `json:"created_at"`
	UpdatedAt            string       `json:"updated_at"`
	Events               []TaskEvent  `json:"events,omitempty"`
	ToolCalls            []ToolCall   `json:"tool_calls,omitempty"`
}

type TaskEvent struct {
	ID                   string `json:"id"`
	TaskID               string `json:"task_id"`
	Iteration            int    `json:"iteration"`
	EventType            string `json:"event_type"`
	Content              string `json:"content"`
	ToolName             string `json:"tool_name,omitempty"`
	ToolArgsJSON         string `json:"tool_args_json,omitempty"`
	ToolOutput           string `json:"tool_output,omitempty"`
	StructuredReportJSON string `json:"structured_report_json,omitempty"`
	CreatedAt            string `json:"created_at"`
}

type ToolCall struct {
	ID            string `json:"id"`
	TaskID        string `json:"task_id"`
	Iteration     int    `json:"iteration"`
	ToolName      string `json:"tool_name"`
	ArgumentsJSON string `json:"arguments_json"`
	Status        string `json:"status"`
	Output        string `json:"output"`
	Error         string `json:"error"`
	CreatedAt     string `json:"created_at"`
	UpdatedAt     string `json:"updated_at"`
}

type Store struct {
	db *sql.DB
	mu sync.Mutex
}

func NewSQLiteStore(dbPath string) (*Store, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Serialize access to avoid SQLITE_BUSY under concurrent goroutines
	db.SetMaxOpenConns(1)

	s := &Store{db: db}
	if err := s.migrate(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) migrate() error {
	queries := []string{
		`PRAGMA journal_mode=WAL;`,
		`PRAGMA busy_timeout=5000;`,
		`CREATE TABLE IF NOT EXISTS workspaces (
			id TEXT PRIMARY KEY,
			name TEXT NOT NULL,
			root_path TEXT NOT NULL,
			created_at TEXT NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			workspace_id TEXT NOT NULL,
			prompt TEXT NOT NULL,
			status TEXT NOT NULL,
			final_answer TEXT NOT NULL DEFAULT '',
			structured_report_json TEXT NOT NULL DEFAULT '',
			max_iterations INTEGER NOT NULL DEFAULT 8,
			current_iteration INTEGER NOT NULL DEFAULT 0,
			error TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS task_events (
			id TEXT PRIMARY KEY,
			task_id TEXT NOT NULL,
			iteration INTEGER NOT NULL DEFAULT 0,
			event_type TEXT NOT NULL,
			content TEXT NOT NULL DEFAULT '',
			tool_name TEXT NOT NULL DEFAULT '',
			tool_args_json TEXT NOT NULL DEFAULT '',
			tool_output TEXT NOT NULL DEFAULT '',
			structured_report_json TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS tool_calls (
			id TEXT PRIMARY KEY,
			task_id TEXT NOT NULL,
			iteration INTEGER NOT NULL DEFAULT 0,
			tool_name TEXT NOT NULL,
			arguments_json TEXT NOT NULL DEFAULT '{}',
			status TEXT NOT NULL,
			output TEXT NOT NULL DEFAULT '',
			error TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		);`,
	}

	for _, q := range queries {
		if _, err := s.db.Exec(q); err != nil {
			return fmt.Errorf("migrate query failed: %w", err)
		}
	}
	return nil
}

func nowISO() string {
	return time.Now().UTC().Format(time.RFC3339Nano)
}

func (s *Store) CreateWorkspace(id, name, rootPath string) (*Workspace, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if id == "" {
		id = uuid.NewString()
	}
	createdAt := nowISO()
	_, err := s.db.Exec(
		`INSERT INTO workspaces (id, name, root_path, created_at) VALUES (?, ?, ?, ?)`,
		id, name, rootPath, createdAt,
	)
	if err != nil {
		return nil, fmt.Errorf("insert workspace: %w", err)
	}
	return &Workspace{
		ID:        id,
		Name:      name,
		RootPath:  rootPath,
		Path:      rootPath,
		CreatedAt: createdAt,
	}, nil
}

func (s *Store) GetWorkspace(id string) (*Workspace, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var w Workspace
	err := s.db.QueryRow(
		`SELECT id, name, root_path, created_at FROM workspaces WHERE id = ?`, id,
	).Scan(&w.ID, &w.Name, &w.RootPath, &w.CreatedAt)
	if err != nil {
		return nil, err
	}
	w.Path = w.RootPath
	return &w, nil
}

func (s *Store) GetWorkspaceByRootPath(rootPath string) (*Workspace, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var w Workspace
	err := s.db.QueryRow(
		`SELECT id, name, root_path, created_at FROM workspaces WHERE root_path = ?`, rootPath,
	).Scan(&w.ID, &w.Name, &w.RootPath, &w.CreatedAt)
	if err != nil {
		return nil, err
	}
	w.Path = w.RootPath
	return &w, nil
}

func (s *Store) ListWorkspaces() ([]Workspace, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	rows, err := s.db.Query(`SELECT id, name, root_path, created_at FROM workspaces ORDER BY created_at ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make([]Workspace, 0)
	for rows.Next() {
		var w Workspace
		if err := rows.Scan(&w.ID, &w.Name, &w.RootPath, &w.CreatedAt); err != nil {
			return nil, err
		}
		w.Path = w.RootPath
		result = append(result, w)
	}
	return result, rows.Err()
}

func populateStructuredReport(t *Task) {
	if strings.TrimSpace(t.StructuredReportJSON) != "" {
		var parsed any
		if err := json.Unmarshal([]byte(t.StructuredReportJSON), &parsed); err == nil {
			t.StructuredReport = parsed
		}
	}
}

func (s *Store) CreateTask(workspaceID, prompt string, maxIterations int) (*Task, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	id := uuid.NewString()
	now := nowISO()
	t := &Task{
		ID:               id,
		WorkspaceID:      workspaceID,
		Prompt:           prompt,
		Status:           "PENDING",
		MaxIterations:    maxIterations,
		CurrentIteration: 0,
		CreatedAt:        now,
		UpdatedAt:        now,
		Events:           []TaskEvent{},
		ToolCalls:        []ToolCall{},
	}

	_, err := s.db.Exec(
		`INSERT INTO tasks (id, workspace_id, prompt, status, final_answer, structured_report_json, max_iterations, current_iteration, error, created_at, updated_at)
		 VALUES (?, ?, ?, ?, '', '', ?, 0, '', ?, ?)`,
		t.ID, t.WorkspaceID, t.Prompt, t.Status, t.MaxIterations, t.CreatedAt, t.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("insert task: %w", err)
	}
	return t, nil
}

func (s *Store) UpdateTaskStatus(taskID, status string, currentIteration int, finalAnswer, structuredReportJSON, errMsg string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := nowISO()
	_, err := s.db.Exec(
		`UPDATE tasks
		 SET status = ?,
		     current_iteration = CASE WHEN ? > current_iteration THEN ? ELSE current_iteration END,
		     final_answer = CASE WHEN ? != '' THEN ? ELSE final_answer END,
		     structured_report_json = CASE WHEN ? != '' THEN ? ELSE structured_report_json END,
		     error = CASE WHEN ? != '' THEN ? ELSE error END,
		     updated_at = ?
		 WHERE id = ?`,
		status,
		currentIteration, currentIteration,
		finalAnswer, finalAnswer,
		structuredReportJSON, structuredReportJSON,
		errMsg, errMsg,
		now, taskID,
	)
	return err
}

func (s *Store) UpdateTaskIteration(taskID string, iteration int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := nowISO()
	_, err := s.db.Exec(
		`UPDATE tasks SET current_iteration = CASE WHEN ? > current_iteration THEN ? ELSE current_iteration END, updated_at = ? WHERE id = ?`,
		iteration, iteration, now, taskID,
	)
	return err
}

func (s *Store) GetTask(id string) (*Task, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var t Task
	err := s.db.QueryRow(
		`SELECT id, workspace_id, prompt, status, final_answer, structured_report_json, max_iterations, current_iteration, error, created_at, updated_at
		 FROM tasks WHERE id = ?`, id,
	).Scan(
		&t.ID, &t.WorkspaceID, &t.Prompt, &t.Status, &t.FinalAnswer, &t.StructuredReportJSON,
		&t.MaxIterations, &t.CurrentIteration, &t.Error, &t.CreatedAt, &t.UpdatedAt,
	)
	if err != nil {
		return nil, err
	}
	populateStructuredReport(&t)

	evRows, err := s.db.Query(
		`SELECT id, task_id, iteration, event_type, content, tool_name, tool_args_json, tool_output, structured_report_json, created_at
		 FROM task_events WHERE task_id = ? ORDER BY created_at ASC, rowid ASC`, id,
	)
	if err != nil {
		return nil, err
	}
	defer evRows.Close()

	t.Events = make([]TaskEvent, 0)
	for evRows.Next() {
		var ev TaskEvent
		if err := evRows.Scan(&ev.ID, &ev.TaskID, &ev.Iteration, &ev.EventType, &ev.Content, &ev.ToolName, &ev.ToolArgsJSON, &ev.ToolOutput, &ev.StructuredReportJSON, &ev.CreatedAt); err != nil {
			return nil, err
		}
		t.Events = append(t.Events, ev)
	}

	tcRows, err := s.db.Query(
		`SELECT id, task_id, iteration, tool_name, arguments_json, status, output, error, created_at, updated_at
		 FROM tool_calls WHERE task_id = ? ORDER BY created_at ASC, rowid ASC`, id,
	)
	if err != nil {
		return nil, err
	}
	defer tcRows.Close()

	t.ToolCalls = make([]ToolCall, 0)
	for tcRows.Next() {
		var tc ToolCall
		if err := tcRows.Scan(&tc.ID, &tc.TaskID, &tc.Iteration, &tc.ToolName, &tc.ArgumentsJSON, &tc.Status, &tc.Output, &tc.Error, &tc.CreatedAt, &tc.UpdatedAt); err != nil {
			return nil, err
		}
		t.ToolCalls = append(t.ToolCalls, tc)
	}

	return &t, nil
}

func (s *Store) ListTasks(workspaceID string) ([]Task, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var (
		rows *sql.Rows
		err  error
	)
	if workspaceID != "" {
		rows, err = s.db.Query(
			`SELECT id, workspace_id, prompt, status, final_answer, structured_report_json, max_iterations, current_iteration, error, created_at, updated_at
			 FROM tasks WHERE workspace_id = ? ORDER BY created_at DESC`, workspaceID,
		)
	} else {
		rows, err = s.db.Query(
			`SELECT id, workspace_id, prompt, status, final_answer, structured_report_json, max_iterations, current_iteration, error, created_at, updated_at
			 FROM tasks ORDER BY created_at DESC`,
		)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tasks := make([]Task, 0)
	for rows.Next() {
		var t Task
		if err := rows.Scan(
			&t.ID, &t.WorkspaceID, &t.Prompt, &t.Status, &t.FinalAnswer, &t.StructuredReportJSON,
			&t.MaxIterations, &t.CurrentIteration, &t.Error, &t.CreatedAt, &t.UpdatedAt,
		); err != nil {
			return nil, err
		}
		populateStructuredReport(&t)
		tasks = append(tasks, t)
	}
	return tasks, rows.Err()
}

func (s *Store) CreateTaskEvent(ev TaskEvent) (*TaskEvent, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if ev.ID == "" {
		ev.ID = uuid.NewString()
	}
	if ev.CreatedAt == "" {
		ev.CreatedAt = nowISO()
	}
	_, err := s.db.Exec(
		`INSERT INTO task_events (id, task_id, iteration, event_type, content, tool_name, tool_args_json, tool_output, structured_report_json, created_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		ev.ID, ev.TaskID, ev.Iteration, ev.EventType, ev.Content, ev.ToolName, ev.ToolArgsJSON, ev.ToolOutput, ev.StructuredReportJSON, ev.CreatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("insert task_event: %w", err)
	}
	return &ev, nil
}

func (s *Store) CreateToolCall(tc ToolCall) (*ToolCall, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if tc.ID == "" {
		tc.ID = uuid.NewString()
	}
	now := nowISO()
	if tc.CreatedAt == "" {
		tc.CreatedAt = now
	}
	tc.UpdatedAt = now
	_, err := s.db.Exec(
		`INSERT OR REPLACE INTO tool_calls (id, task_id, iteration, tool_name, arguments_json, status, output, error, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		tc.ID, tc.TaskID, tc.Iteration, tc.ToolName, tc.ArgumentsJSON, tc.Status, tc.Output, tc.Error, tc.CreatedAt, tc.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("insert tool_call: %w", err)
	}
	return &tc, nil
}

func (s *Store) UpdateToolCall(id, status, output, errMsg string) (*ToolCall, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := nowISO()
	_, err := s.db.Exec(
		`UPDATE tool_calls SET status = ?, output = ?, error = ?, updated_at = ? WHERE id = ?`,
		status, output, errMsg, now, id,
	)
	if err != nil {
		return nil, fmt.Errorf("update tool_call: %w", err)
	}

	var tc ToolCall
	err = s.db.QueryRow(
		`SELECT id, task_id, iteration, tool_name, arguments_json, status, output, error, created_at, updated_at
		 FROM tool_calls WHERE id = ?`, id,
	).Scan(&tc.ID, &tc.TaskID, &tc.Iteration, &tc.ToolName, &tc.ArgumentsJSON, &tc.Status, &tc.Output, &tc.Error, &tc.CreatedAt, &tc.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return &tc, nil
}
