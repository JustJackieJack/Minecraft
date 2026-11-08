#!/usr/bin/env bash
# Wysyla wygenerowana mape do CT strony. Dotychczasowe pliki sa nadpisywane, a stare zostaja.
# lost+found nalezy do roota hosta (osobny wolumen), wiec chmod go pomija.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PRACA="${MAPA_PRACA:-praca}"
CT=$(sed -n 's/^MC_CT_STRONA=//p' ../.env | head -1)
[ -n "$CT" ] || { echo "Ustaw MC_CT_STRONA w .env." >&2; exit 1; }
[ -f "$PRACA/web/index.html" ] || { echo "Najpierw uruchom ./renderuj.sh." >&2; exit 1; }

echo "== Wysyłam $(du -sh --exclude=rstate "$PRACA/web" | cut -f1) do CT $CT (dotknij YubiKeya, gdy zacznie migać)"
tar -C "$PRACA/web" --exclude=rstate --exclude=sql.php --exclude='*.map' -cf - . \
  | ssh Proxmox-02 "pct exec $CT -- sh -c 'mkdir -p /srv/mc-mapa && tar -xf - --no-same-owner -C /srv/mc-mapa && find /srv/mc-mapa -mindepth 1 -maxdepth 1 ! -name lost+found -exec chmod -R a+rX {} +'"
echo "== Gotowe"
