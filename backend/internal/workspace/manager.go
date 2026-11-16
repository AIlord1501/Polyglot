package workspace

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var (
	ErrPathTraversal = errors.New("security violation: path traversal or path outside workspace root is forbidden")
	ErrNotFound      = errors.New("file or directory not found")
)

type FileEntry struct {
	Name      string `json:"name"`
	Path      string `json:"path"`
	IsDir     bool   `json:"is_dir"`
	Size      int64  `json:"size"`
	UpdatedAt string `json:"updated_at"`
}

type Manager struct {
	BaseDir string
}

func NewManager(baseDir string) (*Manager, error) {
	absBase, err := filepath.Abs(baseDir)
	if err != nil {
		return nil, fmt.Errorf("resolve workspaces base dir: %w", err)
	}
	if err := os.MkdirAll(absBase, 0755); err != nil {
		return nil, fmt.Errorf("create workspaces base dir: %w", err)
	}
	return &Manager{BaseDir: absBase}, nil
}

var nonSlugRegex = regexp.MustCompile(`[^a-z0-9\-_]+`)

func Slugify(name string) string {
	s := strings.ToLower(strings.TrimSpace(name))
	s = strings.ReplaceAll(s, " ", "-")
	s = nonSlugRegex.ReplaceAllString(s, "")
	s = strings.Trim(s, "-_")
	if s == "" {
		s = "workspace"
	}
	return s
}

// PrepareWorkspaceDir resolves and creates a workspace directory under BaseDir.
func (m *Manager) PrepareWorkspaceDir(name, requestedPath string) (string, error) {
	slug := Slugify(name)
	var target string
	req := strings.TrimSpace(requestedPath)
	if req == "" {
		target = filepath.Join(m.BaseDir, slug)
	} else {
		// Reject any path traversal in workspace creation as well
		for _, part := range strings.FieldsFunc(req, func(r rune) bool { return r == '/' || r == '\\' }) {
			if part == ".." {
				return "", ErrPathTraversal
			}
		}
		if filepath.IsAbs(req) {
			absReq, err := filepath.Abs(req)
			if err != nil {
				return "", err
			}
			// Allow if it is inside BaseDir or inside a safe temp dir during tests, otherwise place under BaseDir
			if isWithinRoot(m.BaseDir, absReq) {
				target = absReq
			} else {
				return "", ErrPathTraversal
			}
		} else {
			// Strip leading "./" or "workspaces/" if user passed "workspaces/foo"
			cleaned := filepath.Clean(req)
			cleanedSlash := filepath.ToSlash(cleaned)
			cleanedSlash = strings.TrimPrefix(cleanedSlash, "./")
			cleanedSlash = strings.TrimPrefix(cleanedSlash, "workspaces/")
			absTarget, _, err := ResolveSafePath(m.BaseDir, cleanedSlash)
			if err != nil {
				return "", err
			}
			target = absTarget
		}
	}

	if err := os.MkdirAll(target, 0755); err != nil {
		return "", fmt.Errorf("mkdir workspace: %w", err)
	}
	return target, nil
}

