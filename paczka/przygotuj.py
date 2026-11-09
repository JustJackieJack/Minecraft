#!/usr/bin/env python3
"""Przygotowuje dane dla instalatora z Twojej instancji. Uruchom w folderze minecraft:

    python3 ~/WebstormProjects/Minecraft/paczka/przygotuj.py

Tworzy obok siebie paczka.json, skiny.zip, config.zip, config-serwer.zip i mody/ z naszym modem.
Pozostale mody sa tylko linkami do Modrinth, a gdy ich tam nie ma, do CurseForge.
"""
import argparse
import fnmatch
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

TU = pathlib.Path(__file__).resolve().parent
KORZEN = TU.parent
NASZ_MOD = "ci_panowie"
API = "https://api.modrinth.com/v2"
NAGLOWKI = {"User-Agent": "mc-strona-przygotuj-paczke/1.0", "Content-Type": "application/json"}
CZAS_ZIP = (2020, 1, 1, 0, 0, 0)    # stala data: te same pliki daja te sama wersje
# Configi modow, ktorych juz nie ma.
POMIJANY_CONFIG = {"DistantHorizons.toml", "improvedmobs", "puffish_skills", "passiveskilltreeadditions-client.toml",
                   "enhancedcelestials2core-mixins.properties", "hordes-common.toml.bak", "jei/world"}
TEKSTOWE = {".toml", ".json", ".json5", ".cfg", ".txt", ".properties", ".conf", ".yaml", ".yml", ".snbt"}
PRYWATNE = re.compile(rb"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")

sys.path.insert(0, str(KORZEN / "strona" / "narzedzia"))
sys.dont_write_bytecode = True
from generuj_mody import identyfikatory  # noqa: E402


def zapytaj(adres, dane=None):
    tresc = json.dumps(dane).encode() if dane is not None else None
    with urllib.request.urlopen(urllib.request.Request(adres, data=tresc, headers=NAGLOWKI), timeout=60) as o:
        return json.load(o)


def sha1(dane):
    return hashlib.sha1(dane).hexdigest()


def pliki_modrinth(sumy):
    """suma SHA-1 -> (adres pliku, nazwa projektu)."""
    wersje = zapytaj(f"{API}/version_files", {"hashes": sumy, "algorithm": "sha1"})
    ids = sorted({w["project_id"] for w in wersje.values()})
    tytuly = {}
    for i in range(0, len(ids), 80):
        lista = urllib.parse.quote(json.dumps(ids[i:i + 80]))
        for p in zapytaj(f"{API}/projects?ids={lista}"):
            tytuly[p["id"]] = p["title"].strip()
    wynik = {}
    for suma, w in wersje.items():
        for f in w["files"]:
            if f["hashes"].get("sha1") == suma:
                wynik[suma] = (f["url"], tytuly[w["project_id"]])
    return wynik


def adres_curseforge(plik_id, nazwa):
    # Bez zer wiodacych: plik 6863096 lezy w /files/6863/96/, a /096/ daje 403.
    return f"https://mediafilez.forgecdn.net/files/{plik_id // 1000}/{plik_id % 1000}/{urllib.parse.quote(nazwa)}"


def plik_curseforge(projekt, nazwa, suma, pobierz=None):
    """Adres pliku z CurseForge. Sprawdza sume, bo projekt moze miec pliki o tej samej nazwie."""
    pobierz = pobierz or pobierz_plik
    dane = zapytaj(f"https://api.cfwidget.com/minecraft/mc-mods/{projekt}")
    for f in dane.get("files", []):
        if f.get("name") == nazwa:
            adres = adres_curseforge(f["id"], nazwa)
            if sha1(pobierz(adres)) == suma:
                return adres, dane.get("title", projekt)
    return None


def pobierz_plik(adres):
    zapytanie = urllib.request.Request(adres, headers={"User-Agent": NAGLOWKI["User-Agent"]})
    with urllib.request.urlopen(zapytanie, timeout=120) as o:
        return o.read()


def wczytaj_wzorce(plik):
    return [w for w in plik.read_text().splitlines() if w and not w.startswith("#")]


def strona_moda(nazwa, wzorce):
    return "klient" if any(fnmatch.fnmatch(nazwa, w) for w in wzorce) else "oba"


