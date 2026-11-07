# Minecraft na żądanie

Znajomi włączają Proxmox-03 i serwer Minecraft, a po bezczynności wszystko wyłącza się samo.

| Plik | Gdzie działa | Co robi |
|---|---|---|
| `proxmox-02/mc_wlacz.py` | Proxmox-02, na żądanie | budzi host i uruchamia CT z Minecraftem, chyba że działa inny gość |
| `proxmox-02/mc_most.py` | Proxmox-02, stale | łącznik: publikuje stan dla strony i odbiera sygnał włączenia |
| `proxmox-03/mc_straznik.py` | Proxmox-03, co minutę | po bezczynności zatrzymuje serwer, CT i host; zapisuje raport dla łącznika |
| `strona/` | osobny CT w DMZ | strona statusu z przyciskiem włączania |

## Ustawienia

Adresy, numer CT i ID serwera są w `.env`, którego nie ma w repo. Wzór to `.env.template`:

```bash
cp .env.template .env   # i uzupełnij
```

`./aktualizuj.sh` wgrywa `.env` na Proxmoxy jako `/etc/mc-panel/mc.env`. Tokeny tam nie trafiają: klucz Crafty leży tylko na Proxmox-03.
Test `testy/test_repo.py` nie przepuści wartości z `.env` ani prywatnych adresów IP, adresów MAC i UUID w plikach repo.

## Strona

Serwer w Pythonie bez dodatkowych bibliotek, port 8080, za NPM DMZ. Nie ma tokenów ani dostępu do Proxmoxa.
Z hostem wymienia dane tylko przez katalog `/panel`:

| Plik | Kierunek | Treść |
|---|---|---|
| `/panel/out/status.json` | host → strona | `{"aktualizacja": <czas unix>, "stan": "...", "gracze": 0, "max_graczy": 20, "odliczanie_s": 583}` |
| `/panel/in/start` | strona → host | pusty plik-sygnał; host sprawdza tylko, czy istnieje |

Stany: `wylaczony`, `uruchamianie`, `wlaczony`, `zatrzymywanie`, `zablokowany`.
Status starszy niż 2 min strona pokazuje jako „Brak danych” i nie pozwala wtedy włączyć serwera.

Instalacja i aktualizacja na Debianie (wymaga wypchnięcia repo na GitHub):

```bash
curl -fsSL https://raw.githubusercontent.com/JustJackieJack/Minecraft/main/strona/instaluj.sh | bash
```

Przy pierwszej instalacji powstaje `/etc/mc-strona/strona.env` ze wzoru. Wpisz tam linie `STRONA_` ze swojego `.env`, potem:

```bash
systemctl restart mc-strona
```

### Środowisko testowe

Prawdziwy serwer strony i udawany host, bez Proxmoxa i bez Crafty:

```bash
python3 strona/dev.py
```

- `http://127.0.0.1:18080`: strona. Zmiany w `www/` widać po odświeżeniu.
- `http://127.0.0.1:18081`: sterowanie: stan, gracze, odliczanie.

Przycisk „Włącz serwer” przechodzi cały cykl: start, działanie, odliczanie przy 0 graczy, wyłączanie.
Linie `STRONA_` są brane z `.env`, jeśli istnieje.

Tekstura paneli jest własna (`strona/narzedzia/generuj_grafiki.py`), tło to `www/Dirt_background_JE1.webp`, a font Jersey 15 ma licencję OFL.

Lista modów to `strona/www/mody.json`. Po zmianie paczki wygeneruj ją ponownie z instancji Prism:

```bash
python3 strona/narzedzia/generuj_mody.py
```

Linki bierze z Modrinth po sumie pliku, opisy z `info.json` moda Ci_Panowie i `strona/narzedzia/mody_opisy.json`, a tagi z `strona/narzedzia/mody_tagi.json`.
Nowy mod bez opisu lub tagu przerywa generowanie: dopisz go w tych plikach.

## Paczka i instalator

`instalator/` to jeden program w Go, kompilowany na Windows, macOS i Linux. Nie wymaga niczego od gracza.
Dane bierze z `paczka/` w tym repo, a wersje zapisuje w `.ci_panowie.json` w folderze minecraft.

Po zmianie modów, configu albo skinów, w folderze minecraft swojej instancji:

```bash
python3 ~/WebstormProjects/Minecraft/paczka/przygotuj.py
```

Skrypt tworzy `paczka/paczka.json`, trzy archiwa i kopię naszego moda, odświeża listę modów na stronie i ostrzega przed prywatnymi danymi w configu.
Mody są pobierane z Modrinth, a gdy ich tam nie ma, z CurseForge (lista w `paczka/curseforge.json`). Potem zacommituj i wypchnij `paczka/`.

Instalator wydaje GitHub Actions po każdej zmianie w `instalator/`. Testy lokalne: `cd instalator && go test ./...`.

Na serwerze, przy zatrzymanym serwerze, jako jego właściciel:

```bash
./ci_panowie_instalator_linux --serwer
```

Serwer dostaje config z `defaultconfigs`, bez skinów i modów z `paczka/tylko_klient.txt`. Kolejne flagi: `--tak` (bez pytań), `--folder X`.

## Mapa

