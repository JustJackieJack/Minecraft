import http.client
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

KORZEN = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(KORZEN / "strona"))
sys.dont_write_bytecode = True
import dev  # noqa: E402
import serwer  # noqa: E402


USTAWIENIA = {"STRONA_ADRES_GRY": "gra.example.org",
              "STRONA_LINK_MODY": "https://mody.example.org/",
              "STRONA_LINK_MAPA": "https://mapa.example.org/"}


class Strona(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = pathlib.Path(t.name)
        (self.dir / "in").mkdir()
        (self.dir / "out").mkdir()
        self.teraz = 1000.0
        self.panel = serwer.Panel(self.dir, zegar=lambda: self.teraz)
        pliki = serwer.wczytaj_pliki(KORZEN / "strona" / "www", USTAWIENIA)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), serwer.utworz_handler(self.panel, pliki))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def status(self, **dane):
        dane.setdefault("aktualizacja", self.teraz)
        (self.dir / "out" / "status.json").write_text(json.dumps(dane))

    def zapytaj(self, metoda, sciezka, naglowki=None):
        p = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        self.addCleanup(p.close)
        p.request(metoda, sciezka, headers=naglowki or {})
        o = p.getresponse()
        return o.status, o.read(), o

    def stan(self):
        return json.loads(self.zapytaj("GET", "/api/status")[1])

    @property
    def sygnal(self):
        return self.dir / "in" / "start"

    def test_brak_pliku_to_brak_danych(self):
        self.assertEqual(self.stan()["stan"], "brak_danych")

    def test_przeterminowany_status(self):
        self.status(stan="wlaczony", aktualizacja=self.teraz - serwer.STATUS_WAZNY_S - 1)
        self.assertEqual(self.stan()["stan"], "brak_danych")

    def test_uszkodzony_i_obcy_status(self):
        for tresc in ("nie json", "[]", '{"stan":"wlaczony","aktualizacja":true}', '{"stan":"xxx","aktualizacja":1000}'):
            (self.dir / "out" / "status.json").write_text(tresc)
            self.assertEqual(self.stan()["stan"], "brak_danych", tresc)

    def test_przepuszcza_tylko_znane_pola(self):
        self.status(stan="wlaczony", gracze=2, max_graczy=20, odliczanie_s=None, tajne="Inny-999")
        self.assertEqual(self.stan(), {"stan": "wlaczony", "gracze": 2, "max_graczy": 20, "odliczanie_s": None})

    def test_zle_liczby_sa_odrzucane(self):
        self.status(stan="wlaczony", gracze=True, max_graczy=-1, odliczanie_s="9")
        d = self.stan()
        self.assertEqual((d["gracze"], d["max_graczy"], d["odliczanie_s"]), (None, None, None))

    def test_wlacz_tworzy_pusty_sygnal_i_pokazuje_start(self):
        self.status(stan="wylaczony")
        kod, tresc, _ = self.zapytaj("POST", "/api/wlacz")
        self.assertEqual(kod, 202)
        self.assertEqual(self.sygnal.read_bytes(), b"")
        self.assertEqual(self.stan()["stan"], "uruchamianie")
        self.teraz += serwer.PO_KLIKNIECIU_S
        self.status(stan="wylaczony")
        self.assertEqual(self.stan()["stan"], "wylaczony")

    def test_wlacz_odmawia_poza_stanem_wylaczony(self):
        for stan in ("wlaczony", "zablokowany", "uruchamianie", "zatrzymywanie"):
            self.status(stan=stan)
            kod, _, _ = self.zapytaj("POST", "/api/wlacz")
            self.assertEqual(kod, 409, stan)
        self.assertFalse(self.sygnal.exists())

    def test_wlacz_bez_danych_odmawia(self):
        self.assertEqual(self.zapytaj("POST", "/api/wlacz")[0], 409)
        self.assertFalse(self.sygnal.exists())

    def test_blokada_hosta_ma_pierwszenstwo_po_kliknieciu(self):
        self.status(stan="wylaczony")
        self.zapytaj("POST", "/api/wlacz")
        self.status(stan="zablokowany")
        self.assertEqual(self.stan()["stan"], "zablokowany")

    def test_drugie_klikniecie_nie_wysyla_sygnalu(self):
        self.status(stan="wylaczony")
        self.zapytaj("POST", "/api/wlacz")
        self.sygnal.unlink()
        self.assertEqual(self.zapytaj("POST", "/api/wlacz")[0], 409)
        self.assertFalse(self.sygnal.exists())

    def test_nie_zapisuje_przez_dowiazanie(self):
        cel = self.dir / "cel"
        cel.write_text("x")
        os.symlink(cel, self.sygnal)
        self.status(stan="wylaczony")
        self.assertEqual(self.zapytaj("POST", "/api/wlacz")[0], 503)
        self.assertEqual(cel.read_text(), "x")

    def test_obce_pochodzenie(self):
        self.status(stan="wylaczony")
        for n in ({"Origin": "https://zlo.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.zapytaj("POST", "/api/wlacz", n)[0], 403, n)
        self.assertFalse(self.sygnal.exists())

    def test_wlasne_pochodzenie(self):
        self.status(stan="wylaczony")
        n = {"Host": "mc.example.org", "Origin": "https://mc.example.org",
             "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.zapytaj("POST", "/api/wlacz", n)[0], 202)

    def test_pliki_i_naglowki(self):
        kod, tresc, o = self.zapytaj("GET", "/")
        self.assertEqual(kod, 200)
        self.assertIn("Ci Panowie".encode(), tresc)
        self.assertIn("default-src 'none'", o.getheader("Content-Security-Policy"))
        for adres in ("/style.css", "/app.js", "/scena.svg", "/okno.svg", "/tekstura.svg",
                      "/font/pixelify-latin.woff2", "/font/pixelify-latin-ext.woff2"):
            self.assertEqual(self.zapytaj("GET", adres)[0], 200, adres)

    def test_nie_ma_wyjscia_poza_www(self):
        for adres in ("/../serwer.py", "/..%2fserwer.py", "/serwer.py", "/etc/passwd", "/index.html"):
            self.assertEqual(self.zapytaj("GET", adres)[0], 404, adres)

    def test_ustawienia_trafiaja_na_strone(self):
        html = self.zapytaj("GET", "/")[1].decode()
        self.assertIn('href="https://mody.example.org/"', html)
        self.assertIn('href="https://mapa.example.org/"', html)
        self.assertIn(">gra.example.org<", html)
        self.assertNotIn("{{", html)

    def test_zle_ustawienia_nie_psuja_strony(self):
        html = serwer.wstaw_ustawienia(
            '<a href="{{LINK_MODY}}"></a><a href="{{LINK_MAPA}}"></a><code>{{ADRES_GRY}}</code>',
            {"STRONA_ADRES_GRY": "<b>", "STRONA_LINK_MODY": "javascript:alert(1)",
             "STRONA_LINK_MAPA": 'https://x/"y'})
        self.assertEqual(html, '<a href="#"></a><a href="https://x/&quot;y"></a><code>&lt;b&gt;</code>')


class SrodowiskoTestowe(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        d = pathlib.Path(t.name)
        (d / "in").mkdir()
        (d / "out").mkdir()
        self.teraz = 1000.0
        zegar = lambda: self.teraz
        self.panel = serwer.Panel(d, zegar=zegar)
        self.sym = dev.Symulator(self.panel, zegar=zegar)

    def idz(self, sekundy):
        self.teraz += sekundy
        self.sym.krok()
        return self.panel.odczytaj()

    def test_pelny_cykl_po_kliknieciu(self):
        self.assertEqual(self.idz(0)["stan"], "wylaczony")
        self.assertEqual(self.panel.wlacz()[0], 202)
        self.assertEqual(self.idz(1)["stan"], "uruchamianie")
        self.assertFalse(self.panel.sygnal.exists())
        d = self.idz(dev.START_S)
        self.assertEqual((d["stan"], d["gracze"], d["max_graczy"]), ("wlaczony", 0, 20))
        self.assertEqual(self.idz(1)["odliczanie_s"], dev.ODLICZANIE_S)
        self.sym.gracze = 3
        self.assertIsNone(self.idz(1)["odliczanie_s"])
        self.sym.gracze = 0
        self.idz(1)
        self.assertEqual(self.idz(dev.ODLICZANIE_S)["stan"], "zatrzymywanie")
        self.assertEqual(self.idz(dev.STOP_S)["stan"], "wylaczony")

    def test_reczne_wylaczenie_kasuje_stan_po_kliknieciu(self):
        self.idz(0)
        self.panel.wlacz()
        self.sym.ustaw("wylaczony")
        self.assertEqual(self.idz(0)["stan"], "wylaczony")

    def test_brak_danych_usuwa_status(self):
        self.idz(0)
        self.sym.ustaw("brak_danych")
        self.assertEqual(self.idz(0)["stan"], "brak_danych")

    def test_serwer_czyta_zmienione_pliki(self):
        with tempfile.TemporaryDirectory() as www:
            plik = pathlib.Path(www) / "index.html"
            plik.write_text("a")
            pliki = dev.PlikiNaZywo(www, {})
            self.assertEqual(pliki["/"][0], b"a")
            plik.write_text("b")
            self.assertEqual(pliki["/"][0], b"b")


if __name__ == "__main__":
    unittest.main()
