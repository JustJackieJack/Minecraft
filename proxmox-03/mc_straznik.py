#!/usr/bin/env python3
"""Wylacza Minecrafta i Proxmox-03 po bezczynnosci. Uruchamiany co minute na Proxmox-03.

Zasada: przy kazdej watpliwosci nic nie wylaczamy.
    --proba  pokazuje stan i decyzje, niczego nie zmienia ani nie zapisuje.
    Plik SERWIS wstrzymuje wszystkie odliczania.
Ustawienia: /etc/mc-panel/mc.env (wzor: .env.template).
Logi: journalctl -t mc-straznik (zmiany stanu i akcje, nie kazda minuta).
"""
import json
import os
import subprocess
import sys
import syslog
import time

WEZEL = "proxmox-03"
USTAWIENIA = "/etc/mc-panel/mc.env"
CT = ADRES_CT = ID_SERWERA = None   # z USTAWIENIA
PORT_MC = 25565

BEZ_GRACZY_S = 10 * 60
PUSTY_HOST_S = 5 * 60
SERWER_NIE_ODPOWIADA_S = 20 * 60
LIMIT_ZATRZYMANIA_S = 10 * 60
START_CO_S = 2 * 60     # statystyki Crafty moga sie spozniac po wyslaniu startu

STAN = "/run/mc-straznik/stan.json"
SERWIS = "/etc/mc-panel/serwis"
TOKEN = "/etc/mc-panel/crafty.token"

WYLACZONY, NIEZNANE = "down", "unknown"

ODLICZANIA = {
    "bez_graczy_od": ("brak graczy", f"zatrzymanie serwera za {BEZ_GRACZY_S // 60} min"),
    "serwer_down_od": ("serwer nie odpowiada", f"zamkniecie CT za {SERWER_NIE_ODPOWIADA_S // 60} min"),
    "pusty_host_od": ("pusty host", f"wylaczenie Proxmox-03 za {PUSTY_HOST_S // 60} min"),
}
OPIS_AKCJI = {
    "start_mc": "wysylam start serwera do Crafty",
    "stop_mc": "zatrzymuje serwer i zapisuje swiat",
    "stop_ct": "zamykam CT",
    "wylacz_host": "wylaczam Proxmox-03",
}

# Oba fragmenty dzialaja wewnatrz CT, bo z hosta w MGMT nie ma drogi do DMZ.
SONDA = r'''
import json, socket, sys
def vi(n):
    w = b""
    while True:
        b = n & 0x7F
        n >>= 7
        w += bytes([b | (0x80 if n else 0)])
        if not n:
            return w
def czytaj_vi(s):
    n = p = 0
    while True:
        c = s.recv(1)
        if not c or p > 35:
            raise ValueError
        n |= (c[0] & 0x7F) << p
        p += 7
        if not c[0] & 0x80:
            return n
adres, port = sys.argv[1], int(sys.argv[2])
try:
    s = socket.create_connection((adres, port), 10)
except ConnectionRefusedError:
    print("down")
    raise SystemExit
except OSError:
    print("unknown")
    raise SystemExit
try:
    s.settimeout(15)
    h = adres.encode()
    p = vi(0) + vi(763) + vi(len(h)) + h + port.to_bytes(2, "big") + vi(1)
    s.sendall(vi(len(p)) + p + vi(1) + b"\0")
    czytaj_vi(s)
    czytaj_vi(s)
    n = czytaj_vi(s)
    d = b""
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c:
            raise ValueError
        d += c
    print(int(json.loads(d)["players"]["online"]))
except Exception:
    print("unknown")
'''

CRAFTY = r'''
import json, ssl, sys, urllib.request
token = sys.stdin.read().strip()
id_serwera, co = sys.argv[1], sys.argv[2]
adres = sys.argv[3] if len(sys.argv) > 3 else "https://127.0.0.1:8443"
statystyki = co == "stats"
r = urllib.request.Request(
    adres + "/api/v2/servers/" + id_serwera + ("/stats" if statystyki else "/action/" + co),
    method="GET" if statystyki else "POST", headers={"Authorization": "Bearer " + token})
with urllib.request.urlopen(r, timeout=30, context=ssl._create_unverified_context()) as o:
    tresc = o.read()
if statystyki:
    d = json.loads(tresc)
    d = d.get("data", d) if "running" not in d else d
    print({True: "running", False: "stopped"}[d["running"]])
'''


