#!/usr/bin/env python3
"""Render mapy na Proxmox-03: swiat dla CT renderu i eksport na TrueNAS.

    mc_mapa.py hook <vmid> <faza>   hookscript CT renderu (MAPA_CT), wolany przez Proxmoxa
    mc_mapa.py eksport              mc-mapa-eksport.service: wynik renderu na NAS

pre-start:  CT Minecrafta musi stac. Snapshot jego dysku (thin, tylko do odczytu) -> SWIAT.
            CT renderu widzi tylko katalog serwera, bez reszty systemu CT Minecrafta.
post-stop:  odmontowanie i usuniecie snapshotu; gdy render sie udal, start eksportu.
eksport:    rsync PRACA/web -> NAS, potem kasuje flage straznika, jesli nie powstala w trakcie.
Ustawienia: /etc/mc-panel/mc.env. Logi: journalctl -t mc-mapa
"""
import os
import subprocess
import sys
import syslog

USTAWIENIA = "/etc/mc-panel/mc.env"
FLAGA_MAPY = "/etc/pve/mc-panel/mapa_do_renderu"
KATALOG_SERWEROW = "/opt/crafty-controller/crafty/crafty-4/servers"   # w CT Minecrafta

SNAP = "mc-mapa-swiat"              # nazwa LV snapshotu
MONTAZ = "/mnt/mc-mapa/snap"        # caly dysk CT Minecrafta, tylko host
SWIAT = "/mnt/mc-mapa/swiat"        # katalog serwera -> mp0 CT renderu (/swiat, ro)
PRACA = "/var/lib/mc-mapa"          # -> mp1 CT renderu (/praca): stan renderu i kafelki
WYNIK = PRACA + "/wynik"            # zapisuje CT renderu po udanym renderze
FLAGA_PRZY_STARCIE = PRACA + "/flaga_przy_starcie"
NAS = "/mnt/nas/mc-mapa"
USLUGA_EKSPORTU = "mc-mapa-eksport.service"
POMIJANE = ("rstate", "sql.php", "*.map")   # stan renderu zostaje tylko lokalnie


def log(tekst, blad=False):
    syslog.syslog(syslog.LOG_ERR if blad else syslog.LOG_INFO, tekst)
    print(tekst, file=sys.stderr if blad else sys.stdout)


def komenda(*args, timeout=300):
    wynik = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if wynik.returncode != 0:
        raise RuntimeError(f"{args[0]}: " + (wynik.stderr.strip() or f"kod {wynik.returncode}"))
    return wynik.stdout


def wczytaj_ustawienia(sciezka=USTAWIENIA):
    d = {}
    with open(sciezka) as f:
        for wiersz in f:
            klucz, znak, wartosc = wiersz.strip().partition("=")
            if znak and not klucz.startswith("#"):
                d[klucz] = wartosc
    return d


def czytaj(sciezka):
    try:
        with open(sciezka) as f:
            return f.read().strip()
    except OSError:
        return None


def wolumen_ct(konfiguracja):
    """Z wyjscia 'pct config' zwraca wolumen rootfs, np. local-lvm:vm-100-disk-0."""
    for wiersz in konfiguracja.splitlines():
        if wiersz.startswith("rootfs:"):
            return wiersz.split(":", 1)[1].strip().split(",")[0]
    raise RuntimeError("brak rootfs w konfiguracji CT")


def grupa_lvm(sciezka):
    """/dev/pve/vm-100-disk-0 -> (pve, vm-100-disk-0)."""
    czesci = sciezka.strip().split("/")
    if len(czesci) != 4 or czesci[:2] != ["", "dev"]:
        raise RuntimeError(f"nieobslugiwany dysk: {sciezka.strip()} (oczekiwany LVM-thin)")
    return czesci[2], czesci[3]


def zamontowane(sciezka):
    return subprocess.run(["mountpoint", "-q", sciezka]).returncode == 0


def posprzataj(vg):
    for sciezka in (SWIAT, MONTAZ):
        if zamontowane(sciezka):
            komenda("umount", sciezka)
    if subprocess.run(["lvs", f"{vg}/{SNAP}"], capture_output=True).returncode == 0:
        komenda("lvremove", "-y", f"{vg}/{SNAP}")


