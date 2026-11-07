import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile

KORZEN = pathlib.Path(__file__).parent.parent
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("przygotuj", KORZEN / "paczka/przygotuj.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class Paczka(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = pathlib.Path(t.name)

    def test_zip_zalezy_tylko_od_tresci(self):
        (self.dir / "a").write_text("x")
        pierwszy = p.zbuduj_zip({"config/a": self.dir / "a"})
        import os
        os.utime(self.dir / "a", (1, 1))
        self.assertEqual(pierwszy, p.zbuduj_zip({"config/a": self.dir / "a"}))
        (self.dir / "a").write_text("y")
        self.assertNotEqual(pierwszy, p.zbuduj_zip({"config/a": self.dir / "a"}))

    def test_pominiete_foldery_i_pliki(self):
        for n in ("a.toml", "jei/world/x", "jei/ok", "DistantHorizons.toml"):
            (self.dir / n).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / n).write_text("x")
        self.assertEqual(sorted(p.pliki_folderu(self.dir, "config", p.POMIJANY_CONFIG)),
                         ["config/a.toml", "config/jei/ok"])

    def test_strona_moda(self):
        wzorce = (KORZEN / "paczka/tylko_klient.txt").read_text().splitlines()
        self.assertEqual(p.strona_moda("embeddium-0.3.jar", wzorce), "klient")
        self.assertEqual(p.strona_moda("inventoryhud.forge.jar", wzorce), "klient")
        self.assertEqual(p.strona_moda("create-1.20.1.jar", wzorce), "oba")

    def test_adres_curseforge(self):
        self.assertEqual(p.adres_curseforge(6863096, "a+b c.jar"),
                         "https://mediafilez.forgecdn.net/files/6863/96/a%2Bb%20c.jar")

    def test_znajduje_prywatne_dane(self):
        adres = ".".join(["10", "1", "2", "3"])     # sklejany, bo test_repo pilnuje plikow repo
        (self.dir / "a.toml").write_text(f'host = "mc.tajne.example"\nip = "{adres}"\n')
        (self.dir / "b.png").write_text(adres)
        wynik = p.znajdz_prywatne({"config/a.toml": self.dir / "a.toml", "x/b.png": self.dir / "b.png"},
                                  {b"mc.tajne.example"})
        self.assertEqual(wynik, [f"config/a.toml: {adres}", "config/a.toml: mc.tajne.example"])

    def test_wersje_gry(self):
        (self.dir / "minecraft").mkdir()
        self.assertEqual(p.wersje_gry(self.dir / "minecraft"), (None, None))
        (self.dir / "mmc-pack.json").write_text(json.dumps({"components": [
            {"uid": "net.minecraft", "version": "1.20.1"}, {"uid": "net.minecraftforge", "version": "47.4"}]}))
        self.assertEqual(p.wersje_gry(self.dir / "minecraft"), ("1.20.1", "47.4"))

    def test_zip_nadaje_sie_dla_instalatora(self):
        (self.dir / "a").write_text("x")
        cel = self.dir / "w"
        cel.mkdir()
        a = p.zapisz_archiwum(cel, "c.zip", {"config/a": self.dir / "a"})
        self.assertEqual(len(a["wersja"]), 12)
        with zipfile.ZipFile(cel / "c.zip") as z:
            self.assertEqual(z.namelist(), ["config/a"])


if __name__ == "__main__":
    unittest.main()
