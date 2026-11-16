package main

import (
	"log"
	"net/http"
	"os"
	"path/filepath"

	"polyglot/backend/internal/api"
	"polyglot/backend/internal/store"
	"polyglot/backend/internal/workspace"
)

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	port := getenv("PORT", "8080")
	dbPath := getenv("DB_PATH", "polyglot.db")
	workspacesDir := getenv("WORKSPACES_DIR", "")
	if workspacesDir == "" {
		// Default to repo-root workspaces/ directory
		cwd, _ := os.Getwd()
		if filepath.Base(cwd) == "backend" {
			workspacesDir = filepath.Join(filepath.Dir(cwd), "workspaces")
		} else {
			workspacesDir = filepath.Join(cwd, "workspaces")
		}
	}
	grpcAddr := getenv("AGENT_RUNTIME_ADDR", "127.0.0.1:50051")
	toolCallbackURL := getenv("TOOL_CALLBACK_URL", "http://127.0.0.1:"+port+"/internal/v1/tools/execute")

	wsMgr, err := workspace.NewManager(workspacesDir)
	if err != nil {
		log.Fatalf("Failed to initialize workspace manager: %v", err)
	}

	st, err := store.NewSQLiteStore(dbPath)
	if err != nil {
		log.Fatalf("Failed to initialize SQLite store: %v", err)
	}
	defer st.Close()

	// Seed default calculator-demo workspace if none exists
	existing, err := st.ListWorkspaces()
	if err == nil && len(existing) == 0 {
		demoRoot, err := wsMgr.PrepareWorkspaceDir("calculator-demo", "calculator-demo")
		if err == nil {
			_, _ = st.CreateWorkspace("calculator-demo", "calculator-demo", demoRoot)
			log.Printf("Seeded default workspace calculator-demo at %s", demoRoot)
		}
	}

	srv := api.NewServer(st, wsMgr, grpcAddr, toolCallbackURL)

	addr := ":" + port
	log.Printf("Go Backend listening on %s (workspaces=%s, agent-runtime=%s)", addr, wsMgr.BaseDir, grpcAddr)
	if err := http.ListenAndServe(addr, srv); err != nil {
		log.Fatalf("HTTP server error: %v", err)
	}
}