def zbuduj_zip(pliki):
    """pliki: sciezka w archiwum -> sciezka na dysku. Wynik zalezy tylko od tresci."""
    bufor = io.BytesIO()
    with zipfile.ZipFile(bufor, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for nazwa in sorted(pliki):
            info = zipfile.ZipInfo(nazwa, CZAS_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, pathlib.Path(pliki[nazwa]).read_bytes())
    return bufor.getvalue()


def pliki_folderu(korzen, przedrostek, pomijane=frozenset()):
    korzen = pathlib.Path(korzen)
    wynik = {}
    for plik in sorted(korzen.rglob("*")):
        if not plik.is_file() or plik.is_symlink():
            continue
        wzgledna = plik.relative_to(korzen).as_posix()
        if any(wzgledna == p or wzgledna.startswith(p + "/") for p in pomijane):
            continue
        wynik[f"{przedrostek}/{wzgledna}"] = plik
    return wynik


def zapisz_archiwum(cel, nazwa, pliki):
    dane = zbuduj_zip(pliki)
    (cel / nazwa).write_bytes(dane)
    suma = hashlib.sha256(dane).hexdigest()
    return {"plik": nazwa, "wersja": suma[:12], "sha256": suma}


def wartosci_prywatne():
    """Wartosci z .env oprocz sciezek i numerow: tych szukamy w publikowanych plikach."""
    wynik = set()
    env = KORZEN / ".env"
    if env.exists():
        for wiersz in env.read_text().splitlines():
            klucz, znak, wartosc = wiersz.partition("=")
            if znak and klucz in ("MC_ADRES_CT", "MC_ID_SERWERA", "STRONA_ADRES_GRY"):
                wynik.add(wartosc.encode())
    return wynik


def znajdz_prywatne(pliki, wartosci):
    """Zwraca liste 'plik: wartosc' do pokazania przed publikacja."""
    wynik = []
    for nazwa, sciezka in sorted(pliki.items()):
        if pathlib.PurePosixPath(nazwa).suffix not in TEKSTOWE:
            continue
        tresc = pathlib.Path(sciezka).read_bytes()
        for w in sorted(wartosci | set(PRYWATNE.findall(tresc))):
            if w in tresc:
                wynik.append(f"{nazwa}: {w.decode()}")
    return wynik


def wersje_gry(folder):
    """Minecraft i Forge z mmc-pack.json instancji Prism."""
    try:
        skladniki = json.loads((folder.parent / "mmc-pack.json").read_text())["components"]
    except (OSError, ValueError, KeyError):
        return None, None
    wersje = {s.get("uid"): s.get("version") for s in skladniki}
    return wersje.get("net.minecraft"), wersje.get("net.minecraftforge")


def usuniete_mody(cel, mody):
    """Mody z poprzedniej paczki (i wczesniej usuniete), ktorych juz nie ma. Instalator proponuje ich usuniecie."""
    try:
        stara = json.loads((cel / "paczka.json").read_text())
    except (OSError, ValueError):
        return []
    teraz = {m["id"] for m in mody}
    wynik = {u["id"]: u["nazwa"] for u in stara.get("usuniete", [])}
    wynik.update({m["id"]: m["nazwa"] for m in stara.get("mody", [])})
    return [{"id": i, "nazwa": n} for i, n in sorted(wynik.items()) if i not in teraz and i != NASZ_MOD]


def poprzednie_adresy(cel):
    """Sprawdzone wczesniej pliki z CurseForge nie sa pobierane ponownie: bywaja duze."""
    try:
        mody = json.loads((cel / "paczka.json").read_text())["mody"]
    except (OSError, ValueError, KeyError):
        return {}
    return {m["sha1"]: (m["url"], m["nazwa"]) for m in mody if "forgecdn" in m["url"]}


def lista_modow(folder, cel, curseforge, wzorce, opcjonalne=()):
    poprzednie = poprzednie_adresy(cel)
    jary = sorted((folder / "mods").glob("*.jar"))
    sumy = {j: sha1(j.read_bytes()) for j in jary}
    modrinth = pliki_modrinth(list(sumy.values()))
    (cel / "mody").mkdir(exist_ok=True)
    mody, braki, nasze = [], [], set()
    for jar in jary:
        ids, nazwa_jar = identyfikatory(jar)
        id_moda = ids[0] if ids else jar.name
        suma = sumy[jar]
        if id_moda == NASZ_MOD:
            (cel / "mody" / jar.name).write_bytes(jar.read_bytes())
            nasze.add(jar.name)
            adres, tytul = "mody/" + urllib.parse.quote(jar.name), nazwa_jar or id_moda
        elif suma in modrinth:
            adres, tytul = modrinth[suma]
        elif id_moda in curseforge:
            znaleziony = poprzednie.get(suma) or plik_curseforge(curseforge[id_moda], jar.name, suma)
            if znaleziony is None:
                braki.append(jar.name)
                continue
            adres, tytul = znaleziony
        else:
            braki.append(jar.name)
            continue
        mod = {"id": id_moda, "nazwa": tytul, "plik": jar.name, "sha1": suma, "url": adres,
               "strona": strona_moda(jar.name, wzorce)}
        if any(fnmatch.fnmatch(jar.name, w) for w in opcjonalne):
            mod["opcjonalny"] = True
        mody.append(mod)
    for stary in (cel / "mody").glob("*.jar"):
        if stary.name not in nasze:
            stary.unlink()
    return mody, braki


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--cel", default=str(TU))
    o = a.parse_args()
    folder = pathlib.Path.cwd()
    cel = pathlib.Path(o.cel)
    if not (folder / "mods").is_dir() or not (folder / "config").is_dir():
        sys.exit("Uruchom w folderze minecraft, tam gdzie są foldery mods i config.")
    cel.mkdir(parents=True, exist_ok=True)

    curseforge = json.loads((TU / "curseforge.json").read_text())
    wzorce = wczytaj_wzorce(TU / "tylko_klient.txt")
    opcjonalne = wczytaj_wzorce(TU / "opcjonalne.txt")
    mody, braki = lista_modow(folder, cel, curseforge, wzorce, opcjonalne)
    if braki:
        sys.exit("Nie znalazłem adresu dla: " + ", ".join(braki) + ". Dopisz je w paczka/curseforge.json.")

    config = pliki_folderu(folder / "config", "config", POMIJANY_CONFIG)
    serwer = dict(config, **pliki_folderu(folder / "defaultconfigs", "defaultconfigs"))
    skiny = pliki_folderu(folder / "CustomSkinLoader" / "Skiny", "CustomSkinLoader/Skiny")
    skiny["CustomSkinLoader/CustomSkinLoader.json"] = folder / "CustomSkinLoader" / "CustomSkinLoader.json"

    minecraft, forge = wersje_gry(folder)
    paczka = {
        "minecraft": minecraft, "forge": forge,
        "skiny": zapisz_archiwum(cel, "skiny.zip", skiny),
        "config": zapisz_archiwum(cel, "config.zip", config),
        "config_serwer": zapisz_archiwum(cel, "config-serwer.zip", serwer),
        "mody": mody,
        "usuniete": usuniete_mody(cel, mody),
    }
    (cel / "paczka.json").write_text(json.dumps(paczka, ensure_ascii=False, indent=1) + "\n")

    zrodla = {"Modrinth": 0, "CurseForge": 0, "repo": 0}
    for m in mody:
        zrodla["Modrinth" if "modrinth" in m["url"] else "CurseForge" if "forgecdn" in m["url"] else "repo"] += 1
    print(f"Modów: {len(mody)} {zrodla}, tylko dla gracza: {sum(m['strona'] == 'klient' for m in mody)}, "
          f"opcjonalne: {[m['plik'] for m in mody if m.get('opcjonalny')]}")
    print(f"Minecraft {minecraft}, Forge {forge}")
    print(f"Usunięte z paczki (instalator zaproponuje usunięcie): {[u['nazwa'] for u in paczka['usuniete']]}")
    print(f"Skiny {paczka['skiny']['wersja']}, config {paczka['config']['wersja']}, serwer {paczka['config_serwer']['wersja']}")

    znalezione = znajdz_prywatne({**serwer, **skiny}, wartosci_prywatne())
    if znalezione:
        print("\nUWAGA: te dane staną się publiczne po wypchnięciu paczki:")
        for z in znalezione:
            print("  " + z)

    # Lista na stronie ma pokazywac te same mody. Blad nie psuje juz gotowej paczki.
    w = subprocess.run([sys.executable, str(KORZEN / "strona/narzedzia/generuj_mody.py"), "--mody", str(folder / "mods")],
                       capture_output=True, text=True)
    print("\nLista modów na stronie: " + ("zaktualizowana" if w.returncode == 0 else "NIE zaktualizowana: " + w.stderr.strip()))


if __name__ == "__main__":
    main()