def decyduj(st, o, teraz):
    """Zwraca (nowy stan, akcja). Akcja: None, start_mc, stop_mc, stop_ct, wylacz_host, limit_stopu."""
    if o["serwis"]:
        return {}, None

    if not o["ct_dziala"]:
        st = {k: v for k, v in st.items() if k == "pusty_host_od"}
        if o["inni_goscie"] or o["zadania"] or o["sesje"]:
            return {}, None
        st.setdefault("pusty_host_od", teraz)
        if teraz - st["pusty_host_od"] >= PUSTY_HOST_S:
            return {}, "wylacz_host"
        return st, None

    st = {k: v for k, v in st.items() if k != "pusty_host_od"}
    gracze = o["gracze"]

    if "stop_od" in st:
        # Port zamyka sie przed koncem zapisu swiata, wiec decyduje stan procesu w Crafty.
        if gracze == WYLACZONY and o.get("proces_mc") is False:
            if o["zadania"]:
                return st, None
            return {}, "stop_ct"
        if teraz - st["stop_od"] >= LIMIT_ZATRZYMANIA_S:
            del st["stop_od"]
            return st, "limit_stopu"
        return st, None

    if gracze == NIEZNANE:
        return st, None
    if gracze == WYLACZONY:
        st.pop("bez_graczy_od", None)
        st.setdefault("serwer_down_od", teraz)
        if teraz - st["serwer_down_od"] >= SERWER_NIE_ODPOWIADA_S and not o["zadania"]:
            return {}, "stop_ct"
        if o.get("proces_mc") is False and teraz - st.get("start_od", -START_CO_S) >= START_CO_S:
            st["start_od"] = teraz
            return st, "start_mc"
        return st, None

    st.pop("serwer_down_od", None)
    st.pop("start_od", None)
    if gracze > 0:
        st.pop("bez_graczy_od", None)
        return st, None
    st.setdefault("bez_graczy_od", teraz)
    if teraz - st["bez_graczy_od"] >= BEZ_GRACZY_S and not o["zadania"]:
        st["stop_od"] = teraz
        return st, "stop_mc"
    return st, None


def zdarzenia(stary, nowy, akcja):
    """Komunikaty o rozpoczetych i przerwanych odliczaniach."""
    wynik = []
    for klucz, (nazwa, skutek) in ODLICZANIA.items():
        if klucz in nowy and klucz not in stary:
            wynik.append(f"{nazwa}: {skutek}")
        elif klucz in stary and klucz not in nowy and not akcja:
            wynik.append(f"{nazwa}: odliczanie przerwane")
    return wynik


def opis(o):
    return (f"serwis={'tak' if o['serwis'] else 'nie'} ct={'dziala' if o['ct_dziala'] else 'stop'} "
            f"gracze={o['gracze']} proces_mc={o['proces_mc']} "
            f"inni={','.join(o['inni_goscie']) or '-'} zadania={','.join(o['zadania']) or '-'} "
            f"sesje={'tak' if o['sesje'] else 'nie'}")


def log(tekst, blad=False):
    syslog.syslog(syslog.LOG_ERR if blad else syslog.LOG_INFO, tekst)
    if sys.stdout.isatty():
        print(tekst)


def komenda(*args, timeout=60, wejscie=None):
    wynik = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=wejscie)
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or f"kod {wynik.returncode}")
    return wynik.stdout


def pvesh_json(*args):
    return json.loads(komenda("pvesh", "get", *args, "--output-format", "json"))


def odczytaj_gracze():
    try:
        w = komenda("pct", "exec", str(CT), "--", "python3", "-c", SONDA,
                    ADRES_CT, str(PORT_MC), timeout=40).strip()
    except (RuntimeError, subprocess.TimeoutExpired):
        return NIEZNANE
    return int(w) if w.isdigit() else (w if w == WYLACZONY else NIEZNANE)


