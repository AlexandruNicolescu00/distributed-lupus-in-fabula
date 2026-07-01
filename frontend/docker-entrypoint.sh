#!/bin/sh
# Inietta la configurazione runtime nel bundle statico prima di avviare NGINX.
# Legge le env var del container (impostate da k8s/compose) e le scrive in config.js,
# così la stessa immagine si adatta a qualsiasi dominio (localhost, game.local, ...).
set -e

: "${WS_URL:=}"  # default vuoto → l'app ripiega su same-origin

# Limita la sostituzione alla sola ${WS_URL} per non toccare altri eventuali '$'.
envsubst '${WS_URL}' \
  < /usr/share/nginx/html/config.template.js \
  > /usr/share/nginx/html/config.js

echo "[entrypoint] config.js iniettato con WS_URL='${WS_URL}'"

# Delega all'entrypoint ufficiale dell'immagine nginx (CMD passato come argomenti).
exec /docker-entrypoint.sh "$@"
