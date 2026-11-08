#!/usr/bin/env python3
"""Budzi Proxmox-03 i uruchamia CT z Minecraftem. Uruchamiany na Proxmox-02.

Odmawia startu, gdy na Proxmox-03 dziala inny gosc niz CT z Minecraftem (takze render mapy).
Nocnej przerwy nie sprawdza: pilnuje jej lacznik strony, a recznie mozna wlaczyc zawsze.
    --mapa  zamiast Minecrafta uruchamia CT renderu mapy (MAPA_CT), tylko gdy jest flaga
            od straznika i na Proxmox-03 nic nie dziala. Wola go mc-mapa.timer rano.
Kody wyjscia: 0 = CT dziala, 3 = zablokowane, 4 = przekroczony czas, 5 = juz trwa, 6 = mapa aktualna.
Ustawienia: /etc/mc-panel/mc.env (wzor: .env.template).
Logi: journalctl -t mc-wlacz
"""
import fcntl
import json
import os
import subprocess
import sys
import syslog
import time

WEZEL = "proxmox-03"
USTAWIENIA = "/etc/mc-panel/mc.env"
LIMIT_S = 600        # backup po starcie hosta potrafi blokowac CT ponad 4 min
CO_ILE_S = 10
WOL_CO_S = 30        # pakiet moze przepasc, gdy host jeszcze sie wylacza
BLOKADA = "/run/lock/mc-wlacz.lock"   # wspolna dla Minecrafta i mapy
FLAGA_MAPY = "/etc/pve/mc-panel/mapa_do_renderu"

OK, ZABLOKOWANE, CZAS, JUZ_TRWA, NIC = 0, 3, 4, 5, 6


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
    def __init__(self, ct):
        self.ct = ct

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
                if z.get("vmid") == self.ct:
                    ct_dziala = True
                else:
                    inni.append(z.get("name", str(z.get("vmid"))))
        return online, ct_dziala, inni

    def obudz(self):
        komenda("pvenode", "wakeonlan", WEZEL)

    def uruchom_ct(self):
        komenda("pvesh", "create", f"/nodes/{WEZEL}/lxc/{self.ct}/status/start")


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


def wlacz_mape(px, flaga=FLAGA_MAPY, log=log, **zm):
    """Render tylko po dniu, w ktorym serwer dzialal. Blokuje go kazdy dzialajacy gosc, takze Minecraft."""
    if not os.path.exists(flaga):
        log("mapa aktualna, nic do renderu")
        return NIC
    return wlacz(px, log=log, **zm)


def main():
    ustawienia = wczytaj_ustawienia()
    mapa = "--mapa" in sys.argv[1:]
    with open(BLOKADA, "w") as plik:
        try:
            fcntl.flock(plik, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("wlaczanie juz trwa")
            return JUZ_TRWA
        if mapa:
            log("poranny render mapy")
            return wlacz_mape(Proxmox(int(ustawienia["MAPA_CT"])))
        log("prosba o wlaczenie")
        return wlacz(Proxmox(int(ustawienia["MC_CT"])))


if __name__ == "__main__":
    syslog.openlog("mc-wlacz")
    try:
        sys.exit(main())
    except Exception as e:
        log(f"blad: {e}", True)
        sys.exit(1)
