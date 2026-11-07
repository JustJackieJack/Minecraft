#!/usr/bin/env python3
"""Tworzy www/mody.json z modow instancji Prism. Uruchom po zmianie paczki:

    python3 strona/narzedzia/generuj_mody.py

Linki pochodza z Modrinth (dopasowanie po sumie SHA-1 pliku), opisy z info.json moda Ci_Panowie,
a brakujace z mody_opisy.json. Tagi sa w mody_tagi.json. Mod bez opisu albo tagu przerywa prace.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import tomllib
import urllib.parse
import urllib.request
import zipfile

TU = pathlib.Path(__file__).parent
INSTANCJA = "~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances/Serwer_Main/minecraft/mods"
INFO = "~/TMP/Ci_Panowie/src/main/resources/ci_panowie/info.json"
API = "https://api.modrinth.com/v2"
NAGLOWKI = {"User-Agent": "mc-strona-generuj-mody/1.0", "Content-Type": "application/json"}
# Wpis zbiorczy dla wielu modow: kazdy z nich ma wlasny opis.
POMIJANE_INFO = {"structures"}
KOLORY = re.compile(r"&[0-9a-fk-or]")


def zapytaj(adres, dane=None):
    tresc = json.dumps(dane).encode() if dane is not None else None
    zapytanie = urllib.request.Request(adres, data=tresc, headers=NAGLOWKI)
    with urllib.request.urlopen(zapytanie, timeout=30) as o:
        return json.load(o)


def identyfikatory(jar):
    with zipfile.ZipFile(jar) as z:
        try:
            tekst = z.read("META-INF/mods.toml").decode("utf-8", "replace")
        except KeyError:
            return [], None
    try:
        mody = tomllib.loads(tekst).get("mods", [])
        return [m["modId"] for m in mody], next((m.get("displayName") for m in mody), None)
    except tomllib.TOMLDecodeError:     # czesc modow ma niepoprawny TOML
        nazwa = re.search(r'displayName\s*=\s*"([^"]+)"', tekst)
        return re.findall(r'modId\s*=\s*"([^"]+)"', tekst), nazwa and nazwa.group(1)


def projekty_modrinth(sumy):
    wersje = zapytaj(f"{API}/version_files", {"hashes": sumy, "algorithm": "sha1"})
    ids = sorted({w["project_id"] for w in wersje.values()})
    projekty = {}
    for i in range(0, len(ids), 80):
        lista = urllib.parse.quote(json.dumps(ids[i:i + 80]))
        for p in zapytaj(f"{API}/projects?ids={lista}"):
            projekty[p["id"]] = p
    return {suma: projekty[w["project_id"]] for suma, w in wersje.items()}


def opis_z_info(wpis):
    wiersze = [wpis["short"].rstrip(".") + "."] + wpis.get("info", [])
    if "tutorial" in wpis:
        wiersze.append("Samouczek: " + wpis["tutorial"])
    return [KOLORY.sub("", w) for w in wiersze]


def zrodlo_linku(link):
    if link is None:
        return None
    if "modrinth.com" in link:
        return "Modrinth"
    return "CurseForge (szukaj)" if "search?" in link else "CurseForge"


def buduj(mody_dir, info, opisy, tagi, modrinth):
    """modrinth: suma SHA-1 -> projekt. tagi: tag -> identyfikatory modow.
    Zwraca (lista modow, identyfikatory bez opisu lub tagu)."""
    wg_modid = {w["modid"]: w for k, w in info.items() if k not in POMIJANE_INFO}
    wynik, braki = [], []
    for jar in sorted(pathlib.Path(mody_dir).glob("*.jar")):
        ids, nazwa_jar = identyfikatory(jar)
        klucz = ids[0] if ids else jar.name
        suma = hashlib.sha1(jar.read_bytes()).hexdigest()
        projekt = modrinth.get(suma)
        wpis_info = next((wg_modid[i] for i in ids if i in wg_modid), None)
        wlasny = opisy.get(klucz)
        tagi_moda = [tag for tag, ids_tagu in tagi.items() if klucz in ids_tagu]
        if (wpis_info is None and (wlasny or {}).get("opis") is None) or not tagi_moda:
            braki.append(klucz)
            continue
        wlasny = wlasny or {}
        if projekt:
            link = f"https://modrinth.com/{projekt['project_type']}/{projekt['slug']}"
        else:
            link = wlasny.get("link") or (wpis_info or {}).get("url")
        nazwa = wlasny.get("nazwa") or (projekt["title"] if projekt else None) or nazwa_jar or klucz
        wynik.append({
            "id": klucz,
            "nazwa": nazwa.strip(),
            "opis": opis_z_info(wpis_info) if wpis_info else [wlasny["opis"]],
            "tagi": tagi_moda,
            "link": link,
            "zrodlo": zrodlo_linku(link),
        })
    wynik.sort(key=lambda m: m["nazwa"].casefold())
    return wynik, braki


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--mody", default=INSTANCJA)
    a.add_argument("--info", default=INFO)
    a.add_argument("--cel", default=str(TU.parent / "www" / "mody.json"))
    o = a.parse_args()
    mody_dir = pathlib.Path(o.mody).expanduser()
    info = json.loads(pathlib.Path(o.info).expanduser().read_text())["mods"]
    opisy = json.loads((TU / "mody_opisy.json").read_text())
    tagi = json.loads((TU / "mody_tagi.json").read_text())
    sumy = [hashlib.sha1(j.read_bytes()).hexdigest() for j in sorted(mody_dir.glob("*.jar"))]
    wynik, braki = buduj(mody_dir, info, opisy, tagi, projekty_modrinth(sumy))
    if braki:
        sys.exit("Brak opisu lub tagu dla: " + ", ".join(braki))
    pathlib.Path(o.cel).write_text(json.dumps({"tagi": list(tagi), "mody": wynik}, ensure_ascii=False, indent=1) + "\n")
    print(f"modow: {len(wynik)}, bez linku: {[m['nazwa'] for m in wynik if not m['link']]}")
    for tag in tagi:
        print(f"  {tag}: {sum(tag in m['tagi'] for m in wynik)}")


if __name__ == "__main__":
    main()
