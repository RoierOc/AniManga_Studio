#!/usr/bin/env bash
# bootstrap-user.sh — parte no-privilegiada del aprovisionamiento del motor.
# Se ejecuta como el usuario de la app (lo lanza bootstrap-root.sh), desde el
# checkout ya clonado. Reúsa lo que ya existe (desktop/install.sh, scripts/*).
#
# Idempotente: cada paso detecta lo ya hecho y no rompe al re-ejecutarse.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

say()  { printf '\033[1;34m[bootstrap-user]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap-user]\033[0m %s\n' "$*" >&2; }

# ── 1. Motor: venv + frontend (sin crear lanzador; el shell nativo del Setup lo es) ──
say "preparando venv + frontend (motor-solo)…"
ANIMANGA_ENGINE_ONLY=1 bash desktop/install.sh

# ── 2. PyTorch con CUDA (índice de PyTorch, NO PyPI) ──────────────────────────
# requirements.txt ya pinnea torch/torchvision, pero deben venir del índice CUDA
# para tener soporte GPU. Si ya está la build CUDA, pip no reinstala.
say "instalando/validando PyTorch CUDA…"
if ! .venv/bin/python -c 'import torch, sys; sys.exit(0 if torch.version.cuda else 1)' 2>/dev/null; then
    .venv/bin/python -m pip install --quiet \
        torch torchvision --index-url https://download.pytorch.org/whl/cu130 \
        || warn "no se pudo instalar torch CUDA — el escalado no funcionará sin GPU."
fi

# ── 3. Navegador headless de Playwright (lo usan algunas rutas de scraping) ────
say "instalando Chromium de Playwright…"
.venv/bin/python -m playwright install chromium >/dev/null 2>&1 \
    || warn "playwright install falló (se reintenta al usarse)."

# ── 4. Assets listos para usar (idempotentes) ─────────────────────────────────
bash scripts/init-env.sh        || warn "init-env falló."
bash scripts/fetch-suwayomi.sh  || warn "fetch-suwayomi falló."
bash scripts/fetch-models.sh    || warn "fetch-models falló."

# ── 5. Diagnóstico de GPU (informativo, no bloquea) ───────────────────────────
if .venv/bin/python -c 'import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)' 2>/dev/null; then
    say "✓ GPU CUDA disponible."
else
    warn "GPU CUDA NO disponible — instalá el driver NVIDIA en Windows (WSL usa el del host)."
fi

say "✓ bootstrap de usuario completo."
