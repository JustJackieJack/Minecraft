#!/usr/bin/env python3
"""Wylacza Minecrafta i Proxmox-03 po bezczynnosci. Uruchamiany co minute na Proxmox-03.

Zasada: przy kazdej watpliwosci nic nie wylaczamy.
    --proba  pokazuje stan i decyzje, niczego nie zmienia ani nie zapisuje.
    --komunikat TEKST  wysyla TEKST do graczy tak jak ostrzezenia (test), nic wiecej.
    Plik SERWIS wstrzymuje wszystkie odliczania.
Ustawienia: /etc/mc-panel/mc.env (wzor: .env.template).
Logi: journalctl -t mc-straznik (zmiany stanu i akcje, nie kazda minuta).
Co minute zapisuje raport dla lacznika na Proxmox-02 we wspolnym /etc/pve.
Nocna przerwa (NOC_OD-NOC_DO): serwer gasnie, chyba ze gra NOC_WYJATEK. Gracze dostaja ostrzezenia w grze.
Gdy serwer dzialal, zaklada flage dla porannego renderu mapy (mc_wlacz.py --mapa na Proxmox-02).
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
NOC_OD, NOC_DO, NOC_WYJATEK = "23:30", "06:00", None
PORT_MC = 25565

BEZ_GRACZY_S = 10 * 60
PUSTY_HOST_S = 5 * 60
SERWER_NIE_ODPOWIADA_S = 20 * 60
LIMIT_ZATRZYMANIA_S = 10 * 60
START_CO_S = 2 * 60     # statystyki Crafty moga sie spozniac po wyslaniu startu
OSTRZEZENIA_MIN = (30, 15, 5, 1)
NOC_PO_WYJSCIU_S = 5 * 60   # wyjatek wyszedl w nocy: tyle maja pozostali
NOC_POCZATEK_S = 120        # okno na zatrzymanie zaraz po ostrzezeniu "za 1 minute"

STAN = "/run/mc-straznik/stan.json"
SERWIS = "/etc/mc-panel/serwis"
TOKEN = "/etc/mc-panel/crafty.token"
RAPORT = "/etc/pve/mc-panel/straznik.json"
FLAGA_MAPY = "/etc/pve/mc-panel/mapa_do_renderu"
USLUGA_EKSPORTU = "mc-mapa-eksport.service"

WYLACZONY, NIEZNANE = "down", "unknown"

ODLICZANIA = {
    "bez_graczy_od": ("brak graczy", f"zatrzymanie serwera za {BEZ_GRACZY_S // 60} min"),
    "serwer_down_od": ("serwer nie odpowiada", f"zamkniecie CT za {SERWER_NIE_ODPOWIADA_S // 60} min"),
    "pusty_host_od": ("pusty host", f"wylaczenie Proxmox-03 za {PUSTY_HOST_S // 60} min"),
}
OPIS_AKCJI = {
    "start_mc": "wysylam start serwera do Crafty",
    "stop_mc": "zatrzymuje serwer i zapisuje swiat",
    "stop_noc": "nocna przerwa: zatrzymuje serwer i zapisuje swiat",
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
    g = json.loads(d)["players"]
    online = int(g["online"])
    nicki = [str(p["name"]) for p in g.get("sample") or []]
    # Serwer pokazuje najwyzej 12 nickow: niepelna lista to brak wiedzy, a nie brak gracza.
    print(json.dumps({"online": online, "max": int(g.get("max", 0)),
                      "nicki": nicki if len(nicki) >= online else None}))
except Exception:
    print("unknown")
'''

CRAFTY = r'''
import json, ssl, sys, urllib.request
token, _, polecenie = sys.stdin.read().partition("\n")
token = token.strip()
id_serwera, co = sys.argv[1], sys.argv[2]
adres = sys.argv[3] if len(sys.argv) > 3 else "https://127.0.0.1:8443"
statystyki = co == "stats"
sciezka = {"stats": "/stats", "stdin": "/stdin"}.get(co, "/action/" + co)
r = urllib.request.Request(
    adres + "/api/v2/servers/" + id_serwera + sciezka,
    data=polecenie.encode() if co == "stdin" else None,
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
        if o["inni_goscie"] or o["zadania"] or o["sesje"] or o.get("eksport"):
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

    st = nocna_przerwa(st, o, teraz)
    if "noc_stop_o" in st and teraz >= st["noc_stop_o"] and not o["zadania"]:
        st = {k: v for k, v in st.items() if k != "noc_stop_o"}
        st["stop_od"] = teraz
        return st, "stop_noc"

    if gracze > 0:
        st.pop("bez_graczy_od", None)
        return st, None
    st.setdefault("bez_graczy_od", teraz)
    if teraz - st["bez_graczy_od"] >= BEZ_GRACZY_S and not o["zadania"]:
        st["stop_od"] = teraz
        return st, "stop_mc"
    return st, None


def jest_wyjatek(o):
    """True / False, a None, gdy lista nickow jest nieznana."""
    nicki = o.get("nicki")
    if nicki is None or not NOC_WYJATEK:
        return None
    return NOC_WYJATEK in nicki


def nocna_przerwa(st, o, teraz):
    """Ustala noc_stop_o: kiedy w nocy zatrzymac serwer. Przy 0 graczy zostaje zwykle odliczanie."""
    wyjatek = jest_wyjatek(o)
    if not o.get("noc") or wyjatek:
        return {k: v for k, v in st.items() if k != "noc_stop_o"}
    if "noc_stop_o" in st or wyjatek is None or o["gracze"] == 0:
        return st
    # Zaraz po poczatku nocy, gdy gracze dostali juz ostrzezenie "za 1 minute": od razu.
    poczatek = teraz - o["od_nocy_s"]
    ost = st.get("ostrzezenie") or {}
    if (o["od_nocy_s"] < NOC_POCZATEK_S and ost.get("min") == 1
            and abs(ost.get("cel", 0) - poczatek) < 90):
        return dict(st, noc_stop_o=teraz)
    return dict(st, noc_stop_o=teraz + NOC_PO_WYJSCIU_S)


def ostrzezenie(st, o, teraz):
    """Zwraca (stan, tekst komunikatu w grze albo None). Tylko gdy gra ktos poza wyjatkiem."""
    gracze = o.get("gracze")
    if (o.get("serwis") or not o.get("ct_dziala") or type(gracze) is not int or gracze == 0
            or "stop_od" in st or jest_wyjatek(o) is not False):
        return st, None
    if "noc_stop_o" in st:
        cel = st["noc_stop_o"]
    elif not o.get("noc") and o.get("do_nocy_s") is not None:
        cel = round(teraz + o["do_nocy_s"])
    else:
        return st, None
    zostalo = cel - teraz
    k = next((k for k in sorted(OSTRZEZENIA_MIN) if zostalo <= k * 60 + 30), None)
    if k is None or zostalo <= 0:
        return st, None
    ost = st.get("ostrzezenie") or {}
    if abs(ost.get("cel", 0) - cel) < 90 and ost.get("min", 10 ** 6) <= k:
        return st, None
    st = dict(st, ostrzezenie={"cel": cel, "min": k})
    # Prog decyduje, czy wyslac; tekst podaje prawdziwy czas (np. 10 min, gdy wyjatek wyszedl pozniej).
    n = max(1, round(zostalo / 60))
    if n == 1:
        return st, "Nocna przerwa: serwer wyłączy się za 1 minutę. Zapisz postęp!"
    if "noc_stop_o" in st:
        return st, f"Nocna przerwa: serwer wyłączy się za {n} min."
    return st, f"Nocna przerwa: serwer wyłączy się o {NOC_OD} (za {n} min)."


def polecenie_komunikatu(tekst):
    return "tellraw @a " + json.dumps({"text": "[Serwer] " + tekst, "color": "gold"})


def sekundy_godziny(tekst):
    """'23:30' -> sekundy od polnocy; inny format to ValueError."""
    g, znak, mi = tekst.partition(":")
    if not (znak and g.isdigit() and mi.isdigit() and len(mi) == 2 and int(g) < 24 and int(mi) < 60):
        raise ValueError(f"zla godzina: {tekst!r}")
    return int(g) * 3600 + int(mi) * 60


def pora_nocy(teraz, od=None, do=None):
    """Zwraca (czy noc, sekundy od poczatku nocy albo do jej poczatku). Czas lokalny hosta."""
    od, do = od or NOC_OD, do or NOC_DO
    t = time.localtime(teraz)
    s = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
    s_od, s_do = (sekundy_godziny(x) for x in (od, do))
    noc = (s >= s_od or s < s_do) if s_od > s_do else (s_od <= s < s_do)
    return noc, ((s - s_od) if noc else (s_od - s)) % 86400


def raport(st, o, akcja, teraz):
    """Dane dla strony: tylko liczby, bez nazw gosci i zadan."""
    gracze = o["gracze"] if type(o["gracze"]) is int else None
    koniec = None
    if gracze == 0 and "bez_graczy_od" in st and "stop_od" not in st:
        koniec = st["bez_graczy_od"] + BEZ_GRACZY_S
    return {
        "czas": teraz,
        "gracze": gracze,
        "max_graczy": o.get("max_graczy") if gracze is not None else None,
        "koniec_odliczania": koniec,
        "wylaczanie": "stop_od" in st or akcja in ("stop_mc", "stop_noc", "stop_ct"),
    }


def zapisz_raport(dane, sciezka=RAPORT):
    """/etc/pve bez kworum jest tylko do odczytu, wiec wolajacy musi zniesc OSError."""
    os.makedirs(os.path.dirname(sciezka), exist_ok=True)
    tymczasowy = sciezka + ".tmp"
    with open(tymczasowy, "w") as f:
        json.dump(dane, f)
    os.replace(tymczasowy, sciezka)


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
            f"sesje={'tak' if o['sesje'] else 'nie'} eksport={'tak' if o.get('eksport') else 'nie'} "
            f"noc={'tak' if o.get('noc') else 'nie'} wyjatek={ {True: 'gra', False: 'nie', None: '?'}[jest_wyjatek(o)] }")


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


def rozbierz_sonde(w):
    """Zwraca (gracze, max_graczy, nicki). Gracze to liczba, WYLACZONY albo NIEZNANE; nicki lista albo None."""
    if w == WYLACZONY:
        return WYLACZONY, None, None
    try:
        d = json.loads(w)
        online, maks, nicki = d["online"], d["max"], d["nicki"]
    except (ValueError, KeyError, TypeError):
        return NIEZNANE, None, None
    if type(online) is not int or type(maks) is not int or online < 0 or maks < 0:
        return NIEZNANE, None, None
    if not (isinstance(nicki, list) and all(isinstance(n, str) for n in nicki)):
        nicki = None
    return online, maks or None, nicki


def odczytaj_gracze():
    try:
        return rozbierz_sonde(komenda("pct", "exec", str(CT), "--", "python3", "-c", SONDA,
                                      ADRES_CT, str(PORT_MC), timeout=40).strip())
    except (RuntimeError, subprocess.TimeoutExpired):
        return NIEZNANE, None, None


def crafty(co, polecenie=""):
    with open(TOKEN) as f:
        token = f.read().strip()
    return komenda("pct", "exec", str(CT), "--", "python3", "-c", CRAFTY, ID_SERWERA, co,
                   wejscie=token + "\n" + polecenie).strip()


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
    gracze, max_graczy, nicki = odczytaj_gracze() if ct_dziala else (None, None, None)
    teraz = time.time()
    noc, sekundy = pora_nocy(teraz)
    return {
        "serwis": os.path.exists(SERWIS),
        "ct_dziala": ct_dziala,
        "inni_goscie": inni,
        "zadania": [z.get("type", "?") for z in pvesh_json(f"/nodes/{WEZEL}/tasks", "--source", "active")],
        "sesje": bool(komenda("who").strip()),
        "gracze": gracze,
        "max_graczy": max_graczy,
        "proces_mc": odczytaj_proces_mc() if gracze == WYLACZONY else None,
        "nicki": nicki,
        "noc": noc,
        "od_nocy_s": sekundy if noc else None,
        "do_nocy_s": None if noc else sekundy,
        "eksport": usluga_dziala(USLUGA_EKSPORTU),
    }


def usluga_dziala(nazwa):
    # is-active konczy sie kodem innym niz 0, gdy usluga nie dziala: to nie blad.
    w = subprocess.run(["systemctl", "is-active", nazwa], capture_output=True, text=True, timeout=30)
    return w.stdout.strip() in ("activating", "active", "reloading")


def oznacz_mape(o, sciezka=FLAGA_MAPY):
    """Serwer odpowiada, wiec swiat moze sie zmienic: rano trzeba odswiezyc mape."""
    if type(o.get("gracze")) is int and not os.path.exists(sciezka):
        os.makedirs(os.path.dirname(sciezka), exist_ok=True)
        with open(sciezka, "w") as f:
            f.write(str(int(time.time())))
        return True
    return False


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
    if akcja in ("stop_mc", "stop_noc"):
        crafty("stop_server")
    elif akcja == "start_mc":
        crafty("start_server")
    elif akcja == "stop_ct":
        komenda("pct", "shutdown", str(CT), "--timeout", "120", timeout=180)
    elif akcja == "wylacz_host":
        komenda("systemctl", "poweroff")


def wczytaj_ustawienia(sciezka=USTAWIENIA):
    global CT, ADRES_CT, ID_SERWERA, NOC_OD, NOC_DO, NOC_WYJATEK
    d = {}
    with open(sciezka) as f:
        for wiersz in f:
            klucz, znak, wartosc = wiersz.strip().partition("=")
            if znak and not klucz.startswith("#"):
                d[klucz] = wartosc
    CT, ADRES_CT, ID_SERWERA = int(d["MC_CT"]), d["MC_ADRES_CT"], d["MC_ID_SERWERA"]
    NOC_OD, NOC_DO, NOC_WYJATEK = d["NOC_OD"], d["NOC_DO"], d["NOC_WYJATEK"]
    pora_nocy(0)    # zly format godzin ma przerwac prace od razu


def main():
    wczytaj_ustawienia()
    if "--komunikat" in sys.argv:
        tekst = " ".join(sys.argv[sys.argv.index("--komunikat") + 1:]).strip()
        if not tekst:
            raise ValueError("podaj tekst: --komunikat TEKST")
        crafty("stdin", polecenie_komunikatu(tekst))
        log(f"komunikat testowy: {tekst}")
        return 0
    proba = "--proba" in sys.argv
    obs = obserwuj()
    teraz = time.time()
    stary = wczytaj_stan()
    stary_opis = stary.pop("opis", None)
    stary_blad_raportu = stary.pop("blad_raportu", None)
    st, akcja = decyduj(stary, obs, teraz)
    komunikat = None
    if not akcja:
        st, komunikat = ostrzezenie(st, obs, teraz)
    if proba:
        print("obserwacja:", obs)
        print("stan:", st)
        print("decyzja:", akcja)
        print("komunikat w grze:", komunikat)
        return 0

    try:
        if oznacz_mape(obs):
            log("serwer dziala: rano odswieze mape")
    except OSError as e:
        log(f"blad flagi mapy: {e}", blad=True)

    obecny_opis = opis(obs)
    if obecny_opis != stary_opis:
        log(obecny_opis)
    for komunikat in zdarzenia(stary, st, akcja):
        log(komunikat)

    # Raport przed akcja: wylaczenie hosta moze przerwac ten proces. Jego blad niczego nie wstrzymuje.
    blad_raportu = None
    try:
        zapisz_raport(raport(st, obs, akcja, teraz))
    except OSError as e:
        blad_raportu = str(e)
        if blad_raportu != stary_blad_raportu:
            log(f"blad raportu: {e}", blad=True)
    st_zapis = dict(opis=obecny_opis, blad_raportu=blad_raportu)

    if komunikat:
        log(f"komunikat w grze: {komunikat}")
        try:
            crafty("stdin", polecenie_komunikatu(komunikat))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            log(f"blad komunikatu: {e}", blad=True)

    if akcja == "limit_stopu":
        log("serwer nie zatrzymal sie w limicie, ponowie probe", blad=True)
    elif akcja:
        log(OPIS_AKCJI[akcja])
        try:
            # Zapis przed wykonaniem: wylaczenie hosta moze przerwac ten proces.
            zapisz_stan(dict(st, **st_zapis))
            wykonaj(akcja)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            log(f"blad ({akcja}): {e}", blad=True)
            st.pop("stop_od", None)
            st.pop("start_od", None)
    zapisz_stan(dict(st, **st_zapis))
    return 0


if __name__ == "__main__":
    syslog.openlog("mc-straznik")
    try:
        sys.exit(main())
    except Exception as e:
        # Blad odczytu nie moze niczego wylaczyc: stan zostaje bez zmian.
        log(f"blad: {e}", blad=True)
        sys.exit(1)
