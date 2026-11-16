package api

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"polyglot/backend/internal/pb"
	"polyglot/backend/internal/sse"
	"polyglot/backend/internal/store"
	"polyglot/backend/internal/tools"
	"polyglot/backend/internal/workspace"
)

type Server struct {
	store           *store.Store
	wsManager       *workspace.Manager
	executor        *tools.Executor
	sseHub          *sse.Hub
	grpcAddr        string
	toolCallbackURL string
	mux             *http.ServeMux

	dedupMu   sync.Mutex
	emittedEv map[string]bool
}

func NewServer(st *store.Store, wsMgr *workspace.Manager, grpcAddr, toolCallbackURL string) *Server {
	if grpcAddr == "" {
		grpcAddr = "127.0.0.1:50051"
	}
	if toolCallbackURL == "" {
		toolCallbackURL = "http://127.0.0.1:8080/internal/v1/tools/execute"
	}
	s := &Server{
		store:           st,
		wsManager:       wsMgr,
		executor:        tools.NewExecutor(wsMgr),
		sseHub:          sse.NewHub(),
		grpcAddr:        grpcAddr,
		toolCallbackURL: toolCallbackURL,
		mux:             http.NewServeMux(),
		emittedEv:       make(map[string]bool),
	}
	s.routes()
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// CORS headers for :5173 and general dev access
	origin := r.Header.Get("Origin")
	if origin != "" {
		w.Header().Set("Access-Control-Allow-Origin", origin)
	} else {
		w.Header().Set("Access-Control-Allow-Origin", "*")
	}
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept, Cache-Control, X-Requested-With")
	w.Header().Set("Access-Control-Allow-Credentials", "true")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	s.mux.ServeHTTP(w, r)
}

func (s *Server) routes() {
	s.mux.HandleFunc("/api/v1/health", s.handleHealth)
	s.mux.HandleFunc("/api/v1/workspaces", s.handleWorkspaces)
	s.mux.HandleFunc("/api/v1/workspaces/", s.handleWorkspaceSubroutes)
	s.mux.HandleFunc("/api/v1/tasks", s.handleTasks)
	s.mux.HandleFunc("/api/v1/tasks/", s.handleTaskSubroutes)
	s.mux.HandleFunc("/internal/v1/tools/execute", s.handleToolExecute)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{
		"error":  msg,
		"status": "error",
	})
}

func (s *Server) dialAgentRuntime(ctx context.Context) (*grpc.ClientConn, pb.AgentRuntimeServiceClient, error) {
	conn, err := grpc.NewClient(
		s.grpcAddr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return nil, nil, err
	}
	return conn, pb.NewAgentRuntimeServiceClient(conn), nil
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	runtimeStatus := map[string]any{
		"status":         "unavailable",
		"connected":      false,
		"llm_provider":   "unknown",
		"llm_model":      "unknown",
		"llm_configured": false,
		"version":        "1.0.0",
	}

	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	conn, client, err := s.dialAgentRuntime(ctx)
	if err == nil {
		defer conn.Close()
		resp, rpcErr := client.HealthCheck(ctx, &pb.HealthRequest{})
		if rpcErr == nil && resp != nil {
			runtimeStatus["status"] = resp.Status
			runtimeStatus["connected"] = true
			runtimeStatus["llm_provider"] = resp.LlmProvider
			runtimeStatus["llm_model"] = resp.LlmModel
			runtimeStatus["llm_configured"] = resp.LlmConfigured
			runtimeStatus["version"] = resp.Version
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "ok",
		"backend":        "healthy",
		"agent_runtime":  runtimeStatus,
		"llm_provider":   runtimeStatus["llm_provider"],
		"llm_model":      runtimeStatus["llm_model"],
		"llm_configured": runtimeStatus["llm_configured"],
	})
}

