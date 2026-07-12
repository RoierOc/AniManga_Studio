# AniManga Studio — instalación como app de escritorio

La app de escritorio es un shell ligero (Tauri/Rust) que lanza el backend al abrirse,
muestra la UI y apaga todo al cerrar la ventana. El repo es la instalación: clonar,
ejecutar el instalador y abrir la app. Para actualizar: `git pull` + re-ejecutar el
instalador (es idempotente).

## Linux (Arch / Hyprland)

```bash
git clone <repo> animanga && cd animanga
bash desktop/install.sh
```

El instalador comprueba dependencias (pide sudo solo si falta algo de build), crea el
venv, compila frontend y shell, e instala el lanzador. Después: abre **AniManga Studio**
desde tu lanzador de aplicaciones.

- **Render**: si hay un navegador Chromium (Brave/Chromium/Chrome), la app abre en una
  ventana `--app` con perfil propio — render GPU completo. Sin Chromium cae al webview
  WebKitGTK (funcional; en NVIDIA se aplican workarounds automáticos, algo menos fluido).
  - `ANIMANGA_WEBVIEW=1` fuerza el webview; `ANIMANGA_BROWSER=/ruta/bin` elige navegador.
- **Opcionales** (el instalador avisa si faltan): `mpv` (reproductor), `jre-openjdk` +
  `suwayomi/Suwayomi-Server.jar` (Fuentes), `qbittorrent` (descargas de anime),
  `ffmpeg`/`mkvtoolnix-cli` (subtítulos), modelos en `models/` (upscaling — docs/MODELS.md).
- **Secretos**: copia `.env.example` a `.env` (MangaDex OAuth, TMDB…).

## Windows — instalación en un clic (recomendado)

Para un equipo Windows nuevo, todo se instala con **`AniMangaStudio-Setup.exe`**.
El instalador encapsula la complejidad de WSL: no hace falta preparar nada a mano.

1. Descarga y ejecuta `AniMangaStudio-Setup.exe`.
2. Deja marcada la tarea **"Preparar el motor"** (viene marcada por defecto).
3. Si Windows pide **reiniciar** para activar WSL2, reinicia: al volver a iniciar
   sesión la preparación **continúa sola** (un `RunOnce` la reanuda).
4. Al terminar, abre **AniManga Studio** desde el Menú Inicio.

Qué hace el instalador, en orden (todo idempotente y registrado en
`%LOCALAPPDATA%\AniMangaStudio\provision.log`):

- Instala el runtime **WebView2** si falta.
- Copia el **shell nativo** (WebView2 + libmpv) y crea el acceso directo.
- **Aprovisiona el motor** (`provision-engine.ps1`): habilita **WSL2**, instala la
  distro **archlinux**, y dentro ejecuta el bootstrap (`bootstrap-root.sh` →
  `bootstrap-user.sh`): dependencias (Python, Node/pnpm, ffmpeg, mkvtoolnix, Java,
  mpv, qBittorrent), usuario + systemd + fix de interop, **clona el repo** en
  `~/AniMangaStudio`, crea el **venv**, compila el **frontend**, instala **PyTorch
  CUDA**, y prepara los assets: **Suwayomi**, **modelos** y **`.env`**.
- Escribe `config.json` (`distro` + ruta real del checkout) para el shell nativo.

**Requisito de GPU**: el escalado necesita una **GPU NVIDIA con su driver de
Windows** instalado (WSL usa el driver del host). El instalador avisa si CUDA no
está disponible, pero no puede instalar el driver por ti.

**Arquitectura**: el backend (Python/CUDA, ffmpeg, Suwayomi) vive en la distro WSL;
la ventana es un Chromium de Windows en modo `--app` (D3D11 → HEVC por hardware y
Anime4K WebGPU a plena GPU, sin el problema cross-GPU de los híbridos Linux).

- **Ciclo de vida**: abrir → arranca `start_server.sh` en WSL (watchdog incluido) →
  espera `/health` → ventana. Cerrar → `POST /shutdown`. Si el server ya corría, lo
  usa y no lo apaga.
- **Navegador**: Brave → Chrome → Edge (Edge viene con Windows 11, siempre hay
  fallback). Override: campo `"browser"` en `config.json`.
- **qBittorrent/mpv de Windows**: se lanzan solos vía interop (`QBT_WIN_PATH` en
  `.env` si tu ruta es distinta).
- **Actualizar**: vuelve a ejecutar el Setup (o `git pull` + re-ejecutar el
  aprovisionamiento) — es idempotente.

### Alternativa manual (si ya tienes una distro WSL preparada)

Si prefieres no usar el Setup y ya tienes tu WSL montada, desde DENTRO de WSL en la
carpeta del proyecto:

```bash
bash desktop/install.sh
```

Detecta WSL, prepara venv + frontend + assets y copia el lanzador PowerShell a
`%LOCALAPPDATA%\AniMangaStudio` con acceso directo en el Menú Inicio (sin compilar
Rust). Los scripts sueltos (`scripts/init-env.sh`, `scripts/fetch-suwayomi.sh`,
`scripts/fetch-models.sh`) son reutilizables e idempotentes para migraciones.

## Windows (nativo, sin WSL)

> Alternativa experimental — pendiente de validación completa.

Requisitos una vez:
```powershell
winget install Python.Python.3.12 OpenJS.NodeJS pnpm.pnpm Rustlang.Rustup Git.Git
rustup default stable-msvc   # instala las VS Build Tools si te lo pide
```

Instalación:
```powershell
git clone <repo> animanga; cd animanga
powershell -ExecutionPolicy Bypass -File desktop\install.ps1
```

Crea el acceso directo **AniManga Studio** en el Menú Inicio. En Windows el shell usa
WebView2 (Chromium, incluido en Windows 11): rendimiento nativo sin configuración.
qBittorrent/mpv/ffmpeg/mkvtoolnix se instalan aparte si se usan esas funciones.

## Cómo funciona (ambos SO)

- Abrir la app → lanza `start_server.sh`/`.ps1` en foreground como sidecar → splash
  hasta que `/health` responde → UI.
- Cerrar la ventana → `POST /shutdown` → Flask apaga Suwayomi y sale con código 0 →
  el watchdog entiende "apagado limpio" y termina. Un crash (exit ≠ 0) sí se reinicia solo.
- Si el server ya estaba corriendo (modo desarrollo), la app lo usa y NO lo apaga al salir.
- Suwayomi es on-demand: arranca al entrar a Fuentes y se apaga tras ~15 min sin uso
  (`SUWAYOMI_IDLE_MIN`; `SUWAYOMI_EAGER=1` = siempre encendida).
