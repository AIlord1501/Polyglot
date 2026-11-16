package workspace

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestResolveSafePathAndFileOperations(t *testing.T) {
	tmpBase := t.TempDir()
	mgr, err := NewManager(tmpBase)
	if err != nil {
		t.Fatalf("NewManager failed: %v", err)
	}

	wsRoot, err := mgr.PrepareWorkspaceDir("test-ws", "")
	if err != nil {
		t.Fatalf("PrepareWorkspaceDir failed: %v", err)
	}

	// 1. Valid paths inside workspace
	validPaths := []string{
		".",
		"calculator.py",
		"src/math_ops.py",
		"./tests/test_calc.py",
	}
	for _, vp := range validPaths {
		absP, relP, err := ResolveSafePath(wsRoot, vp)
		if err != nil {
			t.Errorf("expected valid path %q to succeed, got error: %v", vp, err)
		}
		if absP == "" || relP == "" {
			t.Errorf("expected non-empty resolved path for %q", vp)
		}
	}

	// 2. Traversal and outside paths MUST fail with ErrPathTraversal
	outsideFile := filepath.Join(tmpBase, "outside.txt")
	_ = os.WriteFile(outsideFile, []byte("secret"), 0644)

	maliciousPaths := []string{
		"../outside.txt",
		"..\\outside.txt",
		"../../Windows/win.ini",
		"..\\..\\Windows\\win.ini",
		"sub/../../outside.txt",
		"/etc/passwd",
		"\\Windows\\win.ini",
		"C:\\Windows\\win.ini",
		outsideFile,
		"bad\x00file.txt",
	}
	for _, mp := range maliciousPaths {
		_, _, err := ResolveSafePath(wsRoot, mp)
		if !errors.Is(err, ErrPathTraversal) {
			t.Errorf("expected ErrPathTraversal for %q, got: %v", mp, err)
		}
		if _, _, _, err := mgr.ReadFile(wsRoot, mp); !errors.Is(err, ErrPathTraversal) {
			t.Errorf("ReadFile should reject %q with ErrPathTraversal, got: %v", mp, err)
		}
		if _, _, err := mgr.WriteFile(wsRoot, mp, "hacked"); !errors.Is(err, ErrPathTraversal) {
			t.Errorf("WriteFile should reject %q with ErrPathTraversal, got: %v", mp, err)
		}
	}

	// Confirm outside.txt was not modified
	data, _ := os.ReadFile(outsideFile)
	if string(data) != "secret" {
		t.Fatalf("outside file was modified!")
	}

	// 3. Write, Read, and List inside workspace
	relWritten, n, err := mgr.WriteFile(wsRoot, "pkg/calc.py", "def add(a, b): return a + b\n")
	if err != nil {
		t.Fatalf("WriteFile failed: %v", err)
	}
	if relWritten != "pkg/calc.py" || n == 0 {
		t.Fatalf("unexpected WriteFile result: rel=%q n=%d", relWritten, n)
	}

	content, cleanRel, size, err := mgr.ReadFile(wsRoot, "pkg/calc.py")
	if err != nil {
		t.Fatalf("ReadFile failed: %v", err)
	}
	if cleanRel != "pkg/calc.py" || size == 0 || content != "def add(a, b): return a + b\n" {
		t.Fatalf("unexpected ReadFile output: %q", content)
	}

	entries, err := mgr.ListFiles(wsRoot, ".")
	if err != nil {
		t.Fatalf("ListFiles failed: %v", err)
	}
	if len(entries) < 2 { // "pkg" dir and "pkg/calc.py"
		t.Fatalf("expected at least 2 entries, got %d", len(entries))
	}
}
