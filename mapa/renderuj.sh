#!/usr/bin/env bash
# Generuje mape 3D z lokalnego swiata. Kolejne uruchomienia renderuja tylko zmienione chunki.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

WERSJA_BM=5.29
SUMA_BM=e830767fa6364b52b9310fb598508ebde7eec5ebe8360a989e240d991af89a3a
MC=1.20.1
PRACA="${MAPA_PRACA:-praca}"
ENV_PLIK="${MAPA_ENV:-../.env}"

wartosc() { sed -n "s/^$1=//p" "$ENV_PLIK" | head -1; }
INSTANCJA=$(wartosc MAPA_INSTANCJA)
SWIAT=$(wartosc MAPA_SWIAT)
[ -n "$INSTANCJA" ] && [ -n "$SWIAT" ] || { echo "Ustaw MAPA_INSTANCJA i MAPA_SWIAT w .env." >&2; exit 1; }
ZRODLO="$INSTANCJA/saves/$SWIAT"
KLIENT="$INSTANCJA/../../../libraries/com/mojang/minecraft/$MC/minecraft-$MC-client.jar"
[ -f "$ZRODLO/level.dat" ] || { echo "Nie ma świata: $ZRODLO" >&2; exit 1; }
[ -f "$KLIENT" ] || { echo "Nie ma klienta Minecrafta: $KLIENT" >&2; exit 1; }

if pgrep -f "java.*instances/$(basename "$(dirname "$INSTANCJA")")" >/dev/null; then
  echo "Zamknij Minecrafta: świat musi być zapisany." >&2
  exit 1
fi

mkdir -p "$PRACA/data" "$PRACA/swiat"
JAR="$PRACA/bluemap-$WERSJA_BM-cli.jar"
if [ ! -f "$JAR" ]; then
  echo "== Pobieram BlueMap $WERSJA_BM"
  curl -fsSL -o "$JAR.nowy" "https://github.com/BlueMap-Minecraft/BlueMap/releases/download/v$WERSJA_BM/bluemap-$WERSJA_BM-cli.jar"
  echo "$SUMA_BM  $JAR.nowy" | sha256sum -c --quiet
  mv "$JAR.nowy" "$JAR"
fi

# BlueMap pracuje na kopii, wiec zapis gry zostaje nietkniety.
echo "== Kopiuję świat"
rsync -a --delete "$ZRODLO/region/" "$PRACA/swiat/region/"
cp -p "$ZRODLO/level.dat" "$PRACA/swiat/level.dat"
cp -p "$KLIENT" "$PRACA/data/minecraft-client-$MC.jar"

echo "== Renderuję (pierwszy raz to może być kilka godzin)"
KONFIG=$(readlink -f config)
MODY=$(readlink -f "$INSTANCJA/mods")
cd "$PRACA"
java -jar "$(basename "$JAR")" -c "$KONFIG" -n "$MODY" -v "$MC" -r
echo "== Gotowe. Rozmiar:"
du -sh web
