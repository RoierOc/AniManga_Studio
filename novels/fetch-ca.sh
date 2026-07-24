#!/usr/bin/env bash
# Certificados que a Node le FALTAN para validar varios sitios de novelas.
#
# Let's Encrypt rotó a raíces nuevas (ISRG Root YE / YR, emitidas en 2026) y sus intermedios
# (YE2, YR2). El almacén de CAs que Node trae compilado todavía no las incluye, así que un
# sitio que sirva la cadena incompleta —cosa habitual— falla con UNABLE_TO_GET_ISSUER_CERT_LOCALLY
# aunque en el navegador se vea perfecto. Medido: novelasligera.com y allnovelread.com (2 de las
# 16 fuentes en español) caían por esto, no por Cloudflare.
#
# Se descargan de los endpoints oficiales de Let's Encrypt (los mismos que anuncia cada
# certificado en su campo AIA) y `start.sh` los pasa vía NODE_EXTRA_CA_CERTS. Idempotente.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/sidecar/certs/extra-ca.pem"
mkdir -p "$(dirname "$OUT")"

# Fresco (menos de 30 días) → no se vuelve a bajar.
if [ -s "$OUT" ] && [ -n "$(find "$OUT" -mtime -30 2>/dev/null)" ]; then
  exit 0
fi

TMP="$(mktemp)"
for CERT in ye2 yr2 ye yr; do
  if curl -fsS --max-time 20 "http://${CERT}.i.lencr.org/" -o "$TMP.der" 2>/dev/null; then
    openssl x509 -inform DER -in "$TMP.der" -outform PEM >> "$TMP" 2>/dev/null || true
  fi
done

# Solo se reemplaza si se obtuvo algo válido: mejor el bundle viejo que uno vacío.
if [ -s "$TMP" ]; then
  mv "$TMP" "$OUT"
  echo "[novels] CA extra actualizada ($(grep -c 'BEGIN CERTIFICATE' "$OUT") certificados)"
else
  rm -f "$TMP"
  echo "[novels] aviso: no se pudo actualizar la CA extra; se sigue con la que hubiera" >&2
fi
rm -f "$TMP.der"
