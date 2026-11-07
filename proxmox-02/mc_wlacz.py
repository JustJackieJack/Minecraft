#!/usr/bin/env python3
"""Budzi Proxmox-03 i uruchamia CT z Minecraftem. Uruchamiany na Proxmox-02.

Odmawia startu, gdy na Proxmox-03 dziala inny gosc niz CT z Minecraftem.
Kody wyjscia: 0 = CT dziala, 3 = zablokowane, 4 = przekroczony czas, 5 = juz trwa.
Ustawienia: /etc/mc-panel/mc.env (wzor: .env.template).
Logi: journalctl -t mc-wlacz
"""
import fcntl
import json
import subprocess
import sys
import syslog
import time

WEZEL = "proxmox-03"
USTAWIENIA = "/etc/mc-panel/mc.env"
CT = None           # z USTAWIENIA
LIMIT_S = 600        # backup po starcie hosta potrafi blokowac CT ponad 4 min
CO_ILE_S = 10
WOL_CO_S = 30        # pakiet moze przepasc, gdy host jeszcze sie wylacza
BLOKADA = "/run/lock/mc-wlacz.lock"

OK, ZABLOKOWANE, CZAS, JUZ_TRWA = 0, 3, 4, 5


def wczytaj_ustawienia(sciezka=USTAWIENIA):
    d = {}
    with open(sciezka) as f:
        for wiersz in f:
            klucz, znak, wartosc = wiersz.strip().partition("=")
            if znak and not klucz.startswith("#"):
                d[klucz] = wartosc
    return d


def log(tekst, blad=False):
    syslog.syslog(syslog.LOG_ERR if blad else syslog.LOG_INFO, tekst)
    if sys.stdout.isatty():
        print(tekst)


def komenda(*args, timeout=180):
    wynik = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or f"kod {wynik.returncode}")
    return wynik.stdout


class Proxmox:
    def stan(self):
        """Zwraca (host_online, ct_dziala, nazwy innych dzialajacych gosci)."""
        zasoby = json.loads(komenda("pvesh", "get", "/cluster/resources",
                                    "--output-format", "json"))
        online, ct_dziala, inni = False, False, []
        for z in zasoby:
            if z.get("node") != WEZEL:
                continue
            if z.get("type") == "node":
                online = z.get("status") == "online"
            elif z.get("type") in ("lxc", "qemu") and z.get("status") == "running":
                if z.get("vmid") == CT:
                    ct_dziala = True
                else:
                    inni.append(z.get("name", str(z.get("vmid"))))
        return online, ct_dziala, inni

    def obudz(self):
        komenda("pvenode", "wakeonlan", WEZEL)

    def uruchom_ct(self):
        komenda("pvesh", "create", f"/nodes/{WEZEL}/lxc/{CT}/status/start")


def wlacz(px, zegar=time.monotonic, spij=time.sleep, log=log):
    koniec = zegar() + LIMIT_S
    ostatni_wol = None
    ostatni_blad = None
    while zegar() < koniec:
        try:
            online, ct_dziala, inni = px.stan()
            if ct_dziala:
                log("CT dziala")
                return OK
            if inni:
                log("zablokowane, dzialaja: " + ", ".join(inni))
                return ZABLOKOWANE
            if online:
                px.uruchom_ct()
            elif ostatni_wol is None or zegar() - ostatni_wol >= WOL_CO_S:
                log("budze host")
                px.obudz()
                ostatni_wol = zegar()
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
            # Np. CT zablokowany przez backup albo host w trakcie wylaczania.
            if str(e) != ostatni_blad:
                log(f"ponawiam: {e}")
                ostatni_blad = str(e)
        spij(CO_ILE_S)
    log("przekroczony czas", True)
    return CZAS


def main():
    global CT
    CT = int(wczytaj_ustawienia()["MC_CT"])
    with open(BLOKADA, "w") as plik:
        try:
            fcntl.flock(plik, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("wlaczanie juz trwa")
            return JUZ_TRWA
        log("prosba o wlaczenie")
        return wlacz(Proxmox())


if __name__ == "__main__":
    syslog.openlog("mc-wlacz")
    try:
        sys.exit(main())
    except Exception as e:
        log(f"blad: {e}", True)
        sys.exit(1)
