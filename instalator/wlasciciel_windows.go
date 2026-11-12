//go:build windows

package main

// Na Windows nie ma wlasciciela pliku w sensie uid/gid - nic do zrobienia.
func przywrocWlasciciela(folder string) (int, error) { return 0, nil }
