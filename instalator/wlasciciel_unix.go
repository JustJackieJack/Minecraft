//go:build !windows

package main

import (
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
)

// przywrocWlasciciela oddaje pliki roota w folderze serwera wlascicielowi tego folderu.
// Dziala tylko, gdy instalator ma uprawnienia roota, a folder nalezy do kogos innego (np. Crafty).
func przywrocWlasciciela(folder string) (int, error) {
	if os.Geteuid() != 0 {
		return 0, nil
	}
	info, err := os.Stat(folder)
	if err != nil {
		return 0, err
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || st.Uid == 0 {
		return 0, nil
	}
	return oddajPlikiRoota(folder, int(st.Uid), int(st.Gid))
}

func oddajPlikiRoota(folder string, uid, gid int) (int, error) {
	n := 0
	err := filepath.WalkDir(folder, func(sciezka string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if st, ok := info.Sys().(*syscall.Stat_t); ok && st.Uid == 0 {
			if err := os.Lchown(sciezka, uid, gid); err != nil {
				return err
			}
			n++
		}
		return nil
	})
	return n, err
}
