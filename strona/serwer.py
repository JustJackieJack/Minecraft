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
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

STANY = {"wylaczony", "uruchamianie", "wlaczony", "zatrzymywanie", "zablokowany", "noc", "mapa", "serwis"}
GODZINA = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")
NICK = re.compile(r"[A-Za-z0-9_]{1,16}")
MAKS_GRACZY = 50
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
    ".webp": "image/webp",
    ".json": "application/json",
}
NAGLOWKI_BEZPIECZENSTWA = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                               "img-src 'self'; font-src 'self'; connect-src 'self'; "
                               "frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


# BlueMap w ramce strony: bez unsafe-eval mapa sie nie laduje (sprawdzone). Wymaga tez stylow w atrybutach i blob:.
CSP_MAPY = ("default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self' blob:; worker-src 'self' blob:; "
            "manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
ADRES_MAPY = "/mapa-dane/"
TYPY_MAPY = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".conf": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".webmanifest": "application/manifest+json",
    ".prbm": "application/octet-stream",
}


def godzina(wartosc):
    """Napis HH:MM albo None."""
    if isinstance(wartosc, str) and GODZINA.fullmatch(wartosc):
        return wartosc
    return None


def liczba(wartosc):
    """Nieujemna liczba calkowita albo None. Odrzuca tez bool."""
    if type(wartosc) is int and 0 <= wartosc < 10 ** 7:
        return wartosc
    return None


def gracze(wartosc, pole):
    """Lista {"nick", pole} z poprawnym nickiem i liczba; reszta odrzucana."""
    if not isinstance(wartosc, list):
        return []
    wynik = []
    for g in wartosc[:MAKS_GRACZY]:
        if isinstance(g, dict) and isinstance(g.get("nick"), str) and NICK.fullmatch(g["nick"]):
            w = g.get(pole)
            if type(w) is int and 0 <= w < 10 ** 11:
                wynik.append({"nick": g["nick"], pole: w})
    return wynik


class Panel:
    def __init__(self, katalog, zegar=time.time):
        self.status = pathlib.Path(katalog) / "out" / "status.json"
        self.sygnal = pathlib.Path(katalog) / "in" / "start"
        self.zegar = zegar
        self.blokada = threading.Lock()
        self.ostatni_klik = None

    def odczytaj(self):
        pusty = {"stan": BRAK_DANYCH, "gracze": None, "max_graczy": None, "odliczanie_s": None,
                 "czynny_od": None, "czynny_do": None, "lista_graczy": [], "czas_gry": []}
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
            "czynny_od": godzina(d.get("czynny_od")),
            "czynny_do": godzina(d.get("czynny_do")),
            "lista_graczy": gracze(d.get("lista_graczy"), "od") if stan in ("wlaczony", "serwis") else [],
            "czas_gry": gracze(d.get("czas_gry"), "s"),
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


def wstaw_ustawienia(tresc, ustawienia):
    wartosci = {
        "{{ADRES_GRY}}": html.escape(ustawienia.get("STRONA_ADRES_GRY") or "adres nieustawiony"),
    }
    for znacznik, wartosc in wartosci.items():
        tresc = tresc.replace(znacznik, wartosc)
    return tresc


def wersja_klucz(wersja):
    return tuple(int(c) for c in wersja.split("."))


