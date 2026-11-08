#!/usr/bin/env python3
"""Lokalne srodowisko testowe strony: prawdziwy serwer + udawany host.

    python3 strona/dev.py
Pliki www/ sa czytane przy kazdym zapytaniu, wiec wystarczy odswiezyc strone.
Slucha tylko na 127.0.0.1.
"""
import argparse
import json
import os
import pathlib
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import serwer

TU = pathlib.Path(__file__).parent
START_S = 10
ODLICZANIE_S = 60
STOP_S = 5
STANY = ("wylaczony", "uruchamianie", "wlaczony", "zatrzymywanie", "zablokowany", "noc", "mapa", "brak_danych")


class Symulator:
    """Zachowuje sie jak host: odbiera sygnal z in/start i zapisuje out/status.json."""

    def __init__(self, panel, zegar=time.time):
        self.panel = panel
        self.zegar = zegar
        self.gracze = 0
        self.ustaw("wylaczony")

    def ustaw(self, stan):
        self.stan = stan
        self.koniec_etapu = self.zegar() + {"uruchamianie": START_S, "zatrzymywanie": STOP_S}.get(stan, 0)
        self.koniec_odliczania = None
        if stan == "wylaczony":
            # Inaczej strona przez minute pokazuje start, a sygnal uruchomilby serwer.
            self.panel.ostatni_klik = None
            self.panel.sygnal.unlink(missing_ok=True)

    def odliczaj(self, sekundy):
        self.koniec_odliczania = self.zegar() + sekundy

    def krok(self):
        teraz = self.zegar()
        if self.panel.sygnal.exists():
            self.panel.sygnal.unlink()
            if self.stan == "wylaczony":
                self.ustaw("uruchamianie")
        if self.stan == "uruchamianie" and teraz >= self.koniec_etapu:
            self.ustaw("wlaczony")
        elif self.stan == "zatrzymywanie" and teraz >= self.koniec_etapu:
            self.ustaw("wylaczony")
        elif self.stan == "wlaczony":
            if self.gracze:
                self.koniec_odliczania = None
            elif self.koniec_odliczania is None:
                self.odliczaj(ODLICZANIE_S)
            elif teraz >= self.koniec_odliczania:
                self.ustaw("zatrzymywanie")
        self.zapisz()

    def odliczanie_s(self):
        if self.koniec_odliczania is None:
            return None
        return max(0, round(self.koniec_odliczania - self.zegar()))

    def zapisz(self):
        if self.stan == "brak_danych":
            self.panel.status.unlink(missing_ok=True)
            return
        dzialajacy = self.stan == "wlaczony"
        dane = {"aktualizacja": self.zegar(), "stan": self.stan,
                "gracze": self.gracze if dzialajacy else None, "max_graczy": 20 if dzialajacy else None,
                "odliczanie_s": self.odliczanie_s(), "czynny_od": "06:00", "czynny_do": "23:30"}
        tymczasowy = self.panel.status.with_suffix(".tmp")
        tymczasowy.write_text(json.dumps(dane))
        os.replace(tymczasowy, self.panel.status)


class PlikiNaZywo:
    """To samo co slownik z wczytaj_pliki, ale bez zapamietywania tresci."""

    def __init__(self, www, ustawienia):
        self.www, self.ustawienia = www, ustawienia

    def __contains__(self, adres):
        return adres in serwer.wczytaj_pliki(self.www, self.ustawienia)

    def __getitem__(self, adres):
        return serwer.wczytaj_pliki(self.www, self.ustawienia)[adres]


def sterowanie(sym, blokada):
    def przyciski(sciezka, parametr, wartosci):
        return "".join(f'<a href="{sciezka}?{parametr}={w}">{w}</a> ' for w in wartosci)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with blokada:
                if u.path == "/stan" and q.get("na") in STANY:
                    sym.ustaw(q["na"])
                elif u.path == "/gracze" and q.get("ile", "").isdigit():
                    sym.gracze = int(q["ile"])
                elif u.path == "/odliczanie" and q.get("s", "").isdigit():
                    sym.odliczaj(int(q["s"]))
                elif u.path != "/":
                    self.send_error(404)
                    return
                sym.krok()
                opis = (f"{sym.stan}, graczy: {sym.gracze}, odliczanie: {sym.odliczanie_s()}")
            if u.path != "/":
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
            tresc = f"""<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>Sterowanie</title><style>body{{font:16px monospace;margin:2em}}
a{{display:inline-block;padding:.3em .6em;margin:.2em;border:1px solid;text-decoration:none}}</style>
<h1>{opis}</h1>
<p>Stan: {przyciski("/stan", "na", STANY)}
<p>Gracze: {przyciski("/gracze", "ile", (0, 1, 7, 20))}
<p>Odliczanie: {przyciski("/odliczanie", "s", (600, 60, 10))}""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(tresc)))
            self.end_headers()
            self.wfile.write(tresc)

        def log_message(self, *_):
            pass

    return Handler


def ustawienia_z_env(sciezka):
    d = {}
    if sciezka.exists():
        for wiersz in sciezka.read_text().splitlines():
            klucz, znak, wartosc = wiersz.strip().partition("=")
            if znak and klucz.startswith("STRONA_"):
                d[klucz] = wartosc
    return d


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--port", type=int, default=18080)
    o = a.parse_args()
    with tempfile.TemporaryDirectory(prefix="mc-strona-dev-") as katalog:
        (pathlib.Path(katalog) / "in").mkdir()
        (pathlib.Path(katalog) / "out").mkdir()
        panel = serwer.Panel(katalog)
        sym = Symulator(panel)
        blokada = threading.Lock()
        pliki = PlikiNaZywo(TU / "www", ustawienia_z_env(TU.parent / ".env"))
        strona = ThreadingHTTPServer(("127.0.0.1", o.port), serwer.utworz_handler(panel, pliki, TU.parent / "mapa" / "praca" / "web"))
        panelik = ThreadingHTTPServer(("127.0.0.1", o.port + 1), sterowanie(sym, blokada))
        threading.Thread(target=strona.serve_forever, daemon=True).start()
        threading.Thread(target=panelik.serve_forever, daemon=True).start()
        print(f"Strona:      http://127.0.0.1:{o.port}", flush=True)
        print(f"Sterowanie:  http://127.0.0.1:{o.port + 1}", flush=True)
        print("Ctrl+C konczy.", flush=True)
        try:
            while True:
                with blokada:
                    sym.krok()
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
