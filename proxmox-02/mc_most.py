#!/usr/bin/env python3
"""Lacznik strony z Proxmoxem. Stale dziala na Proxmox-02.

Strona nie ma drogi do Proxmoxa. Rozmawiaja przez dwa katalogi zamontowane w CT strony:
    in/start          pusty sygnal "wlacz". Tresci nie czytamy, plik jest tylko usuwany.
    out/status.json   stan dla strony. Jedyne, co strona dostaje od hosta.
Stan laczy dane klastra z raportem strażnika z Proxmox-03 (/etc/pve/mc-panel/straznik.json).
Ustawienia: /etc/mc-panel/mc.env. Logi: journalctl -t mc-most
"""
import json
import os
import subprocess
import sys
import syslog
import time

WEZEL = "proxmox-03"
USTAWIENIA = "/etc/mc-panel/mc.env"
CT = None           # z USTAWIENIA
RAPORT = "/etc/pve/mc-panel/straznik.json"
CZLONKOWIE = "/etc/pve/.members"
SYGNAL = "/var/lib/mcpanel/in/start"
STATUS = "/var/lib/mcpanel/out/status.json"
USLUGA_WLACZANIA = "mc-wlacz.service"

SYGNAL_CO_S = 1
STATUS_CO_S = 10        # pvesh jest ciezki, wiec nie czesciej
RAPORT_WAZNY_S = 150    # strażnik pisze co minute
BEZ_RAPORTU_S = 15 * 60  # dluzej CT bez raportu oznacza problem, a nie start
PRZERWA_STARTOW_S = 60
START_TRWA_S = 15       # systemd moze jeszcze nie zglaszac uruchomionej uslugi
SYGNAL_WAZNY_S = 60     # gdy klaster nie odpowiadal, stare klikniecie nic nie wlacza


def log(tekst, blad=False):
    syslog.syslog(syslog.LOG_ERR if blad else syslog.LOG_INFO, tekst)
    if sys.stdout.isatty():
        print(tekst)


def komenda(*args, timeout=60):
    wynik = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or f"kod {wynik.returncode}")
    return wynik.stdout


def host_wylaczony():
    """Tani odczyt bez pvesh. Przy watpliwosci False, wtedy pyta pvesh."""
    try:
        with open(CZLONKOWIE) as f:
            return json.load(f)["nodelist"][WEZEL]["online"] == 0
    except (OSError, ValueError, KeyError, TypeError):
        return False


def odczytaj_klaster():
    """Zwraca (ct_dziala, czas pracy CT w sekundach, nazwy innych dzialajacych gosci)."""
    if host_wylaczony():
        return False, 0, []
    ct_dziala, praca, inni = False, 0, []
    zasoby = json.loads(komenda("pvesh", "get", "/cluster/resources", "--type", "vm",
                                "--output-format", "json"))
    for z in zasoby:
        if z.get("node") == WEZEL and z.get("status") == "running":
            if z.get("vmid") == CT:
                ct_dziala, praca = True, z.get("uptime", 0)
            else:
                inni.append(z.get("name", str(z.get("vmid"))))
    return ct_dziala, praca, inni


def odczytaj_raport(sciezka=RAPORT):
    try:
        with open(sciezka) as f:
            raport = json.load(f)
        return raport if type(raport.get("czas")) in (int, float) else None
    except (OSError, ValueError, AttributeError):
        return None


def stan_strony(ct_dziala, praca, inni, raport, wlaczanie, teraz):
    """Sam stan i liczby, bez nazw gosci: strona nie musi wiedziec, co dziala na hoscie."""
    wynik = {"stan": "wylaczony", "gracze": None, "max_graczy": None, "odliczanie_s": None}
    if not ct_dziala:
        if wlaczanie:
            wynik["stan"] = "uruchamianie"
        elif inni:
            wynik["stan"] = "zablokowany"
        return wynik

    # Raport sprzed startu CT mowi o poprzednim uruchomieniu.
    swiezy = (raport is not None and teraz - raport["czas"] <= RAPORT_WAZNY_S
              and raport["czas"] >= teraz - praca)
    if not swiezy:
        wynik["stan"] = "uruchamianie" if wlaczanie or praca < BEZ_RAPORTU_S else "brak_danych"
    elif raport.get("wylaczanie"):
        wynik["stan"] = "zatrzymywanie"
    elif type(raport.get("gracze")) is int:
        wynik.update(stan="wlaczony", gracze=raport["gracze"], max_graczy=raport.get("max_graczy"))
        koniec = raport.get("koniec_odliczania")
        if isinstance(koniec, (int, float)):
            wynik["odliczanie_s"] = max(0, round(koniec - teraz))
    else:
        wynik["stan"] = "uruchamianie"
    return wynik


