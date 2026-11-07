#!/usr/bin/env bash
# Wgrywa skrypty na Proxmoxy. Uruchamiany lokalnie, zob. README.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

HOSTY=(Proxmox-02 Proxmox-03)
# host, plik lokalny, cel, uprawnienia
PLIKI=(
  "Proxmox-02 .env /etc/mc-panel/mc.env 600"
  "Proxmox-03 .env /etc/mc-panel/mc.env 600"
  "Proxmox-02 proxmox-02/mc_wlacz.py /usr/local/sbin/mc_wlacz.py 755"
  "Proxmox-02 proxmox-02/mc_most.py /usr/local/sbin/mc_most.py 755"
  "Proxmox-02 proxmox-02/mc-most.service /etc/systemd/system/mc-most.service 644"
  "Proxmox-02 proxmox-02/mc-wlacz.service /etc/systemd/system/mc-wlacz.service 644"
  "Proxmox-02 proxmox-02/var-lib-mcpanel-in.mount /etc/systemd/system/var-lib-mcpanel-in.mount 644"
  "Proxmox-02 proxmox-02/var-lib-mcpanel-out.mount /etc/systemd/system/var-lib-mcpanel-out.mount 644"
  "Proxmox-03 proxmox-03/mc_straznik.py /usr/local/sbin/mc_straznik.py 755"
  "Proxmox-03 proxmox-03/mc-straznik.service /etc/systemd/system/mc-straznik.service 644"
  "Proxmox-03 proxmox-03/mc-straznik.timer /etc/systemd/system/mc-straznik.timer 644"
)

sprawdz=0
obudz=0
for arg in "$@"; do
  case "$arg" in
    --sprawdz) sprawdz=1 ;;
    --obudz) obudz=1 ;;
    *) echo "użycie: $0 [--sprawdz] [--obudz]" >&2; exit 2 ;;
  esac
done

# Puste lub brakujące ustawienie zepsułoby skrypty dopiero na hoście.
if [ ! -f .env ]; then
  echo "Brak pliku .env. Skopiuj .env.template do .env i uzupełnij. Niczego nie zmieniono." >&2
  exit 1
fi
while IFS== read -r klucz _; do
  case "$klucz" in ''|\#*) continue ;; esac
  if ! grep -q "^$klucz=." .env; then
    echo "W .env brakuje wartości: $klucz. Niczego nie zmieniono." >&2
    exit 1
  fi
done < .env.template

# Wspólne połączenie SSH: YubiKey tylko raz na host.
GNIAZDA=$(mktemp -d)
SSH_OPCJE=(-o ControlMaster=auto -o "ControlPath=$GNIAZDA/%h" -o ControlPersist=300 -o ConnectTimeout=5)
sprzatanie() {
  for h in "${HOSTY[@]}"; do
    ssh "${SSH_OPCJE[@]}" -O exit "$h" 2>/dev/null || true
  done
  rm -rf "$GNIAZDA"
}
trap sprzatanie EXIT

zdalnie() { ssh "${SSH_OPCJE[@]}" "$@"; }

echo "== Testy"
python3 -B -m unittest testy/test_mc.py testy/test_strona.py testy/test_paczka.py testy/test_repo.py 2>&1 | tail -1

echo "== Połączenie (dotknij YubiKeya, gdy zacznie migać)"
zdalnie -n Proxmox-02 true
if ! zdalnie -n Proxmox-03 true; then
  if [ "$obudz" -eq 0 ]; then
    echo "Brak połączenia z Proxmox-03. Jeśli jest wyłączony, użyj --obudz. Niczego nie zmieniono." >&2
    exit 1
  fi
  echo "Budzę Proxmox-03"
  zdalnie -n Proxmox-02 pvenode wakeonlan proxmox-03
  for _ in $(seq 1 36); do
    sleep 5
    zdalnie -n Proxmox-03 true 2>/dev/null && break
  done
  zdalnie -n Proxmox-03 true
fi

roznice=0
jednostki_02=0
jednostki_03=0
most=0
for wpis in "${PLIKI[@]}"; do
  read -r host zrodlo cel prawa <<< "$wpis"
  lokalny=$(sha256sum "$zrodlo" | cut -d' ' -f1)
  zdalny=$(zdalnie -n "$host" "sha256sum '$cel' 2>/dev/null | cut -d' ' -f1" || true)
  if [ "$lokalny" = "$zdalny" ]; then
    echo "bez zmian     $host:$cel"
    continue
  fi
  roznice=$((roznice + 1))
  if [ "$sprawdz" -eq 1 ]; then
    echo "do aktualizacji $host:$cel"
    continue
  fi
  zdalnie "$host" "mkdir -p \"\$(dirname '$cel')\" && cat > '$cel.nowy' && chmod $prawa '$cel.nowy' && mv -f '$cel.nowy' '$cel'" < "$zrodlo"
  zdalny=$(zdalnie -n "$host" "sha256sum '$cel' | cut -d' ' -f1")
  if [ "$lokalny" != "$zdalny" ]; then
    echo "BŁĄD: suma się nie zgadza: $host:$cel" >&2
    exit 1
  fi
  echo "zaktualizowano $host:$cel"
  case "$host:$cel" in
    Proxmox-02:/etc/systemd/*) jednostki_02=1; most=1 ;;
    Proxmox-03:/etc/systemd/*) jednostki_03=1 ;;
    Proxmox-02:*) most=1 ;;   # działający łącznik trzyma stary kod i stare ustawienia
  esac
done

if [ "$jednostki_02" -eq 1 ]; then
  zdalnie -n Proxmox-02 "systemctl daemon-reload"
fi
if [ "$most" -eq 1 ]; then
  zdalnie -n Proxmox-02 "systemctl try-restart mc-most.service"
fi
if [ "$jednostki_03" -eq 1 ]; then
  zdalnie -n Proxmox-03 "systemctl daemon-reload && systemctl try-restart mc-straznik.timer"
fi

echo "== Gotowe: $roznice plik(ów) różnych"
stan=$(zdalnie -n Proxmox-03 "systemctl is-active mc-straznik.timer" || true)
echo "Timer strażnika na Proxmox-03: $stan"
stan=$(zdalnie -n Proxmox-02 "systemctl is-active mc-most.service" || true)
echo "Łącznik na Proxmox-02: $stan"
