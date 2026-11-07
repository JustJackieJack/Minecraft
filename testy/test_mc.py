import http.server
import importlib.util
import json
import pathlib
import socket
import subprocess
import sys
import threading
import unittest

KORZEN = pathlib.Path(__file__).parent.parent


def laduj(sciezka, nazwa):
    spec = importlib.util.spec_from_file_location(nazwa, KORZEN / sciezka)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


s = laduj("proxmox-03/mc_straznik.py", "mc_straznik")
w = laduj("proxmox-02/mc_wlacz.py", "mc_wlacz")


def obs(**zm):
    o = dict(serwis=False, ct_dziala=True, inni_goscie=[], zadania=[], sesje=False, gracze=0, proces_mc=None)
    o.update(zm)
    return o


class Ustawienia(unittest.TestCase):
    WZOR = KORZEN / ".env.template"

    def tearDown(self):
        s.CT = s.ADRES_CT = s.ID_SERWERA = None

    def test_straznik_czyta_wzor(self):
        s.wczytaj_ustawienia(self.WZOR)
        self.assertEqual((s.CT, s.ADRES_CT), (100, "192.0.2.10"))
        self.assertEqual(s.ID_SERWERA, "00000000-0000-0000-0000-000000000000")

    def test_wlaczanie_czyta_wzor(self):
        self.assertEqual(w.wczytaj_ustawienia(self.WZOR)["MC_CT"], "100")

    def test_brak_pliku_przerywa_pracy_straznika(self):
        with self.assertRaises(OSError):
            s.wczytaj_ustawienia(KORZEN / "nie-ma-takiego-pliku")


class Decyzje(unittest.TestCase):
    def test_gracz_kasuje_odliczanie(self):
        st, a = s.decyduj({"bez_graczy_od": 0}, obs(gracze=2), 9999)
        self.assertEqual((st, a), ({}, None))

    def test_bez_graczy_przed_i_po_limicie(self):
        st, a = s.decyduj({}, obs(), 100)
        self.assertEqual((st["bez_graczy_od"], a), (100, None))
        _, a = s.decyduj(st, obs(), 100 + s.BEZ_GRACZY_S - 1)
        self.assertIsNone(a)
        st, a = s.decyduj(st, obs(), 100 + s.BEZ_GRACZY_S)
        self.assertEqual(a, "stop_mc")
        self.assertIn("stop_od", st)

    def test_nie_zatrzymuje_w_trakcie_backupu(self):
        _, a = s.decyduj({"bez_graczy_od": 0}, obs(zadania=["vzdump"]), s.BEZ_GRACZY_S)
        self.assertIsNone(a)

    def test_nieznany_stan_nic_nie_robi(self):
        st0 = {"bez_graczy_od": 0}
        st, a = s.decyduj(st0, obs(gracze=s.NIEZNANE), 10 ** 6)
        self.assertEqual((st, a), (st0, None))

    def test_po_stopie_zamyka_ct_gdy_serwer_wylaczony(self):
        st0 = {"bez_graczy_od": 0, "stop_od": 100}
        self.assertEqual(s.decyduj(st0, obs(gracze=0), 150)[1], None)
        self.assertEqual(s.decyduj(st0, obs(gracze=s.WYLACZONY, proces_mc=False), 150), ({}, "stop_ct"))

    def test_port_zamkniety_ale_swiat_sie_zapisuje(self):
        st0 = {"stop_od": 100}
        for proces in (True, None):
            self.assertEqual(s.decyduj(st0, obs(gracze=s.WYLACZONY, proces_mc=proces), 150), (st0, None))

    def test_limit_stopu_pozwala_ponowic(self):
        st, a = s.decyduj({"bez_graczy_od": -s.BEZ_GRACZY_S, "stop_od": 100}, obs(), 100 + s.LIMIT_ZATRZYMANIA_S)
        self.assertEqual(a, "limit_stopu")
        self.assertEqual(s.decyduj(st, obs(), 100 + s.LIMIT_ZATRZYMANIA_S + 60)[1], "stop_mc")

    def test_serwer_nie_wstal(self):
        st, a = s.decyduj({}, obs(gracze=s.WYLACZONY), 0)
        self.assertIsNone(a)
        self.assertEqual(s.decyduj(st, obs(gracze=s.WYLACZONY), s.SERWER_NIE_ODPOWIADA_S)[1], "stop_ct")

    def test_start_gdy_crafty_dziala_a_serwer_nie(self):
        o = obs(gracze=s.WYLACZONY, proces_mc=False)
        st, a = s.decyduj({}, o, 1000)
        self.assertEqual(a, "start_mc")
        self.assertIsNone(s.decyduj(st, o, 1000 + s.START_CO_S - 1)[1])
        self.assertEqual(s.decyduj(st, o, 1000 + s.START_CO_S)[1], "start_mc")

    def test_bez_startu_gdy_crafty_milczy_lub_serwer_startuje(self):
        for proces in (None, True):
            self.assertIsNone(s.decyduj({}, obs(gracze=s.WYLACZONY, proces_mc=proces), 1000)[1])

    def test_bez_startu_po_komendzie_stop(self):
        st0 = {"stop_od": 1000}
        o = obs(gracze=0)
        self.assertEqual(s.decyduj(st0, o, 1060), (st0, None))
        self.assertEqual(s.decyduj(st0, obs(gracze=s.WYLACZONY, proces_mc=False), 1060)[1], "stop_ct")

    def test_serwis_blokuje_start(self):
        self.assertIsNone(s.decyduj({}, obs(serwis=True, gracze=s.WYLACZONY, proces_mc=False), 1000)[1])

    def test_pusty_host(self):
        o = obs(ct_dziala=False, gracze=None)
        st, a = s.decyduj({}, o, 0)
        self.assertIsNone(a)
        self.assertEqual(s.decyduj(st, o, s.PUSTY_HOST_S), ({}, "wylacz_host"))

    def test_host_nie_wylacza_sie_gdy_cos_dziala(self):
        for zm in ({"inni_goscie": ["Kali"]}, {"zadania": ["vzdump"]}, {"sesje": True}):
            o = obs(ct_dziala=False, gracze=None, **zm)
            self.assertEqual(s.decyduj({"pusty_host_od": 0}, o, 10 ** 6), ({}, None))

    def test_serwis_wstrzymuje_wszystko(self):
        self.assertEqual(s.decyduj({"bez_graczy_od": 0}, obs(serwis=True), 10 ** 6), ({}, None))




