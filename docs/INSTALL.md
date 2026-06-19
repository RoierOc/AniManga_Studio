# Instalación

Guía paso a paso para clonar y correr el proyecto desde cero en Windows (WSL2),
Linux o macOS.

## 1. Requisitos del sistema

| Componente | Necesario para | Notas |
|---|---|---|
| Python 3.10+ | Backend Flask | `python3 --version` |
| Node + pnpm | Compilar el frontend | `npm install -g pnpm`; opcional si usás `/legacy` |
| GPU NVIDIA + driver + CUDA | Escalado AI | **Requisito duro** — no hay fallback a CPU/AMD/Apple Silicon |
| Java 21+ | Suwayomi (multi-fuente, opcional) | `java --version` |
| ffmpeg + ffprobe | Sincronización de subtítulos (Anime Studio) | |
| mkvmerge (MKVToolNix) | Inyectar subtítulos en `.mkv` | **No usar ffmpeg** para esto — omite el cue index y MPV no lee todos los paquetes |
| mpv | Reproducir anime con subtítulos | |
| qBittorrent | Descargar torrents (Anime Studio) | WebUI debe estar accesible en `:8080` |
| Ollama (opcional) | Traducción de subtítulos local | Alternativa/fallback: Gemini API |

`start_server.sh` corre un preflight check al arrancar y avisa (sin bloquear) qué
falta — cada herramienta solo rompe la función específica que la usa, no la app entera.

### Por sistema operativo

**Windows (WSL2, recomendado) / Linux:**
```bash
sudo apt update
sudo apt install -y ffmpeg mkvtoolnix mpv openjdk-21-jre-headless qbittorrent-nox
```

**macOS (Homebrew):**
```bash
brew install ffmpeg mkvtoolnix mpv openjdk@21 qbittorrent
```

### CUDA / PyTorch

Instalá el driver NVIDIA y el toolkit CUDA para tu sistema primero. Luego instalá
`torch`/`torchvision` desde el índice de PyTorch (no PyPI normal) con la build que
coincida con tu CUDA:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
```

Verificá con:
```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## 2. Clonar e instalar dependencias Python

```bash
git clone <tu-fork-o-este-repo>
cd manga-upscaler
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
pip install -r requirements.txt
playwright install chromium      # descarga el navegador headless que usa playwright
```

## 3. Compilar el frontend

```bash
cd frontend
pnpm install
pnpm build      # genera frontend/dist/ — Flask lo sirve en /
cd ..
```

Si no tenés Node/pnpm, `start_server.sh` sirve automáticamente la UI antigua en
`/legacy` (sin build step, funciona igual pero con menos pulido visual).

## 4. Suwayomi (multi-fuente, opcional)

```bash
mkdir -p suwayomi
wget -O suwayomi/Suwayomi-Server.jar \
  "$(curl -s https://api.github.com/repos/Suwayomi/Suwayomi-Server/releases/latest \
     | grep -oP '"browser_download_url":\s*"\K[^"]+\.jar')"
```

O descargalo manualmente desde
[github.com/Suwayomi/Suwayomi-Server/releases](https://github.com/Suwayomi/Suwayomi-Server/releases)
(el `.jar` standalone, no el instalador) y colocalo en `suwayomi/Suwayomi-Server.jar`.

`suwayomi/start.sh` lo arranca en `127.0.0.1:4567`. En Linux sin entorno gráfico
necesita `xvfb-run` (WebView embebido) — `sudo apt install xvfb` si hace falta;
en Windows/WSL-con-X o macOS no es necesario.

Una vez arrancado, abrí `http://localhost:4567` e instalá las extensiones
(fuentes) que quieras desde la WebUI de Suwayomi.

## 5. Modelos de escalado AI ("pon tu modelo")

El escalador no trae pesos incluidos (son binarios pesados, no van en git).
Ver **[docs/MODELS.md](MODELS.md)** para:
- dónde descargar los modelos por defecto (eula-digimanga, MangaJaNai),
- cómo agregar cualquier otro modelo compatible con [spandrel](https://github.com/chaiNNer-org/spandrel),
- el formato de `models/registry.json`.

## 6. Configurar secretos (`.env`)

```bash
cp .env.example .env
```

Editá `.env` y completá lo que vayas a usar — todo es opcional excepto lo que
quieras activar:

| Variable(s) | De dónde sacarlas |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `MANGADEX_CLIENT_ID/SECRET/USERNAME/PASSWORD` | [mangadex.org](https://mangadex.org) → Settings → API Clients |
| `TMDB_API_KEY` | [themoviedb.org](https://www.themoviedb.org/settings/api) (gratis) — opcional, mejora los banners de anime |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — fallback de traducción de subtítulos |
| `JIMAKU_API_KEY` / `OPENSUBTITLES_*` / `SUBDL_API_KEY` | fuentes de subtítulos opcionales |
| `OLLAMA_MODEL` | si usás Ollama local para traducir, el modelo que tengas descargado (`ollama pull qwen2.5:14b`) |

`.env` está en `.gitignore` — nunca se sube. `MANGA_DIR`/`UPSCALED_DIR`/`MODELS_DIR`
no necesitan configurarse: por defecto viven en `data/` y `models/` dentro del repo.

## 7. Arrancar

```bash
./start_server.sh        # Linux/WSL/macOS
./start_server.ps1       # Windows PowerShell nativo (sin WSL)
```

Abrí `http://localhost:5101`.

Para detener todo: `./stop.sh`.

### Windows con WSL2 (auto-arranque al iniciar sesión)

`start_windows.bat` lanza `start_server.sh` dentro de WSL desde un doble-click o
acceso directo en `shell:startup`. **Editá la ruta dentro del `.bat`** (`cd
/path/to/your/manga-upscaler`) para que apunte a donde clonaste el repo dentro
de tu WSL — los comentarios del archivo explican cómo obtenerla (`pwd`).
