#!/usr/bin/env bash
# Detiene Suwayomi de forma FIABLE y ORDENADA.
#
# Fiable: no depende solo del PID file, que se queda obsoleto/vacío entre
# reinicios de WSL o cuando `xvfb-run` reparenta el `java` real (el bug: `echo $!`
# capturaba el wrapper xvfb-run, no el java, dejando un `java` de 512 MB + su
# `Xvfb` huérfanos en cada cierre).
#
# ORDENADA (crítico): la base de datos H2 de Suwayomi SOLO vuelca a disco en un
# apagado limpio (SIGTERM → el JVM cierra H2 y flushea). Si la matábamos en seco
# con `fuser -k` (SIGKILL) a mitad de escritura, H2 perdía las últimas escrituras
# → las EXTENSIONES/FUENTES instaladas y el repo configurado DESAPARECÍAN en cada
# cierre de la app (el bug "Suwayomi se queda en 0 fuentes"). Por eso ahora:
# SIGTERM a todos → ESPERAR a que floten a disco → SIGKILL solo como último
# recurso si algún proceso se cuelga.

PID_FILE="/tmp/suwayomi.pid"

# ── Reunir los PIDs del java de Suwayomi (pid file + patrón del JAR) ──────────
pids=""
if [ -f "$PID_FILE" ]; then
  p="$(cat "$PID_FILE" 2>/dev/null)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && pids="$p"
fi
# `pgrep -f` cubre el orphan reparentado a init y el caso xvfb-run donde el PID
# file apunta al wrapper y no al java real.
pids="$pids $(pgrep -f 'Suwayomi-Server\.jar' 2>/dev/null)"
pids="$(echo "$pids" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ')"

if [ -z "${pids// /}" ]; then
  # Nada por PID/patrón; aún así limpia el pid file y Xvfb huérfanos.
  rm -f "$PID_FILE" 2>/dev/null
  pkill -f 'xvfb-run .*java' 2>/dev/null || true
  echo "Suwayomi is not running"
  exit 0
fi

# ── 1) Apagado ORDENADO: SIGTERM y esperar el flush de H2 ─────────────────────
for p in $pids; do kill -TERM "$p" 2>/dev/null; done

# Esperar hasta ~10 s a que TODOS terminen (H2 flushea en <1-2 s normalmente).
gone=0
for _ in $(seq 1 20); do
  alive=0
  for p in $pids; do kill -0 "$p" 2>/dev/null && alive=1; done
  if [ "$alive" -eq 0 ]; then gone=1; break; fi
  sleep 0.5
done

# ── 2) SIGKILL SOLO si algo sigue vivo tras el margen de gracia ───────────────
if [ "$gone" -eq 0 ]; then
  echo "Suwayomi no cerró limpio en 10 s — forzando (posible riesgo para la BD)."
  for p in $pids; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null; done
  # Backstop por puerto por si el cmdline cambió y algún java quedó suelto.
  command -v fuser >/dev/null 2>&1 && fuser -k 4567/tcp >/dev/null 2>&1
fi

rm -f "$PID_FILE" 2>/dev/null

# ── 3) Xvfb/xvfb-run huérfano que arrancó Suwayomi para su Chromium (KCEF) ─────
pkill -f 'xvfb-run .*java' 2>/dev/null || true
pkill -f 'Xvfb' 2>/dev/null || true

echo "Suwayomi stopped"
exit 0
