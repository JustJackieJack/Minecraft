#!/usr/bin/env python3
"""Strona statusu Minecrafta. Dziala w CT w DMZ, za NPM DMZ.

Nie ma tokenow ani polaczenia z Proxmoxem. Z hostem rozmawia tylko przez katalog:
    <panel>/out/status.json  stan zapisany przez host, tylko do odczytu
    <panel>/in/start         pusty plik-sygnal "wlacz"; host patrzy tylko, czy istnieje
"""
import argparse
import html
import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

STANY = {"wylaczony", "uruchamianie", "wlaczony", "zatrzymywanie", "zablokowany"}
BRAK_DANYCH = "brak_danych"
STATUS_WAZNY_S = 120
PO_KLIKNIECIU_S = 60
MAKS_STATUS_B = 64 * 1024

TYPY = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
NAGLOWKI_BEZPIECZENSTWA = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                               "img-src 'self'; font-src 'self'; connect-src 'self'; "
                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def liczba(wartosc):
    """Nieujemna liczba calkowita albo None. Odrzuca tez bool."""
    if type(wartosc) is int and 0 <= wartosc < 10 ** 7:
        return wartosc
    return None


class Panel:
    def __init__(self, katalog, zegar=time.time):
        self.status = pathlib.Path(katalog) / "out" / "status.json"
        self.sygnal = pathlib.Path(katalog) / "in" / "start"
        self.zegar = zegar
        self.blokada = threading.Lock()
        self.ostatni_klik = None

    def odczytaj(self):
        pusty = {"stan": BRAK_DANYCH, "gracze": None, "max_graczy": None, "odliczanie_s": None}
        try:
            with open(self.status, "rb") as f:
                d = json.loads(f.read(MAKS_STATUS_B))
            aktualizacja = d["aktualizacja"]
            stan = d["stan"]
        except (OSError, ValueError, KeyError, TypeError):
            return pusty
        if (not isinstance(aktualizacja, (int, float)) or isinstance(aktualizacja, bool)
                or stan not in STANY
                or abs(self.zegar() - aktualizacja) > STATUS_WAZNY_S):
            return pusty

        # Host odpisuje dopiero po chwili, a kliknieta strona ma od razu pokazac start.
        klik = self.ostatni_klik
        if stan == "wylaczony" and klik is not None and self.zegar() - klik < PO_KLIKNIECIU_S:
            stan = "uruchamianie"
        return {
            "stan": stan,
            "gracze": liczba(d.get("gracze")),
            "max_graczy": liczba(d.get("max_graczy")),
            "odliczanie_s": liczba(d.get("odliczanie_s")),
        }

    def wlacz(self):
        """Zwraca (kod HTTP, odpowiedz)."""
        with self.blokada:
            stan = self.odczytaj()["stan"]
            if stan != "wylaczony":
                return 409, {"blad": "stan", "stan": stan}
            try:
                # O_NOFOLLOW: nie zapisujemy przez dowiazanie. Istniejacy sygnal tez wystarczy.
                os.close(os.open(self.sygnal, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
            except OSError:
                return 503, {"blad": "sygnal"}
            self.ostatni_klik = self.zegar()
            return 202, {"stan": "uruchamianie"}


def link(wartosc):
    """Bezpieczny adres do href; bez ustawienia link prowadzi donikad."""
    return html.escape(wartosc) if wartosc.startswith(("https://", "http://")) else "#"


def wstaw_ustawienia(tresc, ustawienia):
    wartosci = {
        "{{ADRES_GRY}}": html.escape(ustawienia.get("STRONA_ADRES_GRY") or "adres nieustawiony"),
        "{{LINK_MODY}}": link(ustawienia.get("STRONA_LINK_MODY", "")),
        "{{LINK_MAPA}}": link(ustawienia.get("STRONA_LINK_MAPA", "")),
    }
    for znacznik, wartosc in wartosci.items():
        tresc = tresc.replace(znacznik, wartosc)
    return tresc


def wczytaj_pliki(www, ustawienia):
    www = pathlib.Path(www)
    pliki = {}
    for plik in www.rglob("*"):
        if plik.is_file() and plik.suffix in TYPY:
            adres = "/" + plik.relative_to(www).as_posix()
            pliki[adres] = (plik.read_bytes(), TYPY[plik.suffix])
    tresc, typ = pliki.pop("/index.html")
    pliki["/"] = (wstaw_ustawienia(tresc.decode(), ustawienia).encode(), typ)
    return pliki


def utworz_handler(panel, pliki):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mc-strona"
        sys_version = ""
        timeout = 20

        def wyslij(self, kod, tresc, typ, cache="no-cache", tylko_naglowki=False):
            self.send_response(kod)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(tresc)))
            self.send_header("Cache-Control", cache)
            for nazwa, wartosc in NAGLOWKI_BEZPIECZENSTWA.items():
                self.send_header(nazwa, wartosc)
            self.end_headers()
            if not tylko_naglowki:
                self.wfile.write(tresc)

        def wyslij_json(self, kod, dane, tylko_naglowki=False):
            self.wyslij(kod, json.dumps(dane).encode(), "application/json", "no-store", tylko_naglowki)

        def odpowiedz_get(self, tylko_naglowki):
            adres = urlsplit(self.path).path
            if adres == "/api/status":
                self.wyslij_json(200, panel.odczytaj(), tylko_naglowki)
            elif adres in pliki:
                tresc, typ = pliki[adres]
                self.wyslij(200, tresc, typ, tylko_naglowki=tylko_naglowki)
            else:
                self.wyslij_json(404, {"blad": "nie ma"}, tylko_naglowki)

        def do_GET(self):
            self.odpowiedz_get(False)

        def do_HEAD(self):
            self.odpowiedz_get(True)

        def z_tej_strony(self):
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
                return False
            pochodzenie = self.headers.get("Origin")
            return pochodzenie is None or (
                urlsplit(pochodzenie).netloc.lower() == (self.headers.get("Host") or "").lower())

        def do_POST(self):
            # Tresc zapytania nie jest czytana, wiec polaczenia nie wolno uzyc ponownie.
            self.close_connection = True
            if urlsplit(self.path).path != "/api/wlacz":
                self.wyslij_json(404, {"blad": "nie ma"})
            elif not self.z_tej_strony():
                self.wyslij_json(403, {"blad": "pochodzenie"})
            else:
                kod, odpowiedz = panel.wlacz()
                adres = self.headers.get("X-Real-IP") or self.client_address[0]
                print(f"wlacz: {kod} od {adres}", flush=True)
                self.wyslij_json(kod, odpowiedz)

        def log_message(self, *_):
            pass

    return Handler


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--adres", default="0.0.0.0")
    a.add_argument("--port", type=int, default=8080)
    a.add_argument("--panel", default="/panel")
    a.add_argument("--www", default=str(pathlib.Path(__file__).parent / "www"))
    o = a.parse_args()
    handler = utworz_handler(Panel(o.panel), wczytaj_pliki(o.www, os.environ))
    ThreadingHTTPServer((o.adres, o.port), handler).serve_forever()


if __name__ == "__main__":
    main()
