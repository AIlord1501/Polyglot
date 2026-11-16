package tools

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"polyglot/backend/internal/workspace"
)

var (
	ErrUnauthorizedTool    = errors.New("tool authorization denied: unknown or unapproved tool")
	ErrUnauthorizedCommand = errors.New("tool authorization denied: command not in allowlist or contains forbidden flags/injection")
	ErrMalformedArguments  = errors.New("malformed tool arguments")
)

const CommandTimeout = 20 * time.Second

type ExecuteRequest struct {
	TaskID      string          `json:"task_id"`
	WorkspaceID string          `json:"workspace_id"`
	CallID      string          `json:"call_id"`
	Iteration   int             `json:"iteration"`
	ToolName    string          `json:"tool_name"`
	Arguments   json.RawMessage `json:"arguments"`
}

type ExecuteResult struct {
	CallID        string `json:"call_id"`
	ToolName      string `json:"tool_name"`
	ArgumentsJSON string `json:"arguments_json"`
	Status        string `json:"status"` // COMPLETED, DENIED, FAILED
	Output        string `json:"output"`
	Error         string `json:"error"`
	HTTPStatus    int    `json:"-"`
}

type Executor struct {
	wsManager *workspace.Manager
}

func NewExecutor(wsManager *workspace.Manager) *Executor {
	return &Executor{wsManager: wsManager}
}

// parseArgsMap accepts either a JSON object or a JSON-encoded string containing a JSON object.
func parseArgsMap(raw json.RawMessage) (map[string]any, string, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return map[string]any{}, "{}", nil
	}

	// If passed as a JSON string, unquote first
	if trimmed[0] == '"' {
		var str string
		if err := json.Unmarshal(trimmed, &str); err != nil {
			return nil, string(raw), fmt.Errorf("%w: invalid JSON string: %v", ErrMalformedArguments, err)
		}
		trimmed = bytes.TrimSpace([]byte(str))
		if len(trimmed) == 0 {
			return map[string]any{}, "{}", nil
		}
	}

	if trimmed[0] != '{' {
		return nil, string(raw), fmt.Errorf("%w: arguments must be a JSON object", ErrMalformedArguments)
	}

	var m map[string]any
	if err := json.Unmarshal(trimmed, &m); err != nil {
		return nil, string(raw), fmt.Errorf("%w: %v", ErrMalformedArguments, err)
	}
	normalized, _ := json.Marshal(m)
	return m, string(normalized), nil
}

func getStringField(m map[string]any, key string, required bool, defaultVal string) (string, error) {
	val, exists := m[key]
	if !exists || val == nil {
		if required {
			return "", fmt.Errorf("%w: missing required field %q", ErrMalformedArguments, key)
		}
		return defaultVal, nil
	}
	s, ok := val.(string)
	if !ok {
		return "", fmt.Errorf("%w: field %q must be a string", ErrMalformedArguments, key)
	}
	if required && strings.TrimSpace(s) == "" {
		return "", fmt.Errorf("%w: field %q cannot be empty", ErrMalformedArguments, key)
	}
	return s, nil
}

