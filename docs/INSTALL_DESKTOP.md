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

## Windows (nativo)

> Pendiente de validación completa — el desarrollo se hace en Arch.

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