def przed_startem(u):
    mc_ct = u["MC_CT"]
    if "status: stopped" not in komenda("pct", "status", mc_ct):
        raise RuntimeError(f"CT {mc_ct} dziala: swiat nie jest zapisany, render odwolany")
    vg, lv = grupa_lvm(komenda("pvesm", "path", wolumen_ct(komenda("pct", "config", mc_ct))))
    posprzataj(vg)
    for sciezka in (WYNIK, FLAGA_PRZY_STARCIE):
        if os.path.exists(sciezka):
            os.unlink(sciezka)
    # Zapamietujemy flage: eksport nie skasuje nowej, zalozonej po tym renderze.
    flaga = czytaj(FLAGA_MAPY)
    if flaga is not None:
        with open(FLAGA_PRZY_STARCIE, "w") as f:
            f.write(flaga)

    os.makedirs(MONTAZ, exist_ok=True)
    os.makedirs(SWIAT, exist_ok=True)
    komenda("lvcreate", "-s", "-n", SNAP, f"{vg}/{lv}")
    try:
        komenda("lvchange", "-ay", "-K", f"{vg}/{SNAP}")
        # noload: bez odtwarzania dziennika, nic nie trafia na dysk.
        komenda("mount", "-o", "ro,noload", f"/dev/{vg}/{SNAP}", MONTAZ)
        serwer = os.path.join(MONTAZ, KATALOG_SERWEROW.lstrip("/"), u["MC_ID_SERWERA"])
        if not os.path.isfile(os.path.join(serwer, "server.properties")):
            raise RuntimeError(f"brak serwera w snapshocie: {KATALOG_SERWEROW}/<MC_ID_SERWERA>")
        komenda("mount", "--bind", serwer, SWIAT)
        komenda("mount", "-o", "remount,bind,ro", SWIAT)
    except Exception:
        posprzataj(vg)
        raise
    log("swiat gotowy dla renderu (snapshot)")


def po_zatrzymaniu(u):
    vg, _ = grupa_lvm(komenda("pvesm", "path", wolumen_ct(komenda("pct", "config", u["MC_CT"]))))
    posprzataj(vg)
    if os.path.exists(WYNIK):
        log("render zakonczony, eksport na NAS")
        komenda("systemctl", "start", "--no-block", USLUGA_EKSPORTU)
    else:
        log("render nieukonczony: flaga zostaje, dokonczy nastepnym razem", blad=True)


def hook(u, vmid, faza):
    if vmid != u["MAPA_CT"]:
        raise RuntimeError(f"hookscript tylko dla CT {u['MAPA_CT']}, nie {vmid}")
    if faza == "pre-start":
        przed_startem(u)
    elif faza == "post-stop":
        po_zatrzymaniu(u)


def eksport():
    if not os.path.exists(WYNIK):
        log("brak udanego renderu, nic do eksportu")
        return
    if not zamontowane(NAS):
        raise RuntimeError(f"{NAS} nie jest zamontowany")
    pomin = [f"--exclude={w}" for w in POMIJANE]
    komenda("rsync", "-rltp", "--delete", "--chmod=D755,F644", *pomin, PRACA + "/web/", NAS + "/",
            timeout=4 * 3600)
    przy_starcie = czytaj(FLAGA_PRZY_STARCIE)
    if przy_starcie is not None and czytaj(FLAGA_MAPY) == przy_starcie:
        os.unlink(FLAGA_MAPY)
    for sciezka in (WYNIK, FLAGA_PRZY_STARCIE):
        if os.path.exists(sciezka):
            os.unlink(sciezka)
    log("mapa wyeksportowana na NAS")


def main(argv):
    u = wczytaj_ustawienia()
    if len(argv) == 3 and argv[0] == "hook":
        hook(u, argv[1], argv[2])
    elif argv == ["eksport"]:
        eksport()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    syslog.openlog("mc-mapa")
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        # Blad w pre-start zatrzymuje start CT renderu: lepiej brak mapy niz render z niepewnego swiata.
        log(f"blad: {e}", blad=True)
        sys.exit(1)
