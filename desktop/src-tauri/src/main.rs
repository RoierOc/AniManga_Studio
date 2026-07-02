// AniManga Studio — shell de escritorio (Fase 1).
// Responsabilidades: lanzar el backend Flask como sidecar si no está ya corriendo,
// mostrar la UI (el webview carga el SPA servido por Flask en :5101) y apagar el
// backend limpiamente al cerrar (POST /shutdown). Si el server ya estaba arriba
// (modo desarrollo con watchdog), la app NO lo toca al salir.

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
    let script = root.join("start_server.sh");
    if !script.exists() {
        eprintln!("[shell] no existe {script:?} — ¿ANIMANGA_ROOT mal apuntada?");
        return None;
    }
    let log = std::fs::File::create("/tmp/manga_server.log").ok();
    let (out, err) = match log {
        Some(f) => (
            Stdio::from(f.try_clone().expect("dup log fd")),
            Stdio::from(f),
        ),
        None => (Stdio::null(), Stdio::null()),
    };
    match Command::new("bash")
        .arg(script)
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

fn main() {
    // WebKitGTK + driver NVIDIA propietario: el renderer DMA-BUF composita mal
    // (jitter de imágenes/iconos al hacer scroll, texturas en blanco, portadas
    // que no pintan). Desactivarlo cae al camino de memoria compartida, estable
    // en NVIDIA. Respetamos el valor si el usuario ya lo fijó en su entorno.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // Segunda instancia → traer la ventana existente al frente.
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
                let _ = w.unminimize();
            }
        }))
        .setup(|app| {
            let owned = if backend_alive() {
                println!("[shell] backend ya corriendo — no se lanza sidecar (modo dev)");
                None
            } else {
                spawn_backend()
            };
            app.manage(Sidecar(Mutex::new(owned)));

            // El splash NO puede sondear /health por fetch (tauri:// → http://127.0.0.1
            // es cross-origin y el webview lo bloquea por CORS). Sondea Rust y navega.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(60);
                let mut warned = false;
                loop {
                    if backend_alive() {
                        if let Some(w) = handle.get_webview_window("main") {
                            let _ = w.navigate(format!("{BASE}/").parse().unwrap());
                        }
                        return;
                    }
                    // Aviso a los 60 s pero seguimos sondeando: si el backend
                    // aparece más tarde, la app entra sola igualmente.
                    if !warned && std::time::Instant::now() > deadline {
                        warned = true;
                        if let Some(w) = handle.get_webview_window("main") {
                            let _ = w.eval("window.showError && window.showError()");
                        }
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error building tauri app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let state = app.state::<Sidecar>();
                let owned = state.0.lock().unwrap().take();
                if let Some(mut child) = owned {
                    stop_backend(&mut child);
                }
            }
        });
}