def odbierz_sygnal(sciezka=SYGNAL):
    """Usuwa sygnal, nie czytajac go. unlink nie podaza za dowiazaniem."""
    try:
        os.unlink(sciezka)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log(f"niepoprawny sygnal: {e}", blad=True)
        return False


def opublikuj(status, teraz, sciezka=STATUS):
    tymczasowy = sciezka + ".tmp"
    with open(tymczasowy, "w") as f:
        json.dump(dict(status, aktualizacja=teraz), f)
    os.chmod(tymczasowy, 0o644)
    os.replace(tymczasowy, sciezka)


def wlaczanie_trwa():
    # is-active konczy sie kodem innym niz 0, gdy usluga nie dziala: to nie blad.
    w = subprocess.run(["systemctl", "is-active", USLUGA_WLACZANIA], capture_output=True, text=True, timeout=30)
    return w.stdout.strip() in ("activating", "active")


def uruchom_wlaczanie():
    komenda("systemctl", "start", "--no-block", USLUGA_WLACZANIA)


class Most:
    def __init__(self, klaster=odczytaj_klaster, raport=odczytaj_raport, sygnal=odbierz_sygnal,
                 publikuj=opublikuj, trwa=wlaczanie_trwa, wlacz=uruchom_wlaczanie, log=log):
        self.klaster, self.raport, self.sygnal = klaster, raport, sygnal
        self.publikuj, self.trwa, self.wlacz, self.log = publikuj, trwa, wlacz, log
        self.nastepny_status = 0
        self.sygnal_czas = None
        self.ostatni_start = None
        self.ostatni_stan = None
        self.ostatni_blad = None

    def krok(self, teraz):
        if self.sygnal():
            self.sygnal_czas = teraz
        if self.sygnal_czas is None and teraz < self.nastepny_status:
            return
        self.nastepny_status = teraz + STATUS_CO_S
        try:
            self.odswiez(teraz)
            self.ostatni_blad = None
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
            # Bez publikacji: po 2 min strona sama pokaze "Brak danych".
            if str(e) != self.ostatni_blad:
                self.log(f"blad: {e}", True)
                self.ostatni_blad = str(e)

    def odswiez(self, teraz):
        ct_dziala, praca, inni = self.klaster()
        status = stan_strony(ct_dziala, praca, inni, self.raport(), self.start_w_toku(teraz), teraz)

        # Klaster odpowiedzial, wiec sygnal jest obsluzony, nawet jesli wlaczenie sie nie uda.
        sygnal_czas, self.sygnal_czas = self.sygnal_czas, None
        if sygnal_czas is not None:
            if teraz - sygnal_czas > SYGNAL_WAZNY_S:
                self.log("sygnal za stary, pomijam")
            elif status["stan"] != "wylaczony":
                self.log(f"sygnal pominiety, stan: {status['stan']}")
            elif self.ostatni_start is not None and teraz - self.ostatni_start < PRZERWA_STARTOW_S:
                self.log("sygnal pominiety, zbyt krotko po poprzednim")
            else:
                self.log("sygnal ze strony: wlaczam")
                self.wlacz()
                self.ostatni_start = teraz
                status = stan_strony(ct_dziala, praca, inni, self.raport(), True, teraz)

        if status["stan"] != self.ostatni_stan:
            self.log(f"stan: {status['stan']}")
            self.ostatni_stan = status["stan"]
        self.publikuj(status, teraz)

    def start_w_toku(self, teraz):
        if self.ostatni_start is not None and teraz - self.ostatni_start < START_TRWA_S:
            return True
        return self.trwa()


def main():
    global CT
    with open(USTAWIENIA) as f:
        for wiersz in f:
            if wiersz.startswith("MC_CT="):
                CT = int(wiersz.split("=", 1)[1])
    if CT is None:
        raise ValueError("brak MC_CT w ustawieniach")
    odbierz_sygnal()    # klikniecie sprzed uruchomienia lacznika juz nieaktualne
    log("start")
    most = Most()
    while True:
        most.krok(time.time())
        time.sleep(SYGNAL_CO_S)


if __name__ == "__main__":
    syslog.openlog("mc-most")
    try:
        sys.exit(main())
    except Exception as e:
        log(f"blad: {e}", True)
        sys.exit(1)