class Logi(unittest.TestCase):
    def test_start_i_przerwanie_odliczania(self):
        self.assertEqual(s.zdarzenia({}, {"bez_graczy_od": 1}, None),
                         ["brak graczy: zatrzymanie serwera za 10 min"])
        self.assertEqual(s.zdarzenia({"bez_graczy_od": 1}, {}, None), ["brak graczy: odliczanie przerwane"])

    def test_akcja_to_nie_przerwanie(self):
        self.assertEqual(s.zdarzenia({"pusty_host_od": 1}, {}, "wylacz_host"), [])

    def test_opis_pokazuje_powod(self):
        o = obs(ct_dziala=False, gracze=None, inni_goscie=["Inny-999"], zadania=["vzdump"])
        self.assertIn("inni=Inny-999 zadania=vzdump", s.opis(o))

    def test_kazda_akcja_ma_opis(self):
        for akcja in ("start_mc", "stop_mc", "stop_ct", "wylacz_host"):
            self.assertIn(akcja, s.OPIS_AKCJI)


def s_vi(n):
    w_ = b""
    while True:
        b = n & 0x7F
        n >>= 7
        w_ += bytes([b | (0x80 if n else 0)])
        if not n:
            return w_


class Sonda(unittest.TestCase):
    def odpal(self, port):
        return subprocess.run([sys.executable, "-c", s.SONDA, "127.0.0.1", str(port)],
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def serwer(self, odpowiedz):
        g = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(g.close)

        def obsluga():
            k, _ = g.accept()
            k.recv(4096)
            k.sendall(odpowiedz)
            k.close()
        threading.Thread(target=obsluga, daemon=True).start()
        return g.getsockname()[1]

    def test_duza_odpowiedz_forge(self):
        tresc = b'{"players":{"online":3,"max":20},"forgeData":"' + b"x" * 300000 + b'"}'
        d = s_vi(0) + s_vi(len(tresc)) + tresc
        self.assertEqual(self.odpal(self.serwer(s_vi(len(d)) + d)), "3")

    def test_port_zamkniety(self):
        g = socket.create_server(("127.0.0.1", 0))
        port = g.getsockname()[1]
        g.close()
        self.assertEqual(self.odpal(port), "down")

    def test_smieci_to_nieznane(self):
        self.assertEqual(self.odpal(self.serwer(b"\x00")), "unknown")


class Crafty(unittest.TestCase):
    def odpal(self, co, odpowiedz=b"{}"):
        zapytania = []

        class H(http.server.BaseHTTPRequestHandler):
            def obsluz(self):
                zapytania.append((self.command, self.path, self.headers["Authorization"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(odpowiedz)
            do_GET = do_POST = obsluz

            def log_message(self, *_):
                pass
        g = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.addCleanup(g.server_close)
        threading.Thread(target=g.handle_request, daemon=True).start()
        w = subprocess.run([sys.executable, "-c", s.CRAFTY, "ID", co, f"http://127.0.0.1:{g.server_port}"],
                           input="TAJNE\n", capture_output=True, text=True, timeout=30)
        return w, zapytania

    def test_akcje(self):
        for co in ("start_server", "stop_server"):
            w, z = self.odpal(co)
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertEqual(z, [("POST", f"/api/v2/servers/ID/action/{co}", "Bearer TAJNE")])

    def test_statystyki(self):
        for tresc, oczekiwane in (({"status": "ok", "data": {"running": False}}, "stopped"),
                                  ({"status": "ok", "running": True}, "running")):
            w, z = self.odpal("stats", json.dumps(tresc).encode())
            self.assertEqual(w.stdout.strip(), oczekiwane, w.stderr)
            self.assertEqual(z[0][:2], ("GET", "/api/v2/servers/ID/stats"))

    def test_zle_statystyki_to_blad(self):
        self.assertNotEqual(self.odpal("stats", b'{"status":"ok"}')[0].returncode, 0)


class FalszywyProxmox:
    def __init__(self, stany, blad_startu=0):
        self.stany, self.blad_startu = list(stany), blad_startu
        self.wol, self.starty = 0, 0

    def stan(self):
        return self.stany.pop(0) if len(self.stany) > 1 else self.stany[0]

    def obudz(self):
        self.wol += 1

    def uruchom_ct(self):
        self.starty += 1
        if self.starty <= self.blad_startu:
            raise RuntimeError("CT is locked (backup)")


class Wlaczanie(unittest.TestCase):
    def uruchom(self, px):
        t = [0]

        def spij(x):
            t[0] += x
        return w.wlacz(px, zegar=lambda: t[0], spij=spij, log=lambda *_: None)

    def test_blokada(self):
        px = FalszywyProxmox([(True, False, ["Inny-999"])])
        self.assertEqual(self.uruchom(px), w.ZABLOKOWANE)
        self.assertEqual(px.starty, 0)

    def test_juz_dziala(self):
        px = FalszywyProxmox([(True, True, [])])
        self.assertEqual(self.uruchom(px), w.OK)
        self.assertEqual((px.wol, px.starty), (0, 0))

    def test_budzi_powtarza_wol_i_startuje(self):
        px = FalszywyProxmox([(False, False, [])] * 7 + [(True, False, [])] + [(True, True, [])])
        self.assertEqual(self.uruchom(px), w.OK)
        self.assertEqual((px.wol, px.starty), (3, 1))

    def test_backup_blokuje_ct_a_start_jest_ponawiany(self):
        px = FalszywyProxmox([(True, False, [])] * 3 + [(True, True, [])], blad_startu=2)
        self.assertEqual(self.uruchom(px), w.OK)
        self.assertEqual(px.starty, 3)

    def test_blokada_po_przebudzeniu(self):
        px = FalszywyProxmox([(False, False, []), (True, False, ["Kali"])])
        self.assertEqual(self.uruchom(px), w.ZABLOKOWANE)
        self.assertEqual(px.starty, 0)

    def test_limit_czasu(self):
        self.assertEqual(self.uruchom(FalszywyProxmox([(False, False, [])])), w.CZAS)


if __name__ == "__main__":
    unittest.main()
