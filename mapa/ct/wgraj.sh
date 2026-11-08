#!/usr/bin/env bash
# Wgrywa do CT renderu (MAPA_CT na Proxmox-03) Jave, BlueMap, config, plik klienta i usluge renderu.
# CT nie ma sieci, wiec wszystko idzie stad. CT musi byc wylaczony: pliki trafiaja przez pct mount,
# bez uruchamiania (start CT od razu renderuje i sie wylacza).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

JRE_URL=https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz
JRE_SUMA=a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd
WERSJA_BM=$(sed -n 's/^WERSJA_BM=//p' ../renderuj.sh)
SUMA_BM=$(sed -n 's/^SUMA_BM=//p' ../renderuj.sh)
MC=1.20.1
UID_CT=100000   # root nieuprzywilejowanego CT
ENV_PLIK=../../.env

wartosc() { sed -n "s/^$1=//p" "$ENV_PLIK" | head -1; }
CT=$(wartosc MAPA_CT)
INSTANCJA=$(wartosc MAPA_INSTANCJA)
[ -n "$CT" ] && [ -n "$INSTANCJA" ] || { echo "Ustaw MAPA_CT i MAPA_INSTANCJA w .env." >&2; exit 1; }
KLIENT="$INSTANCJA/../../../libraries/com/mojang/minecraft/$MC/minecraft-$MC-client.jar"
[ -f "$KLIENT" ] || { echo "Nie ma klienta Minecrafta: $KLIENT" >&2; exit 1; }

POBRANE=../praca/ct-pobrane
mkdir -p "$POBRANE"
pobierz() {     # url, suma, plik
  if [ ! -f "$3" ] || ! echo "$2  $3" | sha256sum -c --quiet 2>/dev/null; then
    echo "== Pobieram $(basename "$3")"
    curl -fsSL -o "$3.nowy" "$1"
    echo "$2  $3.nowy" | sha256sum -c --quiet
    mv "$3.nowy" "$3"
  fi
}
pobierz "$JRE_URL" "$JRE_SUMA" "$POBRANE/jre.tar.gz"
pobierz "https://github.com/BlueMap-Minecraft/BlueMap/releases/download/v$WERSJA_BM/bluemap-$WERSJA_BM-cli.jar" \
  "$SUMA_BM" "$POBRANE/bluemap.jar"

echo "== Składam pliki"
DRZEWO=$(mktemp -d)
trap 'rm -rf "$DRZEWO"' EXIT
OPT="$DRZEWO/opt/mc-mapa"
mkdir -p "$OPT/java" "$DRZEWO/etc/mc-mapa" "$DRZEWO/etc/systemd/system/multi-user.target.wants"
tar -xzf "$POBRANE/jre.tar.gz" -C "$OPT/java" --strip-components=1
cp "$POBRANE/bluemap.jar" "$OPT/bluemap.jar"
cp "$KLIENT" "$OPT/klient.jar"
cp -r ../config "$OPT/config"
install -m 755 renderuj.sh "$OPT/renderuj.sh"
install -m 644 mc-mapa-render.service "$DRZEWO/etc/systemd/system/mc-mapa-render.service"
ln -s ../mc-mapa-render.service "$DRZEWO/etc/systemd/system/multi-user.target.wants/mc-mapa-render.service"

echo "== Wgrywam do CT $CT (dotknij YubiKeya, gdy zacznie migać)"
# Stara Java i config sa usuwane, zeby po aktualizacji nie zostaly obce pliki.
# Tylko opt i etc: archiwum z "." zmieniloby prawa katalogu glownego CT.
tar -C "$DRZEWO" --owner=$UID_CT --group=$UID_CT --numeric-owner -cf - opt etc | ssh Proxmox-03 "set -e
  pct status $CT | grep -q 'status: stopped' || { echo 'CT $CT działa: wyłącz go najpierw.' >&2; exit 1; }
  pct mount $CT >/dev/null
  trap 'pct unmount $CT' EXIT
  R=/var/lib/lxc/$CT/rootfs
  rm -rf \"\$R/opt/mc-mapa/java\" \"\$R/opt/mc-mapa/config\"
  tar -xf - -C \"\$R\" --same-owner
  echo \"Java: \$(\$R/opt/mc-mapa/java/bin/java -version 2>&1 | head -1)\""
echo "== Gotowe"