func (s *Server) handleWorkspaces(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		workspaces, err := s.store.ListWorkspaces()
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"workspaces": workspaces,
		})

	case http.MethodPost:
		var req struct {
			ID   string `json:"id"`
			Name string `json:"name"`
			Path string `json:"path"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		name := strings.TrimSpace(req.Name)
		if name == "" && strings.TrimSpace(req.Path) != "" {
			name = workspace.Slugify(req.Path)
		}
		if name == "" {
			writeError(w, http.StatusBadRequest, "workspace name is required")
			return
		}

		rootPath, err := s.wsManager.PrepareWorkspaceDir(name, req.Path)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				writeError(w, http.StatusForbidden, err.Error())
				return
			}
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		ws, err := s.store.CreateWorkspace(req.ID, name, rootPath)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, ws)

	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleWorkspaceSubroutes(w http.ResponseWriter, r *http.Request) {
	// Expected paths:
	// /api/v1/workspaces/{id}
	// /api/v1/workspaces/{id}/files
	// /api/v1/workspaces/{id}/file
	trimmed := strings.TrimPrefix(r.URL.Path, "/api/v1/workspaces/")
	parts := strings.Split(strings.Trim(trimmed, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "workspace id required")
		return
	}
	wsID := parts[0]
	ws, err := s.store.GetWorkspace(wsID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			writeError(w, http.StatusNotFound, "workspace not found")
			return
		}
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	if len(parts) == 1 {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		writeJSON(w, http.StatusOK, ws)
		return
	}

	action := parts[1]
	switch action {
	case "files":
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		relPath := r.URL.Query().Get("path")
		if relPath == "" {
			relPath = "."
		}
		files, err := s.wsManager.ListFiles(ws.RootPath, relPath)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				writeError(w, http.StatusForbidden, err.Error())
				return
			}
			if errors.Is(err, workspace.ErrNotFound) {
				writeError(w, http.StatusNotFound, err.Error())
				return
			}
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"workspace_id": ws.ID,
			"path":         relPath,
			"files":        files,
		})

	case "file":
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		relPath := r.URL.Query().Get("path")
		if strings.TrimSpace(relPath) == "" {
			writeError(w, http.StatusBadRequest, "query parameter 'path' is required")
			return
		}
		content, cleanRel, size, err := s.wsManager.ReadFile(ws.RootPath, relPath)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				writeError(w, http.StatusForbidden, err.Error())
				return
			}
			if errors.Is(err, workspace.ErrNotFound) {
				writeError(w, http.StatusNotFound, err.Error())
				return
			}
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"workspace_id": ws.ID,
			"path":         cleanRel,
			"content":      content,
			"size":         size,
		})

	default:
		writeError(w, http.StatusNotFound, "unknown workspace endpoint")
	}
}

func (s *Server) handleTasks(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		wsID := r.URL.Query().Get("workspace_id")
		tasks, err := s.store.ListTasks(wsID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"tasks": tasks,
		})

	case http.MethodPost:
		var req struct {
			WorkspaceID   string `json:"workspace_id"`
			Prompt        string `json:"prompt"`
			MaxIterations int    `json:"max_iterations"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		prompt := strings.TrimSpace(req.Prompt)
		if prompt == "" {
			writeError(w, http.StatusBadRequest, "prompt is required")
			return
		}

		wsID := strings.TrimSpace(req.WorkspaceID)
		var ws *store.Workspace
		if wsID == "" {
			workspaces, err := s.store.ListWorkspaces()
			if err != nil || len(workspaces) == 0 {
				writeError(w, http.StatusBadRequest, "workspace_id is required")
				return
			}
			ws = &workspaces[0]
		} else {
			var err error
			ws, err = s.store.GetWorkspace(wsID)
			if err != nil {
				if errors.Is(err, sql.ErrNoRows) {
					writeError(w, http.StatusNotFound, "workspace not found")
					return
				}
				writeError(w, http.StatusInternalServerError, err.Error())
				return
			}
		}

		maxIter := req.MaxIterations
		if maxIter <= 0 {
			maxIter = 8
		}
		if maxIter > 12 {
			maxIter = 12
		}

		task, err := s.store.CreateTask(ws.ID, prompt, maxIter)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}

		// Launch asynchronous gRPC task execution
		go s.executeTaskAsync(task, ws)

		writeJSON(w, http.StatusCreated, task)

	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleTaskSubroutes(w http.ResponseWriter, r *http.Request) {
	trimmed := strings.TrimPrefix(r.URL.Path, "/api/v1/tasks/")
	parts := strings.Split(strings.Trim(trimmed, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "task id required")
		return
	}
	taskID := parts[0]

	if len(parts) == 1 {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		task, err := s.store.GetTask(taskID)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				writeError(w, http.StatusNotFound, "task not found")
				return
			}
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, task)
		return
	}

	if len(parts) == 2 && parts[1] == "stream" {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		s.handleTaskStream(w, r, taskID)
		return
	}

	writeError(w, http.StatusNotFound, "unknown task endpoint")
}

