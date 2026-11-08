#!/bin/bash
# Dziala w CT renderu (/opt/mc-mapa/renderuj.sh). Swiat tylko do odczytu w /swiat, wynik w /praca.
# Po udanym renderze zapisuje /praca/wynik: host eksportuje wtedy mape na NAS.
set -euo pipefail

MC=1.20.1
SWIAT=/swiat
PRACA=/praca
OPT=/opt/mc-mapa

rm -f "$PRACA/wynik"
[ -f "$SWIAT/server.properties" ] || { echo "Brak serwera w $SWIAT" >&2; exit 1; }
POZIOM=$(sed -n 's/^level-name=//p' "$SWIAT/server.properties" | tr -d '\r')
POZIOM=${POZIOM:-world}
[ -f "$SWIAT/$POZIOM/level.dat" ] || { echo "Brak świata $SWIAT/$POZIOM" >&2; exit 1; }

mkdir -p "$PRACA/data"
ln -sfn "$SWIAT/$POZIOM" "$PRACA/swiat"
cp -u "$OPT/klient.jar" "$PRACA/data/minecraft-client-$MC.jar"

# Pamiec: CT ma 10 GB, reszta dla systemu i pamieci podrecznej plikow.
cd "$PRACA"
"$OPT/java/bin/java" -Xmx8G -jar "$OPT/bluemap.jar" -c "$OPT/config" -n "$SWIAT/mods" -v "$MC" -r
date +%s > "$PRACA/wynik"
echo "Render gotowy"
