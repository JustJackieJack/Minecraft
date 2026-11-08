#!/bin/sh
# Hookscript CT renderu mapy: pct set <MAPA_CT> --hookscript local:snippets/mc-mapa-hook.sh
exec /usr/local/sbin/mc_mapa.py hook "$@"
