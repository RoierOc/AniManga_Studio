# Instalación y configuración del motor

Esta guía describe el backend Flask y la interfaz Vue actuales. Para la ventana
Windows con libmpv integrado, consulta [Instalación de escritorio](INSTALL_DESKTOP.md).
No confundir el motor con la aplicación nativa: abrir el puerto 5101 en un navegador
no proporciona el mismo reproductor.

## Requisitos

- Python: las dependencias actuales necesitan al menos **3.11** (`numpy==2.4.3`).
  El CI utiliza **3.14**.
- Node: Vite 8 declara **20.19+ o 22.12+**. Usa pnpm y el lockfile del frontend.
- Git y acceso al repositorio.
- Linux/WSL para los comandos de esta guía.

Herramientas adicionales según lo que uses:

| Herramienta | Función |
|---|---|
| NVIDIA + PyTorch CUDA + modelos | Escalado IA de manga y procesamiento GPU |
| ffmpeg / ffprobe | Vídeo, extracción e información técnica |
| MKVToolNix (`mkvmerge`) | Inyección de subtítulos en MKV |
| ffsubsync | Sincronización de subtítulos |
| Ollama + un modelo descargado | Traducción local de subtítulos |
| qBittorrent con WebUI | Descargas de torrents |
| Java + Suwayomi | Fuentes de manga adicionales |
| `bsdtar` | Lectura de RAR/CBR |
| Sonarr, Radarr, Prowlarr | Integración de Cine |

Instala las herramientas del sistema por los medios de tu distribución. En WSL,
el driver NVIDIA es el de Windows: no instales un driver Linux del kernel para
reemplazarlo. No necesitas CUDA para todas las funciones de biblioteca o lectura.

## Preparar el checkout

```bash
git clone https://github.com/RoierOc/animanga-studio.git
cd animanga-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
bash scripts/init-env.sh
```

El script de configuración crea `.env` si falta y genera `SECRET_KEY` si está
vacía; no sobrescribe una configuración existente. `frontend/dist/` es regenerable
y no viene en Git. No utilices `/legacy` como alternativa equivalente a la UI actual.

## Habilitar el escalado GPU

`requirements.txt` fija las versiones de PyTorch y torchvision. El flujo WSL del
repositorio utiliza el índice CUDA 13.0; si tu instalación requiere otra build,
ajusta la selección a tu driver y a esas versiones, no cambies paquetes a ciegas.

```bash
source .venv/bin/activate
python -m pip install --upgrade --force-reinstall \
  torch==2.11.0 torchvision==0.26.0 \
  --index-url https://download.pytorch.org/whl/cu130
python -c 'import torch; print("CUDA:", torch.cuda.is_available()); print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no disponible")'
```

Este paso es para una instalación que necesite habilitar CUDA; no hace falta
reinstalar una build funcional. Coloca después los pesos indicados en
[MODELS.md](MODELS.md). `scripts/fetch-models.sh` usa el registro y URLs opcionales:
puede informar de modelos ausentes sin descargarlos ni fallar.

## Fuentes y servicios opcionales

```bash
bash scripts/fetch-suwayomi.sh
python -m playwright install chromium
```

Suwayomi utiliza Java y se inicia bajo demanda al entrar a Fuentes. Instala y
configura las extensiones que quieras utilizar; descargar su runtime no incorpora
automáticamente todas las fuentes. Su WebUI local habitual es `127.0.0.1:4567`.
Playwright instala el navegador empleado por las herramientas que lo requieren;
no es el reproductor principal de AniManga Studio.

Para qBittorrent, habilita su WebUI y configura en Ajustes la dirección y las
credenciales que correspondan. Tener abierta su ventana no garantiza que la API
sea accesible desde WSL. La dirección habitual es `http://127.0.0.1:8080`.

Para traducir subtítulos necesitas un Ollama accesible y el modelo configurado en
`OLLAMA_MODEL` (por defecto `qwen2.5:14b`). **No hay fallback actual a Gemini.**
Comprueba los recursos necesarios para el modelo antes de ejecutar una traducción.

Para Cine, sigue [SERVARR.md](SERVARR.md). Para Android, [ANDROID.md](ANDROID.md).

## Arrancar y comprobar

```bash
./start_server.sh
curl --fail http://127.0.0.1:5101/health
```

Abre `http://127.0.0.1:5101`. El script comprueba herramientas, evita instancias
duplicadas y normalmente se desacopla de la terminal. Informa de su PID y de
`/tmp/manga_server.log`; conserva esa información para detener la instancia exacta
que has iniciado. Evita comandos de eliminación de procesos por patrones amplios.

Para desarrollo del frontend, con el backend disponible:

```bash
pnpm --dir frontend dev
```

Vite imprime su dirección. Para servir cambios sin Vite, vuelve a ejecutar
`pnpm --dir frontend build`.

## Configuración y datos

Consulta `.env.example` y Ajustes. Las claves de integraciones pueden guardarse
en la configuración local; no todas viven exclusivamente en `.env`.
`MANGA_DIR`, `UPSCALED_DIR` y `MODELS_DIR` permiten cambiar las ubicaciones;
sus valores predeterminados se resuelven desde el proyecto en `src/api/runtime.py`.

No versiones `.env`, `data/`, pesos, credenciales ni carpetas personales. Una copia
del código no incluye tus datos ni es una copia de seguridad completa de la biblioteca.
El acceso remoto requiere autenticación; no expongas el servicio a Internet.