// ResolveSafePath strictly validates that targetPath is inside workspaceRoot.
// Returns (absoluteResolvedPath, normalizedRelativePathWithForwardSlashes, error).
func ResolveSafePath(workspaceRoot, targetPath string) (string, string, error) {
	if strings.ContainsRune(targetPath, '\x00') {
		return "", "", ErrPathTraversal
	}

	absRoot, err := filepath.Abs(workspaceRoot)
	if err != nil {
		return "", "", fmt.Errorf("invalid workspace root: %w", err)
	}
	evalRoot := evalExistingPrefix(absRoot)

	trimmed := strings.TrimSpace(targetPath)
	if trimmed == "" || trimmed == "." {
		return evalRoot, ".", nil
	}

	// Explicitly check raw path segments for ".." before any normalization
	parts := strings.FieldsFunc(trimmed, func(r rune) bool { return r == '/' || r == '\\' })
	for _, p := range parts {
		if p == ".." {
			return "", "", ErrPathTraversal
		}
	}

	var candidateAbs string
	if filepath.IsAbs(trimmed) || filepath.VolumeName(trimmed) != "" || strings.HasPrefix(trimmed, "/") || strings.HasPrefix(trimmed, `\`) {
		// If it starts with '/' or '\' without drive letter on Windows (e.g., "/etc/passwd" or "\Windows\win.ini"),
		// reject immediately as an outside absolute path.
		if filepath.VolumeName(trimmed) == "" && (strings.HasPrefix(trimmed, "/") || strings.HasPrefix(trimmed, `\`)) {
			return "", "", ErrPathTraversal
		}
		cAbs, err := filepath.Abs(trimmed)
		if err != nil {
			return "", "", ErrPathTraversal
		}
		candidateAbs = cAbs
	} else {
		candidateAbs = filepath.Join(evalRoot, filepath.FromSlash(trimmed))
	}

	candidateAbs = filepath.Clean(candidateAbs)
	evalCandidate := evalExistingPrefix(candidateAbs)

	if !isWithinRoot(evalRoot, evalCandidate) {
		return "", "", ErrPathTraversal
	}

	rel, err := filepath.Rel(evalRoot, evalCandidate)
	if err != nil {
		return "", "", ErrPathTraversal
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return "", "", ErrPathTraversal
	}

	return evalCandidate, filepath.ToSlash(rel), nil
}

func isWithinRoot(root, target string) bool {
	cleanRoot := filepath.Clean(root)
	cleanTarget := filepath.Clean(target)

	// Normalize case on Windows for drive letters / paths
	rel, err := filepath.Rel(strings.ToLower(cleanRoot), strings.ToLower(cleanTarget))
	if err != nil {
		return false
	}
	if rel == "." {
		return true
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return false
	}
	return true
}

// evalExistingPrefix resolves symlinks for the longest existing ancestor of path p,
// then appends any not-yet-created child segments.
func evalExistingPrefix(p string) string {
	cleaned := filepath.Clean(p)
	cur := cleaned
	var tail []string

	for {
		resolved, err := filepath.EvalSymlinks(cur)
		if err == nil {
			for i := len(tail) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, tail[i])
			}
			return filepath.Clean(resolved)
		}
		parent := filepath.Dir(cur)
		if parent == cur {
			return cleaned
		}
		tail = append(tail, filepath.Base(cur))
		cur = parent
	}
}

func shouldIgnoreDir(name string) bool {
	switch name {
	case ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv":
		return true
	default:
		return false
	}
}

func (m *Manager) ListFiles(workspaceRoot, relPath string) ([]FileEntry, error) {
	absTarget, _, err := ResolveSafePath(workspaceRoot, relPath)
	if err != nil {
		return nil, err
	}
	absRoot, _, err := ResolveSafePath(workspaceRoot, ".")
	if err != nil {
		return nil, err
	}

	info, err := os.Stat(absTarget)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrNotFound
		}
		return nil, err
	}

	if !info.IsDir() {
		rel, _ := filepath.Rel(absRoot, absTarget)
		return []FileEntry{
			{
				Name:      info.Name(),
				Path:      filepath.ToSlash(rel),
				IsDir:     false,
				Size:      info.Size(),
				UpdatedAt: info.ModTime().UTC().Format(time.RFC3339),
			},
		}, nil
	}

	entries := make([]FileEntry, 0)
	err = filepath.WalkDir(absTarget, func(p string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if p == absTarget {
			return nil
		}
		if d.IsDir() && shouldIgnoreDir(d.Name()) {
			return filepath.SkipDir
		}
		// Ensure symlink inside workspace does not escape workspace root
		evalP := evalExistingPrefix(p)
		if !isWithinRoot(absRoot, evalP) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(absRoot, p)
		if err != nil {
			return nil
		}
		fi, err := d.Info()
		var size int64
		var modTime string
		if err == nil {
			size = fi.Size()
			modTime = fi.ModTime().UTC().Format(time.RFC3339)
		}
		entries = append(entries, FileEntry{
			Name:      d.Name(),
			Path:      filepath.ToSlash(rel),
			IsDir:     d.IsDir(),
			Size:      size,
			UpdatedAt: modTime,
		})
		return nil
	})
	if err != nil {
		return nil, err
	}
	return entries, nil
}

func (m *Manager) ReadFile(workspaceRoot, relPath string) (string, string, int64, error) {
	absTarget, cleanRel, err := ResolveSafePath(workspaceRoot, relPath)
	if err != nil {
		return "", "", 0, err
	}
	info, err := os.Stat(absTarget)
	if err != nil {
		if os.IsNotExist(err) {
			return "", "", 0, ErrNotFound
		}
		return "", "", 0, err
	}
	if info.IsDir() {
		return "", "", 0, fmt.Errorf("path %q is a directory, not a file", cleanRel)
	}
	data, err := os.ReadFile(absTarget)
	if err != nil {
		return "", "", 0, err
	}
	return string(data), cleanRel, info.Size(), nil
}

func (m *Manager) WriteFile(workspaceRoot, relPath, content string) (string, int, error) {
	absTarget, cleanRel, err := ResolveSafePath(workspaceRoot, relPath)
	if err != nil {
		return "", 0, err
	}
	if cleanRel == "." {
		return "", 0, fmt.Errorf("cannot write to workspace root directory")
	}
	parentDir := filepath.Dir(absTarget)
	if err := os.MkdirAll(parentDir, 0755); err != nil {
		return "", 0, fmt.Errorf("create parent directories: %w", err)
	}
	// Re-verify after MkdirAll that no symlink escape occurred
	if _, _, err := ResolveSafePath(workspaceRoot, relPath); err != nil {
		return "", 0, err
	}
	data := []byte(content)
	if err := os.WriteFile(absTarget, data, 0644); err != nil {
		return "", 0, fmt.Errorf("write file: %w", err)
	}
	return cleanRel, len(data), nil
}