func (e *Executor) Execute(ctx context.Context, workspaceRoot string, req ExecuteRequest) ExecuteResult {
	res := ExecuteResult{
		CallID:        req.CallID,
		ToolName:      req.ToolName,
		ArgumentsJSON: "{}",
	}

	argsMap, normJSON, err := parseArgsMap(req.Arguments)
	res.ArgumentsJSON = normJSON
	if err != nil {
		res.Status = "DENIED"
		res.Error = err.Error()
		res.HTTPStatus = 400
		return res
	}

	switch req.ToolName {
	case "list_files":
		pathArg, err := getStringField(argsMap, "path", false, ".")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		if strings.TrimSpace(pathArg) == "" {
			pathArg = "."
		}
		entries, err := e.wsManager.ListFiles(workspaceRoot, pathArg)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				res.Status = "DENIED"
				res.Error = err.Error()
				res.HTTPStatus = 403
				return res
			}
			res.Status = "FAILED"
			res.Error = err.Error()
			res.HTTPStatus = 200
			return res
		}
		outBytes, _ := json.MarshalIndent(entries, "", "  ")
		res.Status = "COMPLETED"
		res.Output = string(outBytes)
		res.HTTPStatus = 200
		return res

	case "read_file":
		pathArg, err := getStringField(argsMap, "path", true, "")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		content, _, _, err := e.wsManager.ReadFile(workspaceRoot, pathArg)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				res.Status = "DENIED"
				res.Error = err.Error()
				res.HTTPStatus = 403
				return res
			}
			res.Status = "FAILED"
			res.Error = err.Error()
			res.HTTPStatus = 200
			return res
		}
		res.Status = "COMPLETED"
		res.Output = content
		res.HTTPStatus = 200
		return res

	case "write_file":
		pathArg, err := getStringField(argsMap, "path", true, "")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		rawContent, exists := argsMap["content"]
		if !exists || rawContent == nil {
			res.Status = "DENIED"
			res.Error = fmt.Sprintf("%v: missing required field \"content\"", ErrMalformedArguments)
			res.HTTPStatus = 400
			return res
		}
		contentStr, ok := rawContent.(string)
		if !ok {
			res.Status = "DENIED"
			res.Error = fmt.Sprintf("%v: field \"content\" must be a string", ErrMalformedArguments)
			res.HTTPStatus = 400
			return res
		}
		cleanRel, nBytes, err := e.wsManager.WriteFile(workspaceRoot, pathArg, contentStr)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				res.Status = "DENIED"
				res.Error = err.Error()
				res.HTTPStatus = 403
				return res
			}
			res.Status = "FAILED"
			res.Error = err.Error()
			res.HTTPStatus = 200
			return res
		}
		res.Status = "COMPLETED"
		res.Output = fmt.Sprintf("Successfully wrote %d bytes to %s", nBytes, cleanRel)
		res.HTTPStatus = 200
		return res

	case "search_files":
		queryArg, err := getStringField(argsMap, "query", true, "")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		pathArg, err := getStringField(argsMap, "path", false, ".")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		if strings.TrimSpace(pathArg) == "" {
			pathArg = "."
		}
		out, err := e.searchFiles(workspaceRoot, pathArg, queryArg)
		if err != nil {
			if errors.Is(err, workspace.ErrPathTraversal) {
				res.Status = "DENIED"
				res.Error = err.Error()
				res.HTTPStatus = 403
				return res
			}
			res.Status = "FAILED"
			res.Error = err.Error()
			res.HTTPStatus = 200
			return res
		}
		res.Status = "COMPLETED"
		res.Output = out
		res.HTTPStatus = 200
		return res

	case "run_command":
		cmdStr, err := getStringField(argsMap, "command", true, "")
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 400
			return res
		}
		var argsList []string
		if rawArgs, exists := argsMap["args"]; exists && rawArgs != nil {
			switch v := rawArgs.(type) {
			case []any:
				for _, item := range v {
					s, ok := item.(string)
					if !ok {
						res.Status = "DENIED"
						res.Error = fmt.Sprintf("%v: all elements of \"args\" must be strings", ErrMalformedArguments)
						res.HTTPStatus = 400
						return res
					}
					argsList = append(argsList, s)
				}
			case string:
				if strings.TrimSpace(v) != "" {
					if containsShellInjection(v) {
						res.Status = "DENIED"
						res.Error = fmt.Sprintf("%v: shell injection characters detected in args", ErrUnauthorizedCommand)
						res.HTTPStatus = 403
						return res
					}
					argsList = strings.Fields(v)
				}
			default:
				res.Status = "DENIED"
				res.Error = fmt.Sprintf("%v: \"args\" must be an array of strings", ErrMalformedArguments)
				res.HTTPStatus = 400
				return res
			}
		}

		bin, finalArgs, err := ValidateAndNormalizeCommand(workspaceRoot, cmdStr, argsList)
		if err != nil {
			res.Status = "DENIED"
			res.Error = err.Error()
			res.HTTPStatus = 403
			return res
		}

		output, exitErr := runSandboxedCommand(ctx, workspaceRoot, bin, finalArgs)
		res.Output = output
		if exitErr != nil {
			res.Status = "FAILED"
			res.Error = exitErr.Error()
			res.HTTPStatus = 200
			return res
		}
		res.Status = "COMPLETED"
		res.HTTPStatus = 200
		return res

	default:
		res.Status = "DENIED"
		res.Error = fmt.Sprintf("%v: %q", ErrUnauthorizedTool, req.ToolName)
		res.HTTPStatus = 403
		return res
	}
}

func containsShellInjection(s string) bool {
	forbidden := []string{";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r", "&"}
	for _, tok := range forbidden {
		if strings.Contains(s, tok) {
			return true
		}
	}
	return false
}

