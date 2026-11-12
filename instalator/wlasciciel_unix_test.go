//go:build !windows

package main

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func TestOddajPlikiRoota(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("wymaga roota (np. w kontenerze z testami)")
	}
	dir := t.TempDir()
	plik := filepath.Join(dir, "mods", "a.jar")
	if err := os.MkdirAll(filepath.Dir(plik), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plik, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	n, err := oddajPlikiRoota(dir, 1234, 1234)
	if err != nil || n != 3 {
		t.Fatalf("n=%d err=%v, oczekiwano 3 (folder, mods, a.jar)", n, err)
	}
	info, _ := os.Stat(plik)
	if st := info.Sys().(*syscall.Stat_t); st.Uid != 1234 || st.Gid != 1234 {
		t.Fatalf("wlasciciel %d:%d", st.Uid, st.Gid)
	}
}
