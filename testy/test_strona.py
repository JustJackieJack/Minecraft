import gzip
import hashlib
import http.client
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import ThreadingHTTPServer

KORZEN = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(KORZEN / "strona"))
sys.dont_write_bytecode = True
import dev  # noqa: E402
import serwer  # noqa: E402


USTAWIENIA = {"STRONA_ADRES_GRY": "gra.example.org",
              }


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
        self.mapa = self.dir / "mapa"
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), serwer.utworz_handler(self.panel, pliki, self.mapa))
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
        for adres in ("/style.css", "/app.js", "/tekstura.svg",
                      "/font/jersey15-latin.woff2", "/font/jersey15-latin-ext.woff2",
                      "/Dirt_background_JE1.webp", "/mody", "/mody.js", "/mody.json", "/komendy", "/zainstaluj", "/mapa", "/mapa.js"):
            self.assertEqual(self.zapytaj("GET", adres)[0], 200, adres)

    def test_nie_ma_wyjscia_poza_www(self):
        for adres in ("/../serwer.py", "/..%2fserwer.py", "/serwer.py", "/etc/passwd", "/index.html", "/mody.html"):
            self.assertEqual(self.zapytaj("GET", adres)[0], 404, adres)

    def test_ustawienia_trafiaja_na_strone(self):
        html = self.zapytaj("GET", "/")[1].decode()
        self.assertIn(">gra.example.org<", html)
        self.assertNotIn("{{", html)

    def test_zle_ustawienia_nie_psuja_strony(self):
        html = serwer.wstaw_ustawienia("<code>{{ADRES_GRY}}</code>", {"STRONA_ADRES_GRY": "<b>"})
        self.assertEqual(html, "<code>&lt;b&gt;</code>")

    def test_pasek_na_kazdej_stronie(self):
        for adres in ("/", "/mody", "/komendy", "/zainstaluj", "/mapa"):
            html = self.zapytaj("GET", adres)[1].decode()
            self.assertEqual(html.count('aria-current="page"'), 1, adres)
            for cel in ('href="/mody"', 'href="/komendy"', 'href="/zainstaluj"', 'href="/mapa"'):
                self.assertIn(cel, html, adres)
            self.assertNotIn("{{", html, adres)

    def test_strony_nie_uzywaja_sceny(self):
        for adres in ("/", "/style.css"):
            tresc = self.zapytaj("GET", adres)[1].decode()
            self.assertNotIn("scena", tresc)
            self.assertNotIn("okno", tresc)
        self.assertIn("Dirt_background_JE1.webp", self.zapytaj("GET", "/style.css")[1].decode())

    def test_komendy_maja_sekcje_gracza_i_admina(self):
        html = self.zapytaj("GET", "/komendy")[1].decode()
        gracz, admin = html.split("Dla admina")
        self.assertIn("/cipanowie kompas", gracz)
        self.assertIn("/skilltree reset", admin)
        self.assertNotIn("/skilltree", gracz)
        self.assertNotIn("/cipanowie kompas", admin)

    def wgraj_mape(self):
        kafelek = self.mapa / "maps" / "o" / "tiles" / "0" / "x0"
        kafelek.mkdir(parents=True)
        (self.mapa / "index.html").write_text("<p>mapa</p>")
        (self.mapa / "settings.json").write_text("{}")
        with gzip.open(kafelek / "z0.prbm.gz", "wb") as f:
            f.write(b"dane")
        (self.dir / "tajne.json").write_text("sekret")
        return kafelek

    def test_mapa_index_i_pliki(self):
        self.wgraj_mape()
        kod, tresc, o = self.zapytaj("GET", "/mapa-dane/")
        self.assertEqual((kod, tresc), (200, b"<p>mapa</p>"))
        self.assertIn("frame-ancestors 'self'", o.getheader("Content-Security-Policy"))
        self.assertEqual(self.zapytaj("HEAD", "/mapa-dane/settings.json")[0], 200)

    def test_mapa_podaje_gz_z_naglowkiem(self):
        self.wgraj_mape()
        kod, tresc, o = self.zapytaj("GET", "/mapa-dane/maps/o/tiles/0/x0/z0.prbm")
        self.assertEqual((kod, o.getheader("Content-Encoding")), (200, "gzip"))
        self.assertEqual(gzip.decompress(tresc), b"dane")
        self.assertIn("max-age", o.getheader("Cache-Control"))

    def test_brak_kafelka_to_204_a_brak_pliku_404(self):
        self.wgraj_mape()
        self.assertEqual(self.zapytaj("GET", "/mapa-dane/maps/o/tiles/0/x9/z9.prbm")[0], 204)
        self.assertEqual(self.zapytaj("GET", "/mapa-dane/nie-ma.json")[0], 404)

    def test_mapa_bez_wyjscia_poza_katalog(self):
        kafelek = self.wgraj_mape()
        os.symlink(self.dir / "tajne.json", self.mapa / "dowiazanie.json")
        for adres in ("/mapa-dane/../tajne.json", "/mapa-dane/%2e%2e/tajne.json", "/mapa-dane/..%2ftajne.json",
                      "/mapa-dane/maps//o", "/mapa-dane/dowiazanie.json", "/mapa-dane/maps/o/tiles/0/x0/z0.prbm.gz",
                      "/mapa-dane/x%00.json"):
            self.assertEqual(self.zapytaj("GET", adres)[0], 404, adres)
        self.assertTrue((kafelek / "z0.prbm.gz").exists())

    def test_strona_mapy_pozwala_na_ramke_tylko_z_tej_strony(self):
        o = self.zapytaj("GET", "/mapa")[2]
        csp = o.getheader("Content-Security-Policy")
        self.assertIn("frame-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_mapa_bez_wgranych_plikow(self):
        self.assertEqual(self.zapytaj("HEAD", "/mapa-dane/settings.json")[0], 404)
        self.assertEqual(self.zapytaj("GET", "/mapa-dane/")[0], 404)

    def test_zainstaluj_ma_linki_do_instalatora(self):
        html = self.zapytaj("GET", "/zainstaluj")[1].decode()
        for plik in ("windows.exe", "macos_arm", "macos_intel", "linux"):
            self.assertIn(f'releases/latest/download/ci_panowie_instalator_{plik}"', html)
        self.assertIn("Jak pobrać", html)
        windows, reszta = html.split("<h3>Windows</h3>")[1].split("<h3>macOS</h3>")
        macos, linux = reszta.split("<h3>Linux</h3>")
        self.assertIn(".minecraft", windows)
        self.assertIn("Application", macos)
        self.assertIn("ci_panowie_instalator_macos_arm", macos)
        self.assertNotIn("instalator_linux", macos)
        self.assertIn("ci_panowie_instalator_linux", linux)
        self.assertNotIn("macos", linux)


class ListaModow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dane = json.loads((KORZEN / "strona" / "www" / "mody.json").read_text())
        cls.tagi, cls.mody = dane["tagi"], dane["mody"]

    def test_kazdy_mod_ma_opis_i_tagi(self):
        self.assertGreater(len(self.mody), 100)
        for m in self.mody:
            self.assertTrue(m["nazwa"].strip(), m)
            self.assertTrue(all(a.strip() for a in m["opis"]) and m["opis"], m["nazwa"])
            self.assertTrue(m["tagi"], m["nazwa"])
            self.assertLessEqual(set(m["tagi"]), set(self.tagi), m["nazwa"])
            self.assertNotRegex(" ".join(m["opis"]), r"&[0-9a-fk-or]", m["nazwa"])

    def test_linki_sa_https_i_tylko_nasz_mod_jest_bez_linku(self):
        self.assertEqual([m["id"] for m in self.mody if not m["link"]], ["ci_panowie"])
        for m in self.mody:
            if m["link"]:
                self.assertRegex(m["link"], r"^https://(modrinth\.com/|www\.curseforge\.com/)", m["nazwa"])
                self.assertTrue(m["zrodlo"], m["nazwa"])

    def test_nazwy_sa_unikalne_dla_podpowiedzi(self):
        nazwy = [m["nazwa"] for m in self.mody]
        self.assertEqual(len(nazwy), len(set(nazwy)))


class GeneratorModow(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("generuj_mody", KORZEN / "strona/narzedzia/generuj_mody.py")
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = pathlib.Path(t.name)

    def jar(self, nazwa, mod_id):
        with zipfile.ZipFile(self.dir / nazwa, "w") as z:
            z.writestr("META-INF/mods.toml", f'[[mods]]\nmodId="{mod_id}"\ndisplayName="Mod {mod_id}"\n')
        return hashlib.sha1((self.dir / nazwa).read_bytes()).hexdigest()

    def test_laczy_zrodla(self):
        suma = self.jar("a.jar", "a")
        self.jar("b.jar", "b")
        info = {"x": {"modid": "a", "short": "Krotko", "info": ["&cTresc"], "url": "https://www.curseforge.com/z"}}
        opisy = {"b": {"opis": "Biblioteka", "link": "https://www.curseforge.com/minecraft/search?search=b"}}
        projekt = {"project_type": "mod", "slug": "a-mod", "title": "Mod A",
                   "client_side": "unsupported", "server_side": "required"}
        tagi = {"Potwory": ["a"], "Biblioteka": ["b"], "Pusty": []}
        wynik, braki = self.g.buduj(self.dir, info, opisy, tagi, {suma: projekt})
        self.assertEqual(braki, [])
        a, b = wynik
        self.assertEqual((a["nazwa"], a["link"], a["tagi"]),
                         ("Mod A", "https://modrinth.com/mod/a-mod", ["Potwory"]))
        self.assertEqual(a["opis"], ["Krotko.", "Tresc"])
        self.assertEqual((b["nazwa"], b["zrodlo"], b["tagi"]),
                         ("Mod b", "CurseForge (szukaj)", ["Biblioteka"]))

    def test_zglasza_mod_bez_opisu(self):
        self.jar("a.jar", "a")
        self.assertEqual(self.g.buduj(self.dir, {}, {}, {"T": ["a"]}, {}), ([], ["a"]))

    def test_zglasza_mod_bez_tagu(self):
        self.jar("a.jar", "a")
        self.assertEqual(self.g.buduj(self.dir, {}, {"a": {"opis": "x"}}, {}, {}), ([], ["a"]))


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
