#!/usr/bin/env bash
# Instaluje strone statusu na Debianie. Mozna uruchamiac wielokrotnie: to tez aktualizacja.
#   curl -fsSL https://raw.githubusercontent.com/JustJackieJack/Minecraft/main/strona/instaluj.sh | bash
set -euo pipefail

ARCHIWUM="${MC_ARCHIWUM:-https://github.com/JustJackieJack/Minecraft/archive/refs/heads/main.tar.gz}"
CEL=/opt/mc-strona
UID_STRONY=1500   # staly, bo host nadaje wedlug niego prawa do /panel/in

main() {
  [ "$(id -u)" -eq 0 ] || { echo "Uruchom jako root." >&2; exit 1; }

  export DEBIAN_FRONTEND=noninteractive
  echo "== Pakiety"
  apt-get update -qq </dev/null
  apt-get install -y -qq --no-install-recommends python3 curl ca-certificates </dev/null >/dev/null

  echo "== Pliki"
  local zrodlo tymczasowy=""
  zrodlo=$(cd "$(dirname "${BASH_SOURCE[0]:-/nonexistent/x}")" 2>/dev/null && pwd || true)
  if [ -z "$zrodlo" ] || [ ! -f "$zrodlo/serwer.py" ]; then
    tymczasowy=$(mktemp -d)
    trap 'rm -rf "${tymczasowy:-}"' EXIT
    curl -fsSL "$ARCHIWUM" | tar -xz -C "$tymczasowy" --strip-components=1
    zrodlo="$tymczasowy/strona"
  fi

  if ! id mc-strona >/dev/null 2>&1; then
    groupadd --gid "$UID_STRONY" mc-strona
    useradd --uid "$UID_STRONY" --gid "$UID_STRONY" --no-create-home --shell /usr/sbin/nologin mc-strona
  fi

  local nowy="$CEL.nowy"
  rm -rf "$nowy"
  mkdir -p "$nowy"
  cp -r "$zrodlo/serwer.py" "$zrodlo/www" "$nowy/"
  chown -R root:root "$nowy"
  chmod -R a+rX,go-w "$nowy"
  rm -rf "$CEL.stary"
  [ ! -e "$CEL" ] || mv "$CEL" "$CEL.stary"
  mv "$nowy" "$CEL"
  rm -rf "$CEL.stary"

  # Docelowo to punkty montowania z hosta. Na zamontowanych katalogach niczego nie zmieniamy.
  [ -d /panel/in ] || install -d -m 700 -o mc-strona -g mc-strona /panel/in
  [ -d /panel/out ] || install -d -m 755 /panel/out
  install -d -m 755 /srv/mc-mapa   # pliki wgrywa mapa/wyslij.sh

  echo "== Ustawienia"
  local ustawienia=/etc/mc-strona/strona.env
  if [ ! -e "$ustawienia" ]; then
    install -d -m 755 /etc/mc-strona
    grep '^STRONA_' "$zrodlo/../.env.template" > "$ustawienia"
    echo "UWAGA: uzupelnij $ustawienia i wykonaj: systemctl restart mc-strona"
  fi

  echo "== Usluga"
  install -m 644 "$zrodlo/mc-strona.service" /etc/systemd/system/mc-strona.service
  systemctl daemon-reload
  systemctl enable -q mc-strona
  systemctl restart mc-strona

  local i
  for i in $(seq 1 10); do
    if curl -fs -o /dev/null http://127.0.0.1:8080/api/status; then
      echo "== Gotowe: strona działa na porcie 8080"
      return 0
    fi
    sleep 1
  done
  echo "Strona nie odpowiada. Sprawdź: journalctl -u mc-strona" >&2
  exit 1
}

main "$@"