func (s *Server) handleTaskStream(w http.ResponseWriter, r *http.Request, taskID string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming not supported")
		return
	}

	// Subscribe first so no live events are missed while reading historical events
	ch, unsubscribe := s.sseHub.Subscribe(taskID)
	defer unsubscribe()

	task, err := s.store.GetTask(taskID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			writeError(w, http.StatusNotFound, "task not found")
			return
		}
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	seen := make(map[string]bool)
	sendSSE := func(ev store.TaskEvent) {
		if seen[ev.ID] {
			return
		}
		seen[ev.ID] = true
		data, err := json.Marshal(ev)
		if err != nil {
			return
		}
		_, _ = fmt.Fprintf(w, "data: %s\n\n", data)
		flusher.Flush()
	}

	for _, ev := range task.Events {
		sendSSE(ev)
	}

	// If task was already in a terminal state before streaming started, exit cleanly
	if task.Status == "COMPLETED" || task.Status == "FAILED" {
		return
	}

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-ch:
			if !ok {
				return
			}
			sendSSE(ev)
			if ev.EventType == "FINAL_ANSWER" || (ev.EventType == "STATUS" && (ev.Content == "COMPLETED" || ev.Content == "FAILED")) {
				return
			}
		}
	}
}

func (s *Server) recordAndPublishEvent(ev store.TaskEvent) (*store.TaskEvent, error) {
	saved, err := s.store.CreateTaskEvent(ev)
	if err != nil {
		return nil, err
	}
	if saved.TaskID != "" {
		s.sseHub.Publish(saved.TaskID, *saved)
	}
	return saved, nil
}

func (s *Server) markOrCheckDedup(taskID string, iteration int, toolName, eventType string) bool {
	if taskID == "" {
		return false
	}
	key := fmt.Sprintf("%s:%d:%s:%s", taskID, iteration, toolName, eventType)
	s.dedupMu.Lock()
	defer s.dedupMu.Unlock()
	if s.emittedEv[key] {
		return true
	}
	s.emittedEv[key] = true
	return false
}