def wczytaj_zmiany(katalog):
    """Lista zmian z plikow zmiany/<wersja>.md (jeden plik = jedna aktualizacja, wersja = wersja moda Ci_Panowie).

    Format: opcjonalny pierwszy wiersz "data: RRRR-MM-DD", potem punkty "- tekst" (kolejne wiersze bez "- "
    dopisuja sie do poprzedniego punktu). Najnowsza wersja pierwsza; plik o zlej nazwie albo bez punktow jest pomijany.
    """
    wynik = []
    for plik in pathlib.Path(katalog).glob("*.md"):
        if not re.fullmatch(r"\d+(\.\d+)*", plik.stem):
            continue
        data, punkty = None, []
        for wiersz in plik.read_text(encoding="utf-8").splitlines():
            wiersz = wiersz.strip()
            if not wiersz:
                continue
            znaleziona = re.fullmatch(r"data:\s*(\d{4}-\d{2}-\d{2})", wiersz)
            if znaleziona and data is None and not punkty:
                data = znaleziona.group(1)
            elif wiersz.startswith("- "):
                punkty.append(wiersz[2:].strip())
            elif punkty:
                punkty[-1] += " " + wiersz
        if punkty:
            wynik.append({"wersja": plik.stem, "data": data, "punkty": punkty})
    return sorted(wynik, key=lambda z: wersja_klucz(z["wersja"]), reverse=True)


def wczytaj_pliki(www, ustawienia):
    www = pathlib.Path(www)
    pliki = {"/zmiany.json": (json.dumps(wczytaj_zmiany(www / "zmiany"), ensure_ascii=False).encode(),
                              TYPY[".json"])}
    for plik in www.rglob("*"):
        if plik.is_file() and plik.suffix in TYPY:
            tresc = plik.read_bytes()
            adres = "/" + plik.relative_to(www).as_posix()
            if plik.suffix == ".html":      # /mody.html jest dostepne jako /mody
                adres = "/" if plik.stem == "index" else "/" + plik.stem
                tresc = wstaw_ustawienia(tresc.decode(), ustawienia).encode()
            pliki[adres] = (tresc, TYPY[plik.suffix])
    return pliki


def znajdz_plik_mapy(korzen, adres):
    """Zwraca (sciezka, typ, skompresowany) albo None. BlueMap prosi o z0.prbm, a na dysku jest z0.prbm.gz."""
    wzgledny = unquote(adres[len(ADRES_MAPY):]) or "index.html"
    czesci = wzgledny.split("/")
    if "\0" in wzgledny or "\\" in wzgledny or any(c in ("", ".", "..") for c in czesci):
        return None
    typ = TYPY_MAPY.get(pathlib.PurePosixPath(wzgledny).suffix)
    if typ is None:
        return None
    korzen = pathlib.Path(korzen)
    for sciezka, skompresowany in ((korzen / wzgledny, False), (korzen / (wzgledny + ".gz"), True)):
        try:
            # Dowiazanie nie moze wyprowadzic poza katalog mapy.
            if sciezka.is_file() and sciezka.resolve().is_relative_to(korzen.resolve()):
                return sciezka, typ, skompresowany
        except OSError:
            return None
    return None


def utworz_handler(panel, pliki, mapa=None):
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

        def wyslij_mape(self, adres, tylko_naglowki):
            znaleziony = znajdz_plik_mapy(mapa, adres) if mapa else None
            if znaleziony is None:
                # Brak kafelka to puste morze, a nie blad.
                self.send_response(204 if "/tiles/" in adres and adres.endswith((".prbm", ".png")) else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            sciezka, typ, skompresowany = znaleziony
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(sciezka.stat().st_size))
            self.send_header("Cache-Control", "public, max-age=3600" if "/tiles/" in adres else "no-cache")
            self.send_header("Content-Security-Policy", CSP_MAPY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if skompresowany:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if not tylko_naglowki:
                with open(sciezka, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)

        def odpowiedz_get(self, tylko_naglowki):
            adres = urlsplit(self.path).path
            if adres.startswith(ADRES_MAPY):
                self.wyslij_mape(adres, tylko_naglowki)
            elif adres == "/api/status":
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
    a.add_argument("--mapa", default="/srv/mc-mapa")
    a.add_argument("--www", default=str(pathlib.Path(__file__).parent / "www"))
    o = a.parse_args()
    handler = utworz_handler(Panel(o.panel), wczytaj_pliki(o.www, os.environ), o.mapa)
    ThreadingHTTPServer((o.adres, o.port), handler).serve_forever()


if __name__ == "__main__":
    main()