def crafty(co):
    with open(TOKEN) as f:
        return komenda("pct", "exec", str(CT), "--", "python3", "-c", CRAFTY, ID_SERWERA, co,
                       wejscie=f.read()).strip()


def odczytaj_proces_mc():
    """True/False wg Crafty; None, gdy Crafty jeszcze nie odpowiada."""
    try:
        w = crafty("stats")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    return {"running": True, "stopped": False}.get(w)


def obserwuj():
    ct_dziala, inni = False, []
    for z in pvesh_json("/cluster/resources", "--type", "vm"):
        if z.get("node") == WEZEL and z.get("status") == "running":
            if z.get("vmid") == CT:
                ct_dziala = True
            else:
                inni.append(z.get("name", str(z.get("vmid"))))
    gracze = odczytaj_gracze() if ct_dziala else None
    return {
        "serwis": os.path.exists(SERWIS),
        "ct_dziala": ct_dziala,
        "inni_goscie": inni,
        "zadania": [z.get("type", "?") for z in pvesh_json(f"/nodes/{WEZEL}/tasks", "--source", "active")],
        "sesje": bool(komenda("who").strip()),
        "gracze": gracze,
        "proces_mc": odczytaj_proces_mc() if gracze == WYLACZONY else None,
    }


def wczytaj_stan():
    try:
        with open(STAN) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def zapisz_stan(st):
    tymczasowy = STAN + ".tmp"
    with open(tymczasowy, "w") as f:
        json.dump(st, f)
    os.replace(tymczasowy, STAN)


def wykonaj(akcja):
    if akcja == "stop_mc":
        crafty("stop_server")
    elif akcja == "start_mc":
        crafty("start_server")
    elif akcja == "stop_ct":
        komenda("pct", "shutdown", str(CT), "--timeout", "120", timeout=180)
    elif akcja == "wylacz_host":
        komenda("systemctl", "poweroff")


def wczytaj_ustawienia(sciezka=USTAWIENIA):
    global CT, ADRES_CT, ID_SERWERA
    d = {}
    with open(sciezka) as f:
        for wiersz in f:
            klucz, znak, wartosc = wiersz.strip().partition("=")
            if znak and not klucz.startswith("#"):
                d[klucz] = wartosc
    CT, ADRES_CT, ID_SERWERA = int(d["MC_CT"]), d["MC_ADRES_CT"], d["MC_ID_SERWERA"]


def main():
    wczytaj_ustawienia()
    proba = "--proba" in sys.argv
    obs = obserwuj()
    teraz = time.time()
    stary = wczytaj_stan()
    stary_opis = stary.pop("opis", None)
    st, akcja = decyduj(stary, obs, teraz)
    if proba:
        print("obserwacja:", obs)
        print("stan:", st)
        print("decyzja:", akcja)
        return 0

    obecny_opis = opis(obs)
    if obecny_opis != stary_opis:
        log(obecny_opis)
    for komunikat in zdarzenia(stary, st, akcja):
        log(komunikat)

    if akcja == "limit_stopu":
        log("serwer nie zatrzymal sie w limicie, ponowie probe", blad=True)
    elif akcja:
        log(OPIS_AKCJI[akcja])
        try:
            # Zapis przed wykonaniem: wylaczenie hosta moze przerwac ten proces.
            zapisz_stan(dict(st, opis=obecny_opis))
            wykonaj(akcja)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            log(f"blad ({akcja}): {e}", blad=True)
            st.pop("stop_od", None)
            st.pop("start_od", None)
    zapisz_stan(dict(st, opis=obecny_opis))
    return 0


if __name__ == "__main__":
    syslog.openlog("mc-straznik")
    try:
        sys.exit(main())
    except Exception as e:
        # Blad odczytu nie moze niczego wylaczyc: stan zostaje bez zmian.
        log(f"blad: {e}", blad=True)
        sys.exit(1)
