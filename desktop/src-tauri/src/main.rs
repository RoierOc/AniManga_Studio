// AniManga Studio — shell de escritorio (Fase 1).
// Responsabilidades: lanzar el backend Flask como sidecar si no está ya corriendo,
// mostrar la UI y apagar el backend limpiamente al cerrar (POST /shutdown). Si el
// server ya estaba arriba (modo desarrollo con watchdog), la app NO lo toca al salir.
//
// Dos modos de UI (Linux):
//  - "browser-app": si hay un navegador Chromium instalado (Brave/Chromium/Chrome),
//    abre el SPA en una ventana --app con perfil propio. Render Chrome completo con
//    GPU — evita el techo de WebKitGTK+NVIDIA (jitter/lentitud). Por defecto.
//  - "webview": ventana Tauri con WebKitGTK (fallback, o ANIMANGA_WEBVIEW=1).
// En Windows el webview de Tauri es WebView2 (Chromium): allí el modo webview es
// el bueno y este workaround no aplica.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const BASE: &str = "http://127.0.0.1:5101";

/// Hijo del sidecar (bash start_server.sh en foreground) si lo lanzamos nosotros.
struct Sidecar(Mutex<Option<Child>>);

fn backend_alive() -> bool {
    ureq::get(&format!("{BASE}/health"))
        .timeout(Duration::from_millis(1200))
        .call()
        .is_ok()
}

/// Raíz del repo: ANIMANGA_ROOT si está definida; si no, la ruta de compilación
/// (desktop/src-tauri → dos niveles arriba). Suficiente para builds locales.
fn repo_root() -> PathBuf {
    if let Ok(p) = std::env::var("ANIMANGA_ROOT") {
        return PathBuf::from(p);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .expect("repo root")
        .to_path_buf()
}

fn spawn_backend() -> Option<Child> {
    let root = repo_root();
    let script = if cfg!(target_os = "windows") {
        root.join("start_server.ps1")
    } else {
        root.join("start_server.sh")
    };
    if !script.exists() {
        eprintln!("[shell] no existe {script:?} — ¿ANIMANGA_ROOT mal apuntada?");
        return None;
    }
    let log = std::fs::File::create(std::env::temp_dir().join("manga_server.log")).ok();
    let (out, err) = match log {
        Some(f) => (
            Stdio::from(f.try_clone().expect("dup log fd")),
            Stdio::from(f),
        ),
        None => (Stdio::null(), Stdio::null()),
    };
    let mut cmd = if cfg!(target_os = "windows") {
        let mut c = Command::new("powershell");
        c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&script);
        c
    } else {
        let mut c = Command::new("bash");
        c.arg(&script);
        c
    };
    match cmd
        .current_dir(&root)
        .env("MANGA_SERVER_FG", "1") // foreground: la app es dueña del árbol de procesos
        .stdout(out)
        .stderr(err)
        .spawn()
    {
        Ok(child) => {
            println!("[shell] backend lanzado (pid {})", child.id());
            Some(child)
        }
        Err(e) => {
            eprintln!("[shell] no se pudo lanzar el backend: {e}");
            None
        }
    }
}