func (s *Server) handleToolExecute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req tools.ExecuteRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"call_id":   "",
			"tool_name": "",
			"status":    "DENIED",
			"output":    "",
			"error":     "invalid JSON request body: " + err.Error(),
		})
		return
	}

	if req.CallID == "" {
		req.CallID = uuid.NewString()
	}
	if req.Iteration <= 0 {
		req.Iteration = 1
	}

	// Resolve workspace root
	var ws *store.Workspace
	if strings.TrimSpace(req.WorkspaceID) != "" {
		wFound, err := s.store.GetWorkspace(req.WorkspaceID)
		if err == nil {
			ws = wFound
		}
	}
	if ws == nil && strings.TrimSpace(req.TaskID) != "" {
		if tFound, err := s.store.GetTask(req.TaskID); err == nil {
			if wFound, err := s.store.GetWorkspace(tFound.WorkspaceID); err == nil {
				ws = wFound
			}
		}
	}
	if ws == nil {
		// Fallback to first registered workspace (e.g. calculator-demo) for standalone security verification tests
		if list, err := s.store.ListWorkspaces(); err == nil && len(list) > 0 {
			ws = &list[0]
		}
	}
	if ws == nil {
		writeJSON(w, http.StatusNotFound, map[string]any{
			"call_id":   req.CallID,
			"tool_name": req.ToolName,
			"status":    "DENIED",
			"output":    "",
			"error":     "workspace not found",
		})
		return
	}

	if req.TaskID != "" {
		_ = s.store.UpdateTaskIteration(req.TaskID, req.Iteration)
	}

	// Execute tool inside workspace security boundary
	res := s.executor.Execute(r.Context(), ws.RootPath, req)

	// Persist tool_call in SQLite
	_, _ = s.store.CreateToolCall(store.ToolCall{
		ID:            req.CallID,
		TaskID:        req.TaskID,
		Iteration:     req.Iteration,
		ToolName:      req.ToolName,
		ArgumentsJSON: res.ArgumentsJSON,
		Status:        res.Status,
		Output:        res.Output,
		Error:         res.Error,
	})

	// Emit TOOL_CALL and TOOL_RESULT events if associated with a task and not already emitted
	if req.TaskID != "" {
		if !s.markOrCheckDedup(req.TaskID, req.Iteration, req.ToolName, "TOOL_CALL") {
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:       req.TaskID,
				Iteration:    req.Iteration,
				EventType:    "TOOL_CALL",
				Content:      fmt.Sprintf("Invoking tool %s", req.ToolName),
				ToolName:     req.ToolName,
				ToolArgsJSON: res.ArgumentsJSON,
			})
		}

		resultContent := res.Output
		if res.Error != "" {
			if resultContent != "" {
				resultContent = resultContent + "\nError: " + res.Error
			} else {
				resultContent = "Error: " + res.Error
			}
		}
		if !s.markOrCheckDedup(req.TaskID, req.Iteration, req.ToolName, "TOOL_RESULT") {
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:       req.TaskID,
				Iteration:    req.Iteration,
				EventType:    "TOOL_RESULT",
				Content:      resultContent,
				ToolName:     req.ToolName,
				ToolArgsJSON: res.ArgumentsJSON,
				ToolOutput:   resultContent,
			})
		}
	}

	httpStatus := res.HTTPStatus
	if httpStatus == 0 {
		httpStatus = http.StatusOK
	}

	writeJSON(w, httpStatus, map[string]any{
		"call_id":   res.CallID,
		"tool_name": res.ToolName,
		"status":    res.Status,
		"output":    res.Output,
		"error":     res.Error,
	})
}

