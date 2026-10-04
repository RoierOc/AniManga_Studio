# Instalación de escritorio

## Qué aplicación es la principal

La experiencia principal Windows está en **`desktop/native/`**:

- Ventana propia en Rust con WebView2/DirectComposition.
- Reproducción integrada con **libmpv**, incluyendo MKV, pistas ASS y Anime4K.
- Interfaz Vue compartida con el motor Flask.
- Motor y herramientas de procesamiento en WSL2.

`desktop/src-tauri/`, los lanzadores Chromium y el player web siguen existiendo
como rutas alternativas o históricas. **No son el mismo shell ni ofrecen por
definición la misma reproducción.**

## Windows con instalador

Si dispones de un `AniMangaStudio-Setup.exe` construido para tu configuración:

1. Ejecuta el instalador y revisa la tarea de preparación del motor.
2. Si la habilitación de WSL2 solicita reiniciar, sigue las indicaciones.
3. Completa el acceso al repositorio y las dependencias que requieran configuración.
4. Abre AniManga Studio y configura en Ajustes las integraciones que quieras usar.

El código del instalador incluye aprovisionamiento de WSL, backend, frontend y
recursos. Eso **no garantiza una instalación desatendida en cualquier PC nuevo**:
el repositorio es privado, los pesos pueden necesitar URLs y los servicios tienen
credenciales propias. No se ofrece aquí un enlace de descarga pública del Setup.

## Construir el instalador desde WSL

Prepara primero el motor siguiendo [INSTALL.md](INSTALL.md). En Windows necesitas
el toolchain Rust/MSVC con sus herramientas de compilación, libmpv y sus archivos
de desarrollo, WebView2 e Inno Setup. El script no reemplaza la instalación de
esas herramientas ni del driver de la GPU.

```bash
cd /ruta/al/checkout/animanga-studio
bash desktop/windows/build-installer.sh
```

**Antes de ejecutarlo, revisa las rutas del script.** Los valores predeterminados
incluyen directorios de la máquina de desarrollo y no son universales. Sus
variables permiten indicar tu configuración:

| Variable | Qué representa |
|---|---|
| `WIN_BUILD_DIR` | Carpeta de compilación Windows, expresada como ruta accesible desde WSL |
| `MPV_SOURCE_WIN` | Ruta Windows de los recursos de desarrollo de libmpv |
| `LIBMPV_DIR` | Carpeta accesible desde WSL que contiene `libmpv-2.dll` |
| `DISTRO` | Nombre de la distribución WSL que utilizará el shell |
| `LINUX_PATH` | Ruta del checkout del motor dentro de esa distribución |
| `APP_VERSION` | Versión que se incluye en el instalador |

El script copia el crate a la carpeta Windows, ejecuta `cargo build --release`,
prepara los recursos y llama a Inno Setup. Imprime la ruta del Setup al terminar.
No lo ejecutes sobre carpetas con trabajo ajeno: recrea su directorio de staging.
Los shaders son recursos adicionales; revisa la resolución de rutas y los perfiles
en `desktop/native/src/player.rs`. No vienen incorporados por clonar los pesos IA.

## Alternativas: no confundir con el shell principal

### Linux y preparación WSL

```bash
bash desktop/install.sh
```

En Linux, el script prepara el motor y el shell Tauri; puede utilizar Chromium en
modo aplicación o WebKitGTK. En WSL, el lanzador alternativo utiliza un Chromium
de Windows. El modo `ANIMANGA_ENGINE_ONLY=1` prepara el motor sin ese lanzador.

El instalador comprueba herramientas y tiene comandos específicos de Arch, con
posibles solicitudes de sudo. Léelo antes de ejecutarlo en otra distribución.
No se afirma que esta ruta reproduzca las capacidades del shell libmpv principal.

### Windows sin WSL

`desktop/install.ps1` prepara Python, frontend y **Tauri**, no `desktop/native/`.
Es una ruta alternativa y no utiliza el reproductor libmpv del shell principal.

### macOS

El shell principal está dirigido a Windows. Los servicios incluyen código
multiplataforma, pero no hay una distribución de escritorio macOS equivalente.

## Actualización y datos

Actualiza el checkout que usa la aplicación, recompila el frontend y reconstruye
el shell si cambió Rust.

Antes de cambiar la instalación, respalda tus bibliotecas y configuración local.
No copies un venv entre Windows y Linux ni entre rutas como si fuera portable.
`data/`, las claves y los modelos no llegan con una clonación de Git.
