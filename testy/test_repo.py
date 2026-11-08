"""Pilnuje, zeby dane z .env nie trafily do publicznego repo."""
import pathlib
import re
import subprocess
import unittest

KORZEN = pathlib.Path(__file__).parent.parent
PRYWATNE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
    r"|(?!0{8}-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")


JAWNE = {"NOC_OD", "NOC_DO"}   # godziny nocnej przerwy sa publiczne (tez na stronie)


def git(*args):
    return subprocess.run(["git", *args], cwd=KORZEN, capture_output=True, text=True)


def pliki_repo():
    """Pliki sledzone i te, ktore moglyby zostac dodane."""
    wynik = git("ls-files", "-co", "--exclude-standard").stdout.splitlines()
    return [(n, tekst) for n in wynik if (tekst := odczytaj(KORZEN / n)) is not None]


def odczytaj(sciezka):
    try:
        return sciezka.read_text()
    except (UnicodeDecodeError, OSError):
        return None     # binarne, np. fonty


class Repo(unittest.TestCase):
    def test_env_jest_ignorowany(self):
        self.assertEqual(git("check-ignore", ".env").returncode, 0)
        self.assertEqual(git("check-ignore", ".env.template").returncode, 1)

    def test_brak_prywatnych_adresow(self):
        for nazwa, tekst in pliki_repo():
            self.assertEqual(PRYWATNE.findall(tekst), [], nazwa)

    def test_brak_wartosci_z_env(self):
        env = KORZEN / ".env"
        if not env.exists():
            self.skipTest("brak .env")
        wartosci = {w.partition("=")[2].strip() for w in env.read_text().splitlines()
                    if w and not w.startswith("#") and w.partition("=")[0] not in JAWNE}
        wartosci.discard("")
        for nazwa, tekst in pliki_repo():
            for w in wartosci:
                self.assertNotRegex(tekst, rf"(?<![\w.-]){re.escape(w)}(?![\w-])", nazwa)

    def test_wzor_ma_te_same_klucze_co_env(self):
        env = KORZEN / ".env"
        if not env.exists():
            self.skipTest("brak .env")
        klucze = lambda p: set(re.findall(r"^(\w+)=", p.read_text(), re.M))
        self.assertEqual(klucze(env), klucze(KORZEN / ".env.template"))


if __name__ == "__main__":
    unittest.main()