/// Apagado limpio: /shutdown hace exit(0) en Flask y el watchdog (exit 0 = fin)
/// termina solo. Si no responde, matamos el proceso del sidecar como último recurso.
fn stop_backend(child: &mut Child) {
    let _ = ureq::post(&format!("{BASE}/shutdown"))
        .timeout(Duration::from_secs(5))
        .call();
    for _ in 0..20 {
        if let Ok(Some(_)) = child.try_wait() {
            println!("[shell] backend apagado limpiamente");
            return;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    eprintln!("[shell] el backend no terminó tras /shutdown — kill del sidecar");
    let _ = child.kill();
    let _ = child.wait();
}

/// Navegador Chromium para el modo browser-app. ANIMANGA_BROWSER lo fija a dedo;
/// ANIMANGA_WEBVIEW=1 desactiva este modo (fuerza el webview WebKitGTK).
fn find_chromium() -> Option<PathBuf> {
    if std::env::var_os("ANIMANGA_WEBVIEW").is_some() {
        return None;
    }
    if let Ok(b) = std::env::var("ANIMANGA_BROWSER") {
        return Some(PathBuf::from(b));
    }
    if cfg!(not(target_os = "linux")) {
        return None; // Windows/macOS: WebView2/WKWebView ya rinden bien
    }
    for name in [
        "brave", "chromium", "google-chrome-stable", "google-chrome",
        "thorium-browser", "vivaldi", "microsoft-edge-stable",
    ] {
        if let Ok(out) = Command::new("which").arg(name).output() {
            if out.status.success() {
                let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !p.is_empty() {
                    return Some(PathBuf::from(p));
                }
            }
        }
    }
    None
}

/// Ventana --app de Chromium con perfil propio (imprescindible: sin él, --app se
/// fusiona con una instancia del navegador ya abierta y el proceso retorna al
/// instante, con lo que perderíamos el "cerrar ventana → apagar backend").
fn spawn_browser_app(browser: &PathBuf) -> Option<Child> {
    let profile = dirs_profile();
    Command::new(browser)
        .arg(format!("--app={BASE}/"))
        .arg(format!("--user-data-dir={}", profile.display()))
        .arg("--class=animanga-studio")
        .arg("--no-first-run")
        .arg("--no-default-browser-check")
        // Player embebido: HEVC por hardware (NVDEC vía driver VAAPI de NVIDIA
        // → HEVC se copia sin recodificar = cero pérdida) y WebGPU (Anime4K).
        // OJO: NO añadir el feature "Vulkan" aquí — rompe el init de GPU de
        // Chromium en Linux/NVIDIA y deja la ventana en blanco/transparente.
        .arg("--enable-features=VaapiOnNvidiaGPUs,VaapiIgnoreDriverChecks,AcceleratedVideoDecodeLinuxGL,PlatformHEVCDecoderSupport")
        .arg("--enable-unsafe-webgpu")
        .env("LIBVA_DRIVER_NAME", "nvidia")
        .env("NVD_BACKEND", "direct")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
}

fn dirs_profile() -> PathBuf {
    let base = std::env::var("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into()))
                .join(".local/share")
        });
    base.join("animanga-webshell")
}

fn main() {
    // Workarounds WebKitGTK+NVIDIA (solo aplican al modo webview): DMA-BUF
    // composita mal (jitter/texturas en blanco) en Wayland y XWayland —
    // verificado en esta máquina (RTX 3050, driver 610). ANIMANGA_X11=1 queda
    // como experimento para re-probar aceleración con drivers futuros.
    if std::env::var_os("ANIMANGA_X11").is_some() {
        if std::env::var_os("GDK_BACKEND").is_none() {
            std::env::set_var("GDK_BACKEND", "x11");
        }
    } else if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    if std::env::var_os("WEBKIT_SKIA_CPU_PAINTING_THREADS").is_none() {
        let threads = std::thread::available_parallelism()
            .map(|n| (n.get() / 2).clamp(2, 8))
            .unwrap_or(4);
        std::env::set_var("WEBKIT_SKIA_CPU_PAINTING_THREADS", threads.to_string());
    }

    let browser = find_chromium();
    let use_browser = browser.is_some();

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
                let _ = w.unminimize();
            }
        }));

    builder = builder.setup(move |app| {
        let owned = if backend_alive() {
            println!("[shell] backend ya corriendo — no se lanza sidecar (modo dev)");
            None
        } else {
            spawn_backend()
        };
        app.manage(Sidecar(Mutex::new(owned)));

        if let Some(browser) = browser.clone() {
            // ── Modo browser-app (Chromium) ──────────────────────────────────
            // Sin ventana Tauri: esperamos /health, abrimos la ventana --app y
            // cuando el usuario la cierra, salimos (Exit apaga el sidecar).
            println!("[shell] modo browser-app: {}", browser.display());
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(90);
                while !backend_alive() {
                    if std::time::Instant::now() > deadline {
                        eprintln!("[shell] backend no respondió en 90 s — saliendo");
                        handle.exit(1);
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                match spawn_browser_app(&browser) {
                    Some(mut child) => {
                        let _ = child.wait(); // ventana cerrada → fin de la app
                        handle.exit(0);
                    }
                    None => {
                        eprintln!("[shell] no se pudo abrir el navegador — saliendo");
                        handle.exit(1);
                    }
                }
            });
        } else {
            // ── Modo webview (WebKitGTK / WebView2) ──────────────────────────
            let win = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("splash.html".into()),
            )
            .title("AniManga Studio")
            .inner_size(1500.0, 940.0)
            .min_inner_size(900.0, 600.0)
            .center()
            .build()?;

            // El splash NO puede sondear /health por fetch (tauri:// → http://
            // es cross-origin y CORS lo bloquea). Sondea Rust y navega.
            std::thread::spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(60);
                let mut warned = false;
                loop {
                    if backend_alive() {
                        let _ = win.navigate(format!("{BASE}/").parse().unwrap());
                        return;
                    }
                    if !warned && std::time::Instant::now() > deadline {
                        warned = true;
                        let _ = win.eval("window.showError && window.showError()");
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
            });
        }
        Ok(())
    });

    let app = builder
        .build(tauri::generate_context!())
        .expect("error building tauri app");

    // En modo browser-app no hay ventanas Tauri: evita que el runtime salga solo
    // al arrancar por "no quedan ventanas".
    if use_browser {
        // (Tauri solo auto-sale cuando se cierra la última ventana; sin ventanas
        // creadas nunca dispara ese camino, así que no hace falta nada más.)
    }

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            let state = app.state::<Sidecar>();
            let owned = state.0.lock().unwrap().take();
            if let Some(mut child) = owned {
                stop_backend(&mut child);
            }
        }
    });
}