Mapa jest generowana z góry na Twoim komputerze, z lokalnego świata, przez [BlueMap CLI](https://bluemap.bluecolored.de/). Wymaga Javy 21+ i ustawień `MAPA_*` oraz `MC_CT_STRONA` w `.env`.

```bash
./mapa/renderuj.sh   # pierwszy raz może trwać kilka godzin, później tylko zmiany
./mapa/wyslij.sh     # wysyła wynik do CT strony
```

Zamknij wcześniej Minecrafta. BlueMap pracuje na kopii świata w `mapa/praca/` (poza repo) i używa pliku klienta z Twojej instalacji gry.
Strona serwuje pliki z `/srv/mc-mapa` pod `/mapa-dane/`. Podgląd lokalny: `python3 strona/dev.py`, zakładka Mapa.

## Czasy

- 10 min bez graczy: zatrzymanie serwera i CT.
- 5 min pustego hosta: wyłączenie Proxmox-03.
- 20 min bez odpowiedzi serwera: zatrzymanie CT.

Gdy CT działa, a serwer jest wyłączony, strażnik wysyła do Crafty komendę startu (najwyżej co 2 min).
Dotyczy to też ręcznego zatrzymania w panelu, więc do prac przy serwerze włącz tryb serwisowy.

Host się nie wyłączy, gdy działa inny gość, trwa zadanie Proxmoxa (np. backup) albo ktoś jest zalogowany przez SSH.
Przy błędzie odczytu nic się nie wyłącza.

Tryb serwisowy na Proxmox-03 wstrzymuje wszystkie odliczania:

```bash
touch /etc/mc-panel/serwis   # włącz
rm /etc/mc-panel/serwis      # wyłącz
```

## Testy lokalne

```bash
python3 -B -m unittest testy/test_mc.py testy/test_strona.py testy/test_paczka.py testy/test_repo.py
```

## Logi

Logi trafiają do journala, więc widać je też w panelu: węzeł → **System** → **System Log**.
Strażnik zapisuje zmiany stanu i akcje, a nie każdą minutę.

```bash
journalctl -t mc-straznik -f            # Proxmox-03, na żywo
journalctl -t mc-straznik --since today # Proxmox-03, dzisiaj
journalctl -t mc-straznik -p err        # Proxmox-03, tylko błędy
journalctl -t mc-wlacz --since today    # Proxmox-02
journalctl -t mc-most -f                # Proxmox-02, łącznik strony
```

## Aktualizacja skryptów

Uruchamiana lokalnie. Najpierw odpala testy, a wgrywa tylko zmienione pliki.
Każdy host wymaga dotknięcia YubiKeya raz na uruchomienie.

```bash
./aktualizuj.sh             # wgraj zmiany
./aktualizuj.sh --sprawdz   # pokaż różnice, niczego nie zmieniaj
./aktualizuj.sh --obudz     # najpierw obudź Proxmox-03, jeśli jest wyłączony
```

Skrypt nie włącza timera, nie dotyka tokenu ani ustawień CT. Działający timer restartuje tylko po zmianie jego plików.

## Wdrożenie

1. W panelu Crafty włącz **Server Auto Start** dla serwera i utwórz klucz API.
2. Uzupełnij `.env`; `MC_ID_SERWERA` to ID z adresu serwera w panelu.
3. Wgraj pliki z tego katalogu:

   ```bash
   ./aktualizuj.sh
   ```

4. Na Proxmox-03 zapisz klucz i wyłącz autostart CT:

   ```bash
   install -d -m 700 /etc/mc-panel
   read -rs T; ( umask 077; printf '%s' "$T" > /etc/mc-panel/crafty.token ); unset T
   pct set "$(sed -n 's/^MC_CT=//p' /etc/mc-panel/mc.env)" --onboot 0
   ```

5. Na Proxmox-03 sprawdź decyzję bez wykonywania jej (CT i serwer powinny działać):

   ```bash
   mc_straznik.py --proba
   ```

   Przy działającym serwerze `gracze` musi być liczbą.

6. Dopiero wtedy włącz strażnika:

   ```bash
   systemctl enable --now mc-straznik.timer
   journalctl -t mc-straznik -f
   ```

## Wdrożenie strony i łącznika

Działanie: strażnik pisze co minutę raport do `/etc/pve/mc-panel/straznik.json` (wspólne dla klastra), a łącznik na Proxmox-02
łączy go z danymi klastra i co 10 s zapisuje `status.json`. Zapisuje też stan po kliknięciu. Strona nie dostaje nazw gości.

1. Wgraj pliki: `./aktualizuj.sh`.
2. Na Proxmox-02 włącz katalogi wymiany:

   ```bash
   systemctl enable --now var-lib-mcpanel-in.mount var-lib-mcpanel-out.mount
   ```

3. Utwórz nieuprzywilejowany CT z Debianem w DMZ, zainstaluj stronę (patrz „Strona”) i podłącz katalogi, a potem zrestartuj CT:

   ```bash
   pct set <id> --mp0 /var/lib/mcpanel/in,mp=/panel/in --mp1 /var/lib/mcpanel/out,mp=/panel/out,ro=1
   pct reboot <id>
   ```

4. Firewall CT: `tcp 8080` tylko z NPM DMZ i admina. W NPM DMZ dodaj proxy host na `http://<ip>:8080`.
5. Na Proxmox-02 włącz łącznik:

   ```bash
   systemctl enable --now mc-most.service
   journalctl -t mc-most -f
   ```

## Test na żywo

1. Na Proxmox-02: `mc_wlacz.py`. Host i serwer powinny wstać.
2. Bez graczy po 10 min serwer powinien zapisać świat i się zatrzymać, a CT zamknąć.
3. Po kolejnych 5 min Proxmox-03 powinien się wyłączyć.
4. Znów `mc_wlacz.py`: druga pobudka sprawdza, czy Wake-on-LAN przetrwał restart.
5. Uruchom Windows 7 i wywołaj `mc_wlacz.py`: oczekiwany kod `3`, CT nie startuje.