func (s *Server) executeTaskAsync(task *store.Task, ws *store.Workspace) {
	_ = s.store.UpdateTaskStatus(task.ID, "RUNNING", 1, "", "", "")
	_, _ = s.recordAndPublishEvent(store.TaskEvent{
		TaskID:    task.ID,
		Iteration: 0,
		EventType: "STATUS",
		Content:   "RUNNING",
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	conn, client, err := s.dialAgentRuntime(ctx)
	if err != nil {
		errMsg := fmt.Sprintf("failed to connect to agent-runtime gRPC (%s): %v", s.grpcAddr, err)
		s.failTask(task.ID, 0, errMsg)
		return
	}
	defer conn.Close()

	stream, err := client.ExecuteTask(ctx, &pb.TaskRequest{
		TaskId:          task.ID,
		WorkspaceId:     ws.ID,
		WorkspaceRoot:   ws.RootPath,
		Prompt:          task.Prompt,
		MaxIterations:   int32(task.MaxIterations),
		ToolCallbackUrl: s.toolCallbackURL,
	})
	if err != nil {
		errMsg := fmt.Sprintf("ExecuteTask RPC failed: %v", err)
		s.failTask(task.ID, 0, errMsg)
		return
	}

	var (
		completed   bool
		failed      bool
		lastIter    int
		finalAnswer string
		reportJSON  string
		lastErr     string
	)

	for {
		ev, recvErr := stream.Recv()
		if errors.Is(recvErr, io.EOF) {
			break
		}
		if recvErr != nil {
			lastErr = fmt.Sprintf("stream error: %v", recvErr)
			failed = true
			break
		}

		iter := int(ev.Iteration)
		if iter > lastIter {
			lastIter = iter
			_ = s.store.UpdateTaskIteration(task.ID, iter)
		}

		evType := strings.ToUpper(strings.TrimSpace(ev.EventType))
		switch evType {
		case "TOOL_CALL":
			if s.markOrCheckDedup(task.ID, iter, ev.ToolName, "TOOL_CALL") {
				continue
			}
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:       task.ID,
				Iteration:    iter,
				EventType:    "TOOL_CALL",
				Content:      ev.Content,
				ToolName:     ev.ToolName,
				ToolArgsJSON: ev.ToolArgsJson,
			})

		case "TOOL_RESULT":
			if s.markOrCheckDedup(task.ID, iter, ev.ToolName, "TOOL_RESULT") {
				continue
			}
			out := ev.ToolOutput
			if out == "" {
				out = ev.Content
			}
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:       task.ID,
				Iteration:    iter,
				EventType:    "TOOL_RESULT",
				Content:      ev.Content,
				ToolName:     ev.ToolName,
				ToolArgsJSON: ev.ToolArgsJson,
				ToolOutput:   out,
			})

		case "FINAL_ANSWER":
			completed = true
			finalAnswer = ev.Content
			reportJSON = ev.StructuredReportJson
			_ = s.store.UpdateTaskStatus(task.ID, "COMPLETED", lastIter, finalAnswer, reportJSON, "")
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:               task.ID,
				Iteration:            iter,
				EventType:            "FINAL_ANSWER",
				Content:              ev.Content,
				StructuredReportJSON: ev.StructuredReportJson,
			})

		case "ERROR":
			lastErr = ev.Content
			failed = true
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:    task.ID,
				Iteration: iter,
				EventType: "ERROR",
				Content:   ev.Content,
			})

		default:
			_, _ = s.recordAndPublishEvent(store.TaskEvent{
				TaskID:               task.ID,
				Iteration:            iter,
				EventType:            evType,
				Content:              ev.Content,
				ToolName:             ev.ToolName,
				ToolArgsJSON:         ev.ToolArgsJson,
				ToolOutput:           ev.ToolOutput,
				StructuredReportJSON: ev.StructuredReportJson,
			})
		}
	}

	if completed {
		_ = s.store.UpdateTaskStatus(task.ID, "COMPLETED", lastIter, finalAnswer, reportJSON, "")
		_, _ = s.recordAndPublishEvent(store.TaskEvent{
			TaskID:    task.ID,
			Iteration: lastIter,
			EventType: "STATUS",
			Content:   "COMPLETED",
		})
		return
	}

	if failed {
		if lastErr == "" {
			lastErr = "Task execution failed"
		}
		s.failTask(task.ID, lastIter, lastErr)
		return
	}

	// Stream ended without FINAL_ANSWER or explicit ERROR
	s.failTask(task.ID, lastIter, "Agent stream ended without final answer")
}

func (s *Server) failTask(taskID string, iter int, errMsg string) {
	log.Printf("[task %s] failed: %s", taskID, errMsg)
	_ = s.store.UpdateTaskStatus(taskID, "FAILED", iter, "", "", errMsg)
	_, _ = s.recordAndPublishEvent(store.TaskEvent{
		TaskID:    taskID,
		Iteration: iter,
		EventType: "ERROR",
		Content:   errMsg,
	})
	_, _ = s.recordAndPublishEvent(store.TaskEvent{
		TaskID:    taskID,
		Iteration: iter,
		EventType: "STATUS",
		Content:   "FAILED",
	})
}
