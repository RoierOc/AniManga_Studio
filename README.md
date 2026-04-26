# MangaJaNai — Manga Manager & AI Upscaler

Web app for searching, downloading, AI-upscaling and exporting manga.
Flask backend · Vue 3 frontend · PyTorch GPU upscaler · Suwayomi multi-source.

---

## Quick start

```bash
# 1. Start Flask app (port 5001)
./start_server.sh

# 2. Start Suwayomi multi-source server (port 4567) — optional
./suwayomi/start.sh
```

Open **http://localhost:5001**

---

## Requirements

| Component | Version |
|---|---|
| Python | 3.14+ (pyenv) |
| CUDA | 12.x |
| Java | 21+ (for Suwayomi) |
| GPU VRAM | ≥ 6 GB recommended |

---

## Project structure

```
manga-upscaler/
├── src/
│   ├── app.py               # Flask entry point
│   └── api/
│       ├── runtime.py       # Shared paths & helpers
│       ├── library.py       # Local manga library
│       ├── mangadex.py      # MangaDex OAuth + search
│       ├── download.py      # Chapter download (async)
│       ├── upscale.py       # AI upscale orchestration
│       ├── upscale_worker.py# GPU worker process
│       ├── reader.py        # In-app reader pages
│       ├── status.py        # Task status aggregator
│       ├── export.py        # CBZ/CBR export (Tomo Builder)
│       └── sources.py       # Suwayomi proxy (multi-source)
├── static/
│   ├── css/styles.css
│   └── js/
│       ├── app.js           # Vue 3 SPA
│       └── vue.js
├── templates/index.html
├── suwayomi/
│   ├── server.conf          # Suwayomi headless config
│   ├── start.sh             # Start Suwayomi
│   └── stop.sh              # Stop Suwayomi
├── docs/                    # Planning & analysis docs
├── archive/                 # Old prototypes (not active)
├── start_server.sh          # Start Flask (Linux/WSL)
├── start_server.ps1         # Start Flask (PowerShell)
└── documentación técnica                # AI assistant context
```

---

## Data directories (outside repo)

| Path | Contents |
|---|---|
| `~/MangaLibrary/` | Downloaded chapters |
| `~/MangaLibrary_Upscaled/` | AI-upscaled chapters |
| `/Manga_Upscaler_project/MODELS/` | AI model weights (.pth) |
| `/Manga_Upscaler_project/suwayomi/` | Suwayomi JAR + data |

---

## Active AI model

**4x-eula-digimanga-bw-v2-nc1** — RRDBNet, 16.7M params, grayscale (1ch), 4x scale.
Optimized for B&W digital manga. Tile size: 384px.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MANGA_DIR` | `~/MangaLibrary` | Downloaded manga root |
| `UPSCALED_DIR` | `~/MangaLibrary_Upscaled` | Upscaled output root |
| `MODEL_PATH_EULA_4X` | `/Manga_Upscaler_project/MODELS/4x-eula-...pth` | Active upscale model |
| `UPSCALE_TILE_SIZE` | `384` | GPU tile size (px) |
| `COLOR_PIXEL_FRACTION` | `0.10` | Color page skip threshold |
| `SECRET_KEY` | hardcoded | Flask session secret |
