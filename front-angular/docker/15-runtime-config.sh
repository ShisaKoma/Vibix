#!/bin/sh
set -eu
case "${SITE_ID:-2}" in ''|*[!0-9]*) echo 'SITE_ID doit être un entier supérieur à 1.' >&2; exit 1;; esac
if [ "${SITE_ID:-2}" -lt 2 ] || [ "${SITE_ID:-2}" -gt 2147483647 ]; then
  echo 'SITE_ID doit être compris entre 2 et 2147483647.' >&2; exit 1
fi
case "${DATA_MODE:-api}" in api|mock) ;; *) echo 'DATA_MODE doit valoir api ou mock.' >&2; exit 1;; esac
if ! printf '%s' "$API_UPSTREAM" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?$'; then
  echo 'API_UPSTREAM doit être une origine HTTP(S) sans chemin.' >&2; exit 1
fi
if ! printf '%s' "$API_HOST_HEADER" | grep -Eq '^[A-Za-z0-9.-]+(:[0-9]+)?$'; then
  echo 'API_HOST_HEADER doit être un nom d’hôte valide.' >&2; exit 1
fi
poc_site_id=$(printf '%s' "${SITE_ID:-2}" | sed 's/^0*//')
printf '{"siteId":%s,"dataMode":"%s","apiBase":"/api/v1/public"}\n' "$poc_site_id" "${DATA_MODE:-api}" > /usr/share/nginx/html/runtime-config.json