// ValidateAndNormalizeCommand enforces the strict developer command allowlist and blocks shell injection & outside-workspace paths.
func ValidateAndNormalizeCommand(workspaceRoot, command string, args []string) (string, []string, error) {
	if containsShellInjection(command) {
		return "", nil, fmt.Errorf("%w: shell injection characters in command %q", ErrUnauthorizedCommand, command)
	}
	for _, a := range args {
		if containsShellInjection(a) {
			return "", nil, fmt.Errorf("%w: shell injection characters in argument %q", ErrUnauthorizedCommand, a)
		}
	}

	cmdParts := strings.Fields(strings.TrimSpace(command))
	if len(cmdParts) == 0 {
		return "", nil, fmt.Errorf("%w: empty command", ErrMalformedArguments)
	}

	baseBin := cmdParts[0]
	combinedArgs := append([]string{}, cmdParts[1:]...)
	combinedArgs = append(combinedArgs, args...)

	// Reject any path separators in binary name (e.g., ../evil or C:\Windows\System32\cmd.exe)
	if strings.ContainsAny(baseBin, `/\:`) {
		return "", nil, fmt.Errorf("%w: binary path %q is not allowed", ErrUnauthorizedCommand, baseBin)
	}

	lowerBin := strings.ToLower(strings.TrimSuffix(baseBin, ".exe"))

	switch lowerBin {
	case "python", "python3":
		// On Windows, prefer "python" if both point to Python 3, or keep lowerBin
		baseBin = lowerBin
	case "pytest":
		baseBin = "pytest"
	case "go":
		if len(combinedArgs) == 0 || combinedArgs[0] != "test" {
			return "", nil, fmt.Errorf("%w: only 'go test' is permitted for go", ErrUnauthorizedCommand)
		}
		baseBin = "go"
	case "npm":
		if len(combinedArgs) == 0 || combinedArgs[0] != "test" {
			return "", nil, fmt.Errorf("%w: only 'npm test' is permitted for npm", ErrUnauthorizedCommand)
		}
		baseBin = "npm"
	default:
		return "", nil, fmt.Errorf("%w: binary %q is not in allowlist (allowed: python, python3, pytest, go test, npm test)", ErrUnauthorizedCommand, baseBin)
	}

	// Inspect arguments for outside-workspace flags or traversal
	for i, arg := range combinedArgs {
		trimmedArg := strings.TrimSpace(arg)
		if trimmedArg == "" {
			continue
		}
		// Reject inline script execution (-c) if it attempts dangerous operations or outside paths
		if (lowerBin == "python" || lowerBin == "python3") && trimmedArg == "-c" {
			return "", nil, fmt.Errorf("%w: inline '-c' code execution is forbidden; write a file inside the workspace instead", ErrUnauthorizedCommand)
		}
		// Reject any traversal segment ".."
		for _, seg := range strings.FieldsFunc(trimmedArg, func(r rune) bool { return r == '/' || r == '\\' }) {
			if seg == ".." {
				return "", nil, fmt.Errorf("%w: path traversal '..' in argument %q", ErrUnauthorizedCommand, arg)
			}
		}
		// Reject volume names (C:\...) or rooted paths (/etc/..., \Windows\...) in non-flag arguments or flag values
		if filepath.VolumeName(trimmedArg) != "" || strings.HasPrefix(trimmedArg, "/") || strings.HasPrefix(trimmedArg, `\`) {
			return "", nil, fmt.Errorf("%w: outside-workspace or absolute path in argument %q", ErrUnauthorizedCommand, arg)
		}
		if strings.Contains(trimmedArg, "=") {
			parts := strings.SplitN(trimmedArg, "=", 2)
			val := parts[1]
			if filepath.VolumeName(val) != "" || strings.HasPrefix(val, "/") || strings.HasPrefix(val, `\`) || strings.Contains(val, "..") {
				return "", nil, fmt.Errorf("%w: outside-workspace path in flag %q", ErrUnauthorizedCommand, arg)
			}
		}
		_ = i
	}

	// Ensure workspaceRoot itself is valid
	if _, _, err := workspace.ResolveSafePath(workspaceRoot, "."); err != nil {
		return "", nil, err
	}

	return baseBin, combinedArgs, nil
}

func runSandboxedCommand(parentCtx context.Context, workspaceRoot, bin string, args []string) (string, error) {
	ctx, cancel := context.WithTimeout(parentCtx, CommandTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.Dir = workspaceRoot

	var combined bytes.Buffer
	cmd.Stdout = &combined
	cmd.Stderr = &combined

	err := cmd.Run()
	outStr := combined.String()
	if ctx.Err() == context.DeadlineExceeded {
		return outStr, fmt.Errorf("command timed out after %s", CommandTimeout)
	}
	return outStr, err
}

func (e *Executor) searchFiles(workspaceRoot, relPath, query string) (string, error) {
	absTarget, _, err := workspace.ResolveSafePath(workspaceRoot, relPath)
	if err != nil {
		return "", err
	}
	absRoot, _, err := workspace.ResolveSafePath(workspaceRoot, ".")
	if err != nil {
		return "", err
	}

	lowerQuery := strings.ToLower(query)
	var matches []string

	err = filepath.WalkDir(absTarget, func(p string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			if name == ".git" || name == "__pycache__" || name == ".pytest_cache" || name == "node_modules" {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(absRoot, p)
		if err != nil {
			return nil
		}
		relSlash := filepath.ToSlash(rel)

		f, err := os.Open(p)
		if err != nil {
			return nil
		}
		defer f.Close()

		scanner := bufio.NewScanner(f)
		lineNum := 0
		for scanner.Scan() {
			lineNum++
			line := scanner.Text()
			if strings.Contains(strings.ToLower(line), lowerQuery) {
				matches = append(matches, fmt.Sprintf("%s:%d: %s", relSlash, lineNum, line))
				if len(matches) >= 200 {
					return fs.SkipAll
				}
			}
		}
		return nil
	})
	if err != nil && !errors.Is(err, fs.SkipAll) {
		return "", err
	}
	if len(matches) == 0 {
		return "No matches found.", nil
	}
	return strings.Join(matches, "\n"), nil
}
