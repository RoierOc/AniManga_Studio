// AniManga Studio — shell nativa de Windows (Fase 1).
//
// Ventana propia con WebView2 en modo COMPOSICIÓN (DirectComposition) hospedando
// la SPA Vue REAL servida por Flask en http://127.0.0.1:5101 — sin Chromium
// externo. Gestiona el ciclo de vida del backend (igual que launch.ps1): si no
// responde /health, lo arranca por wsl.exe; al cerrar, POST /shutdown solo si esta
// shell lo levantó. La infra de composición (visual web transparente sobre una
// visual de vídeo) queda lista para que la Fase 2 inserte libmpv por debajo.
#![windows_subsystem = "windows"]

use std::cell::{Cell, RefCell};
use std::io::{Read, Write};
use std::mem::size_of;
use std::net::TcpStream;
use std::os::windows::process::CommandExt;
use std::rc::Rc;
use std::time::{Duration, Instant};

// Sin esto, un app de subsistema "windows" que lanza wsl.exe (consola) hace
// parpadear una ventana de consola negra. CREATE_NO_WINDOW la suprime.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
// Permite que un proceso hijo ESCAPE del Job Object del shell (que es KILL_ON_JOB_CLOSE).
// Se usa SOLO para el `wsl.exe stop.sh` de respaldo, que debe sobrevivir a nuestra muerte para
// terminar de matar el backend Linux; todo lo demás (WebView2) se queda en el job y muere.
const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;

use webview2_com::{
    CreateCoreWebView2CompositionControllerCompletedHandler,
    CreateCoreWebView2EnvironmentCompletedHandler, WebMessageReceivedEventHandler,
    Microsoft::Web::WebView2::Win32::{
        ICoreWebView2, ICoreWebView2CompositionController, ICoreWebView2Controller,
        ICoreWebView2Controller2, ICoreWebView2Controller3, ICoreWebView2Environment,
        ICoreWebView2Environment3, COREWEBVIEW2_BOUNDS_MODE_USE_RAW_PIXELS, COREWEBVIEW2_COLOR,
        COREWEBVIEW2_MOUSE_EVENT_KIND, COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS,
    },
};
use windows::core::{w, Interface, PCWSTR, PWSTR};
use windows::Win32::System::WinRT::EventRegistrationToken;
use windows::Win32::Foundation::{
    BOOL, CloseHandle, ERROR_ALREADY_EXISTS, E_FAIL, GetLastError, HINSTANCE, HWND, LPARAM, LRESULT,
    POINT, RECT, WPARAM,
};
use windows::Win32::System::Threading::{
    CreateMutexW, GetCurrentProcess, GetCurrentProcessId, OpenProcess, TerminateProcess,
    PROCESS_TERMINATE,
};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
    JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use std::sync::atomic::{AtomicU32, Ordering};
use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
    D3D11_SDK_VERSION,
};
use windows::Win32::Graphics::DirectComposition::{
    DCompositionCreateDevice, IDCompositionDevice, IDCompositionTarget, IDCompositionVisual,
};
use windows::Win32::Graphics::Dwm::DwmExtendFrameIntoClientArea;
use windows::Win32::Graphics::Dxgi::IDXGIDevice;
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MonitorFromWindow, ScreenToClient, MONITORINFO, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_ALL, COINIT_APARTMENTTHREADED};
use windows::Win32::UI::Shell::{
    ITaskbarList3, Shell_NotifyIconW, TaskbarList, NOTIFYICONDATAW, NIF_ICON, NIF_INFO,
    NIF_MESSAGE, NIF_REALTIME, NIF_TIP, NIIF_ERROR, NIIF_INFO, NIIF_NOSOUND,
    NIIF_RESPECT_QUIET_TIME, NIM_ADD, NIM_DELETE, NIM_MODIFY, NIN_BALLOONHIDE,
    NIN_BALLOONTIMEOUT, NIN_BALLOONUSERCLICK, TBPF_ERROR, TBPF_INDETERMINATE,
    TBPF_NOPROGRESS, TBPF_NORMAL, TBPF_PAUSED,
};
use windows::Win32::UI::Controls::MARGINS;
use windows::Win32::UI::HiDpi::{
    GetDpiForWindow, GetSystemMetricsForDpi, SetProcessDpiAwarenessContext,
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture};
use windows::Win32::UI::WindowsAndMessaging::*;

mod player;
use player::{Player, TIMER_RENDER};

const BASE_HOST: &str = "127.0.0.1:5101";
const APP_URL: &str = "http://127.0.0.1:5101/";
const WM_TASK_NOTIFICATION: u32 = 0x8000 + 17;
const TASK_NOTIFICATION_ICON_ID: u32 = 0xA11;

struct App {
    controller: ICoreWebView2CompositionController,
    ctrl: ICoreWebView2Controller,
    dcomp: IDCompositionDevice,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    hwnd: HWND,
    visual_video: IDCompositionVisual,
    webview: ICoreWebView2,
    // Motor de vídeo: se crea al abrir el primer episodio (Fase 2).
    player: Option<Player>,
}
thread_local! {
    static APP: RefCell<Option<App>> = const { RefCell::new(None) };
    /* Barra de tareas de Windows: la barrita de progreso DENTRO del icono. Es lo que deja ver
       cómo va un escalado a 4K o una descarga sin tener que traer la ventana al frente — algo
       que una web no puede hacer y una app de escritorio sí.
       Se crea perezosamente: si el shell de Windows no la ofrece (sesión rara, Explorer caído),
       se registra una vez y se sigue sin ella; nunca es un error que deba parar nada. */
    static TASKBAR: RefCell<Option<ITaskbarList3>> = const { RefCell::new(None) };
    static TASK_NOTIFICATION_ICON: Cell<bool> = const { Cell::new(false) };
    static OWNED: RefCell<bool> = const { RefCell::new(false) };
    // Contador de frames para throttlear el reporte de tiempo a JS.
    static FRAME: RefCell<u32> = const { RefCell::new(0) };
    // Estado guardado al entrar en fullscreen borderless (estilo + placement).
    static FS: RefCell<Option<(i32, WINDOWPLACEMENT)>> = const { RefCell::new(None) };
    // ¿Ocultar el cursor? Lo pide el JS cuando la UI del player se auto-oculta.
    static CURSOR_HIDE: Cell<bool> = const { Cell::new(false) };
}

// Fullscreen borderless: quita bordes/título y cubre el monitor; al salir restaura
// estilo y posición. Es lo correcto para un reproductor (SW_MAXIMIZE deja el título
// y no siempre aplica). Dispara WM_SIZE → el vídeo se redimensiona solo.
unsafe fn set_fullscreen(hwnd: HWND, on: bool) {
    if on {
        if FS.with(|f| f.borrow().is_some()) {
            return; // ya estamos en fullscreen
        }
        let style = GetWindowLongW(hwnd, GWL_STYLE);
        let mut wp = WINDOWPLACEMENT {
            length: size_of::<WINDOWPLACEMENT>() as u32,
            ..Default::default()
        };
        let _ = GetWindowPlacement(hwnd, &mut wp);
        let hmon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
        let mut mi = MONITORINFO {
            cbSize: size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        if !GetMonitorInfoW(hmon, &mut mi).as_bool() {
            return;
        }
        FS.with(|f| *f.borrow_mut() = Some((style, wp)));
        SetWindowLongW(hwnd, GWL_STYLE, style & !(WS_OVERLAPPEDWINDOW.0 as i32));
        let r = mi.rcMonitor;
        let _ = SetWindowPos(
            hwnd,
            HWND_TOP,
            r.left,
            r.top,
            r.right - r.left,
            r.bottom - r.top,
            SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
        );
    } else if let Some((style, wp)) = FS.with(|f| f.borrow_mut().take()) {
        SetWindowLongW(hwnd, GWL_STYLE, style);
        let _ = SetWindowPlacement(hwnd, &wp);
        let _ = SetWindowPos(
            hwnd,
            HWND::default(),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
        );
    }
}

// ── Ciclo de vida del backend ────────────────────────────────────────────────

fn backend_alive() -> bool {
    let addr = match BASE_HOST.parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_secs(2)) else {
        return false;
    };
    let req = "GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    let _ = s.read_to_string(&mut buf);
    buf.contains(" 200 ")
}

// Apaga el backend Linux SIN BLOQUEAR el hilo de UI (el cierre debe sentirse instantáneo).
// Antes esto sondeaba /health hasta ~2.5 s y esperaba a `stop.sh` hasta 8 s → la ventana ya
// estaba oculta pero `animanga.exe` + WebView2 seguían vivos esos ~10 s porque aún no se
// llegaba al `TerminateProcess(self)` → parecía "no se cierra". Ahora: (1) POST /shutdown
// best-effort y (2) `wsl.exe stop.sh` LANZADO Y OLVIDADO con CREATE_BREAKAWAY_FROM_JOB para
// que sobreviva a nuestra muerte y termine de matar el stack Linux (matar el wsl.exe relay NO
// mata los procesos Linux; hay que correr stop.sh DENTRO de WSL). No esperamos a nada.
fn backend_shutdown() {
    if let Ok(addr) = BASE_HOST.parse::<std::net::SocketAddr>() {
        if let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
            let _ = s.set_write_timeout(Some(Duration::from_millis(300)));
            let req = "POST /shutdown HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
            let _ = s.write_all(req.as_bytes());
            let _ = s.flush();
        }
    }
    // Backstop garantizado y desacoplado: sobrevive porque rompe con el Job (BREAKAWAY_OK).
    let (distro, linux_path) = read_config();
    let _ = std::process::Command::new("wsl.exe")
        .args(["-d", &distro, "--cd", &linux_path, "-e", "bash", "stop.sh"])
        .creation_flags(CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB)
        .spawn();
    // El wsl.exe relay FG lo remata igualmente el kill del árbol de hijos / el Job.
    BACKEND_WSL_PID.store(0, Ordering::SeqCst);
}

// Extrae "<clave>":"<valor>" de un JSON plano (sin dependencias).
fn json_str(json: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\"");
    let i = json.find(&pat)? + pat.len();
    let rest = &json[i..];
    let colon = rest.find(':')?;
    let after = rest[colon + 1..].trim_start();
    let q = after.find('"')? + 1;
    let end = after[q..].find('"')?;
    Some(after[q..q + end].to_string())
}

// Arranca el backend Flask dentro de WSL (start_server.sh se auto-detacha con su
// watchdog). Devuelve true si tras el poll /health responde.
fn ensure_backend() -> bool {
    if backend_alive() {
        return true; // ya corría (dev): NO lo apagaremos al salir
    }
    OWNED.with(|o| *o.borrow_mut() = true);

    let (distro, linux_path) = read_config();
    // FG (foreground) es CLAVE: sin MANGA_SERVER_FG, start_server.sh se auto-detacha
    // (nohup) y wsl.exe retorna al instante; la sesión WSL efímera creada por esta
    // invocación Windows→WSL se derrumba y MATA al hijo detached antes de que Flask
    // ligue el 5101 (por eso "solo funciona si lanzo ./restart.sh a mano"). En FG,
    // wsl.exe se queda vivo corriendo el watchdog y ANCLA la sesión WSL mientras la
    // app viva; al cerrar, backend_shutdown() (POST /shutdown) hace que el watchdog
    // salga limpio y wsl.exe termine. spawn() no espera: el hijo sigue corriendo.
    if let Ok(child) = std::process::Command::new("wsl.exe")
        .args([
            "-d",
            &distro,
            "--cd",
            &linux_path,
            "-e",
            "env",
            "MANGA_SERVER_FG=1",
            "bash",
            "start_server.sh",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
    {
        // Recuerda el PID del wsl.exe FG para terminarlo al cerrar (evita huérfanos).
        BACKEND_WSL_PID.store(child.id(), Ordering::SeqCst);
    }

    let deadline = Instant::now() + Duration::from_secs(150); // arranque en frío de WSL
    while Instant::now() < deadline {
        if backend_alive() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn read_config() -> (String, String) {
    let default = ("archlinux".to_string(), String::new());
    let Ok(local) = std::env::var("LOCALAPPDATA") else {
        return default;
    };
    let path = format!("{local}\\AniMangaStudio\\config.json");
    let Ok(txt) = std::fs::read_to_string(&path) else {
        return default;
    };
    (
        json_str(&txt, "distro").unwrap_or(default.0),
        json_str(&txt, "linuxPath").unwrap_or(default.1),
    )
}

fn message_box(text: PCWSTR, caption: PCWSTR) {
    unsafe {
        // TOPMOST + SETFOREGROUND: el proceso puede no tener ventana propia todavía
        // (fallo en ensure_backend antes de crearla); sin estos flags el diálogo
        // salía DETRÁS/invisible y bloqueaba el proceso para siempre → se quedaba
        // colgado reteniendo el mutex y hacía falta reiniciar para volver a abrir.
        MessageBoxW(
            None,
            text,
            caption,
            MB_OK | MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND,
        );
    }
}

// ── Shell / WebView2 en composición ──────────────────────────────────────────

fn wv_err(e: webview2_com::Error) -> windows::core::Error {
    match e {
        webview2_com::Error::WindowsError(w) => w,
        webview2_com::Error::CallbackError(s) => windows::core::Error::new(E_FAIL, s),
        _ => windows::core::Error::new(E_FAIL, "webview2 async error"),
    }
}

fn client_size(hwnd: HWND) -> (u32, u32) {
    let mut r = RECT::default();
    unsafe {
        let _ = GetClientRect(hwnd, &mut r);
    }
    (((r.right - r.left).max(1)) as u32, ((r.bottom - r.top).max(1)) as u32)
}

// PID del wsl.exe que ESTA instancia arrancó para el backend (0 = no lo arrancó
// nosotros, p.ej. el server ya estaba vivo). Al cerrar lo terminamos para que no
// queden wsl.exe/backend huérfanos (antes se acumulaban y "cerrar no los mataba").
static BACKEND_WSL_PID: AtomicU32 = AtomicU32::new(0);

/// Mete a ESTE proceso en un Job Object con KILL_ON_JOB_CLOSE. Todo lo que el shell lance
/// DESPUÉS (los procesos de WebView2 `msedgewebview2.exe`, `wsl.exe`) hereda el job; cuando el
/// shell muere, Windows mata el job entero → NADA queda huérfano, sin importar CÓMO se cerró
/// (X, crash, kill). Es la RED DE SEGURIDAD definitiva contra los zombies `animanga.exe` +
/// `msedgewebview2.exe` que sobrevivían al cierre. Debe llamarse ANTES de crear WebView2 y de
/// lanzar wsl.exe. El handle se filtra a propósito: mantenerlo abierto ata la vida del job a la
/// del proceso (al terminar el proceso, el handle se cierra solo → dispara KILL_ON_JOB_CLOSE).
unsafe fn setup_kill_on_close_job() {
    let Ok(job) = CreateJobObjectW(None, PCWSTR::null()) else {
        return;
    };
    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    // KILL_ON_JOB_CLOSE: al morir el shell, mata todo el job (WebView2, wsl.exe relay).
    // BREAKAWAY_OK: permite que el `wsl.exe stop.sh` de respaldo se lance FUERA del job (con
    // CREATE_BREAKAWAY_FROM_JOB) para que sobreviva y termine de matar el backend Linux.
    info.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
    let _ = SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        &info as *const _ as *const core::ffi::c_void,
        size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
    );
    let _ = AssignProcessToJobObject(job, GetCurrentProcess());
    // A propósito NO se llama a CloseHandle(job): HANDLE es Copy/sin Drop, así que al salir de
    // este scope el handle del SO queda ABIERTO durante toda la vida del proceso. Eso es lo que
    // ata el job al proceso — al terminar el proceso, el SO cierra el handle → KILL_ON_JOB_CLOSE.
    let _ = job;
}

/// Log de diagnóstico del cierre en `%LOCALAPPDATA%\AniMangaStudio\shutdown.log`. Como cada
/// rebuild del shell cuesta ~4 h, si el cierre volviera a fallar este log dice EXACTAMENTE hasta
/// qué paso llegó (o si `shutdown_everything` ni se llama) sin tener que adivinar/recompilar.
fn dbg_log(msg: &str) {
    use std::io::Write;
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let dir = format!("{local}\\AniMangaStudio");
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(format!("{dir}\\shutdown.log"))
        {
            let _ = writeln!(f, "{msg}");
        }
    }
}

/// Mata TODO el árbol de procesos hijos de `root_pid` (los `msedgewebview2.exe` de WebView2, el
/// `wsl.exe` relay y sus descendientes) de forma DETERMINISTA — sin depender de que WebView2
/// se auto-cierre ni de `Controller::Close()` (que podía deadlockear al no bombear mensajes).
/// NO mata a `root_pid` (eso es el TerminateProcess(self) final). Es la clave para que
/// `msedgewebview2.exe` no sobreviva al cierre.
unsafe fn kill_child_tree(root_pid: u32) {
    let Ok(snap) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
        return;
    };
    // (pid, ppid) de todos los procesos.
    let mut procs: Vec<(u32, u32)> = Vec::new();
    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    if Process32FirstW(snap, &mut entry).is_ok() {
        loop {
            procs.push((entry.th32ProcessID, entry.th32ParentProcessID));
            if Process32NextW(snap, &mut entry).is_err() {
                break;
            }
        }
    }
    let _ = CloseHandle(snap);
    // BFS: descendientes de root_pid.
    let mut targets: Vec<u32> = Vec::new();
    let mut frontier = vec![root_pid];
    while let Some(pid) = frontier.pop() {
        for &(p, pp) in &procs {
            if pp == pid && p != root_pid && !targets.contains(&p) {
                targets.push(p);
                frontier.push(p);
            }
        }
    }
    for pid in targets {
        if let Ok(h) = OpenProcess(PROCESS_TERMINATE, false, pid) {
            let _ = TerminateProcess(h, 1);
            let _ = CloseHandle(h);
        }
    }
}

/// Cierre ÚNICO y autoritativo del shell, use la ruta que use (WM_CLOSE o fin del bucle).
/// FILOSOFÍA: no esperar ni depender de teardown de COM/WebView2 (colgaba y dejaba
/// `animanga.exe` + `msedgewebview2.exe` vivos). En su lugar: disparar el apagado del backend
/// SIN bloquear, matar de forma explícita todo el árbol de hijos, y autoterminar de golpe.
/// El Job Object (KILL_ON_JOB_CLOSE) es la red de seguridad final por si algo se escapó.
fn shutdown_everything() -> ! {
    dbg_log("[shutdown] start");
    unsafe {
        // 1) Mata explícitamente WebView2 (msedgewebview2.exe) + wsl.exe relay + descendientes.
        //    ANTES del backstop de backend: así el `wsl.exe stop.sh` que se lanza después NO cae
        //    en este barrido (aún no existe) y sobrevive para matar el stack Linux.
        kill_child_tree(GetCurrentProcessId());
        dbg_log("[shutdown] child tree killed");
    }
    // 2) Backend WSL (no bloquea): POST /shutdown + `stop.sh` desacoplado que rompe con el Job y
    //    sobrevive a nuestra muerte. Solo si esta shell levantó el backend.
    if OWNED.with(|o| *o.borrow()) {
        backend_shutdown();
    }
    dbg_log("[shutdown] backend signaled");
    unsafe {
        // 3) Otros animanga.exe colgados de sesiones previas.
        reap_other_instances();
        // 4) Autoterminación tajante (sin desenrollar hilos de WebView2/libmpv que colgaban).
        dbg_log("[shutdown] terminating self");
        let _ = TerminateProcess(GetCurrentProcess(), 0);
    }
    // TerminateProcess(self) no retorna; este loop solo satisface el tipo `!`.
    loop {
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Termina cualquier OTRO proceso `animanga.exe` (no el actual). Sirve para barrer
/// zombies colgados (arranques en frío que nunca abrieron ventana, cierres sucios)
/// que retienen el mutex/recursos e impiden abrir la app sin ir al Administrador.
unsafe fn reap_other_instances() {
    let me = GetCurrentProcessId();
    let Ok(snap) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
        return;
    };
    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    if Process32FirstW(snap, &mut entry).is_ok() {
        loop {
            let len = entry.szExeFile.iter().position(|&c| c == 0).unwrap_or(0);
            let name = String::from_utf16_lossy(&entry.szExeFile[..len]);
            if entry.th32ProcessID != me && name.eq_ignore_ascii_case("animanga.exe") {
                if let Ok(h) = OpenProcess(PROCESS_TERMINATE, false, entry.th32ProcessID) {
                    let _ = TerminateProcess(h, 1);
                    let _ = CloseHandle(h);
                }
            }
            if Process32NextW(snap, &mut entry).is_err() {
                break;
            }
        }
    }
    let _ = CloseHandle(snap);
}

/// Instancia única, AUTO-SANADORA. Sin esto, un `animanga.exe` colgado (sin
/// ventana) retiene el mutex y bloquea todo lanzamiento hasta ir al Administrador
/// de tareas. Lógica: si YA hay una ventana de la app → enfócala (no duplicar); si
/// el mutex está tomado pero SIN ventana, el dueño es un zombie → termínalo y toma
/// el relevo. Solo un proceso entra en la rama de barrido (el mutex lo garantiza),
/// así que no hay matanza mutua entre arranques simultáneos.
unsafe fn acquire_singleton() -> bool {
    let handle = CreateMutexW(None, BOOL(1), w!("AniMangaStudio-SingleInstance"));
    let already = GetLastError() == ERROR_ALREADY_EXISTS;
    if already {
        if let Ok(hw) = FindWindowW(w!("AniMangaShell"), PCWSTR::null()) {
            if !hw.is_invalid() {
                // Instancia viva con ventana → al frente y salimos.
                if let Ok(h) = handle {
                    let _ = CloseHandle(h);
                }
                let _ = ShowWindow(hw, SW_RESTORE);
                let _ = SetForegroundWindow(hw);
                return false;
            }
        }
        // Mutex tomado pero sin ventana → zombie colgado. Bárrelo y arranca nosotros.
        if let Ok(h) = handle {
            let _ = CloseHandle(h);
        }
        reap_other_instances();
        let _ = CreateMutexW(None, BOOL(1), w!("AniMangaStudio-SingleInstance"));
        return true;
    }
    // Primera instancia. Retén el handle toda la vida del proceso (el SO lo libera
    // al salir). Barre por si quedaron zombies de sesiones anteriores.
    if let Ok(h) = handle {
        let _ = h;
    }
    reap_other_instances();
    true
}

fn main() -> windows::core::Result<()> {
    // 0) Instancia única — antes de ensure_backend para no esperar el backend dos veces.
    unsafe {
        if !acquire_singleton() {
            return Ok(());
        }
        // Red de seguridad: mete el proceso en un Job KILL_ON_JOB_CLOSE ANTES de lanzar
        // wsl.exe y de crear WebView2, para que ambos hereden el job y mueran con el shell.
        setup_kill_on_close_job();
    }

    // 1) Backend arriba antes de cargar la UI.
    if !ensure_backend() {
        message_box(
            w!("El backend no respondió en 150 s.\nRevisa el log en \\\\wsl.localhost\\archlinux\\tmp\\manga_server.log"),
            w!("AniManga Studio"),
        );
        return Ok(());
    }

    unsafe {
        // Per-Monitor DPI Aware V2: sin esto, en un monitor con escalado (p.ej. el
        // TV 4K al 150%) GetClientRect devuelve píxeles LÓGICOS y el swapchain se
        // crea a menor resolución que se estira a la física → vídeo borroso. Con
        // esto el cliente es físico (4K real) y mpv escala a resolución nativa.
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()?;
        let hinstance: HINSTANCE = GetModuleHandleW(None)?.into();

        let class = w!("AniMangaShell");
        let wc = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wndproc),
            hInstance: hinstance,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            lpszClassName: class,
            ..Default::default()
        };
        RegisterClassW(&wc);
        let hwnd = CreateWindowExW(
            WS_EX_NOREDIRECTIONBITMAP,
            class,
            w!("AniManga Studio"),
            WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            1440,
            900,
            None,
            None,
            hinstance,
            None,
        )?;

        // Ventana SIN marco del sistema pero conservando el estilo WS_OVERLAPPEDWINDOW
        // (redimensionar/snap/animaciones de maximizar). WM_NCCALCSIZE quita la barra
        // de título visible; DwmExtendFrameIntoClientArea deja 1px de "cristal" arriba
        // para que Windows siga dibujando la sombra y las esquinas redondeadas (Win11).
        // Nuestra barra de título propia (HTML en el WebView) cubre ese píxel.
        let margins = MARGINS {
            cxLeftWidth: 0,
            cxRightWidth: 0,
            cyTopHeight: 1,
            cyBottomHeight: 0,
        };
        let _ = DwmExtendFrameIntoClientArea(hwnd, &margins);
        // Fuerza a recalcular el área no-cliente ya (dispara WM_NCCALCSIZE) para que el
        // marco desaparezca antes del primer paint.
        let _ = SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        );

        // D3D11 solo para el device DXGI que necesita DirectComposition.
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            None,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&[D3D_FEATURE_LEVEL_11_0]),
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )?;
        let device = device.unwrap();
        let context = context.unwrap();
        let dxgi_device: IDXGIDevice = device.cast()?;

        // DComp: root → [visual_video (vídeo, DEBAJO) | visual_web (web, encima)].
        let dcomp: IDCompositionDevice = DCompositionCreateDevice(&dxgi_device)?;
        let target: IDCompositionTarget = dcomp.CreateTargetForHwnd(hwnd, true)?;
        let root: IDCompositionVisual = dcomp.CreateVisual()?;
        let visual_web: IDCompositionVisual = dcomp.CreateVisual()?;
        let visual_video: IDCompositionVisual = dcomp.CreateVisual()?;
        root.AddVisual(&visual_web, false, None)?;
        root.AddVisual(&visual_video, false, &visual_web)?; // vídeo debajo de web
        target.SetRoot(&root)?;
        dcomp.Commit()?;

        // WebView2 transparente → navega a la app real.
        let environment = create_environment()?;
        let env3: ICoreWebView2Environment3 = environment.cast()?;
        let controller = create_composition_controller(&env3, hwnd)?;
        controller.SetRootVisualTarget(&visual_web)?;
        let ctrl: ICoreWebView2Controller = controller.cast()?;
        // Ahora que el proceso es DPI-aware, los bounds van en píxeles FÍSICOS (raw)
        // y el WebView rasteriza la UI a la escala del monitor para que no salga
        // diminuta. WebView2 reajusta la escala solo al cambiar de monitor.
        if let Ok(ctrl3) = ctrl.cast::<ICoreWebView2Controller3>() {
            let scale = GetDpiForWindow(hwnd) as f64 / 96.0;
            let _ = ctrl3.SetBoundsMode(COREWEBVIEW2_BOUNDS_MODE_USE_RAW_PIXELS);
            let _ = ctrl3.SetRasterizationScale(if scale > 0.0 { scale } else { 1.0 });
            let _ = ctrl3.SetShouldDetectMonitorScaleChanges(true);
        }
        let (cw, ch) = client_size(hwnd);
        ctrl.SetBounds(RECT { left: 0, top: 0, right: cw as i32, bottom: ch as i32 })?;
        let ctrl2: ICoreWebView2Controller2 = ctrl.cast()?;
        ctrl2.SetDefaultBackgroundColor(COREWEBVIEW2_COLOR { A: 0, R: 0, G: 0, B: 0 })?;
        ctrl.SetIsVisible(true)?;
        let webview = ctrl.CoreWebView2()?;
        // Fase 2: canal IPC JS↔Rust (los controles Vue mandarán comandos al motor).
        if let Ok(settings) = webview.Settings() {
            let _ = settings.SetIsWebMessageEnabled(true);
        }
        register_ipc(&webview)?;
        let mut url = APP_URL.encode_utf16().collect::<Vec<u16>>();
        url.push(0);
        webview.Navigate(PCWSTR(url.as_ptr()))?;
        dcomp.Commit()?;

        APP.with(|a| {
            *a.borrow_mut() = Some(App {
                controller,
                ctrl,
                dcomp: dcomp.clone(),
                device: device.clone(),
                context: context.clone(),
                hwnd,
                visual_video: visual_video.clone(),
                webview: webview.clone(),
                player: None,
            })
        });

        let _ = ShowWindow(hwnd, SW_SHOW);
        SetTimer(hwnd, 1, 250, None); // recomponer por si el 1er paint llega tarde

        // Pre-calentar el motor de vídeo (mpv+GL+interop ~0.5 s) para que el primer
        // play sea instantáneo. En reposo no renderiza (Player::active=false).
        with_player(|_| {});

        // Los tirones NO están en el render (medido: draw=42ms, present=0ms incluso durante
        // el tirón), así que el hilo se va en OTRO mensaje. Delatamos al culpable: cuánto se
        // tarda en sacar cada mensaje de la cola (espera) y en despacharlo (trabajo).
        let mut msg = MSG::default();
        loop {
            let t0 = Instant::now();
            if !GetMessageW(&mut msg, None, 0, 0).as_bool() {
                break;
            }
            let wait = t0.elapsed();
            let (m, t1) = (msg.message, Instant::now());
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
            let work = t1.elapsed();
            if wait.as_millis() > 100 || work.as_millis() > 100 {
                player::log_line(&format!(
                    "[bloqueo] msg=0x{:04X} espera={}ms trabajo={}ms",
                    m,
                    wait.as_millis(),
                    work.as_millis()
                ));
            }
        }
    }

    // Fin del bucle de mensajes → cierre autoritativo único (WebView2 + backend + autotermina).
    shutdown_everything()
}

fn create_environment() -> windows::core::Result<ICoreWebView2Environment> {
    let out: Rc<RefCell<Option<ICoreWebView2Environment>>> = Rc::new(RefCell::new(None));
    let sink = out.clone();
    CreateCoreWebView2EnvironmentCompletedHandler::wait_for_async_operation(
        Box::new(move |handler| unsafe {
            let userdata = w!("C:\\Users\\Example\\animanga-native\\wv2-userdata");
            webview2_com::Microsoft::Web::WebView2::Win32::CreateCoreWebView2EnvironmentWithOptions(
                PCWSTR::null(),
                userdata,
                None,
                &handler,
            )
            .map_err(webview2_com::Error::WindowsError)
        }),
        Box::new(move |result, environment| {
            result?;
            *sink.borrow_mut() = environment;
            Ok(())
        }),
    )
    .map_err(wv_err)?;
    let env = out.borrow_mut().take().expect("environment");
    Ok(env)
}

fn create_composition_controller(
    env3: &ICoreWebView2Environment3,
    hwnd: HWND,
) -> windows::core::Result<ICoreWebView2CompositionController> {
    let out: Rc<RefCell<Option<ICoreWebView2CompositionController>>> = Rc::new(RefCell::new(None));
    let sink = out.clone();
    let env3 = env3.clone();
    CreateCoreWebView2CompositionControllerCompletedHandler::wait_for_async_operation(
        Box::new(move |handler| unsafe {
            env3.CreateCoreWebView2CompositionController(hwnd, &handler)
                .map_err(webview2_com::Error::WindowsError)
        }),
        Box::new(move |result, controller| {
            result?;
            *sink.borrow_mut() = controller;
            Ok(())
        }),
    )
    .map_err(wv_err)?;
    let c = out.borrow_mut().take().expect("composition controller");
    Ok(c)
}

// ── IPC JS↔Rust (Fase 2) ─────────────────────────────────────────────────────
//
// La SPA Vue manda comandos con `window.chrome.webview.postMessage({cmd,...})`,
// que llegan a `add_WebMessageReceived`. Rust responde con `PostWebMessageAsJson`
// (evento `message` en JS). De momento el dispatcher solo registra y responde a
// `ping`; la Fase 2 conectará cada `cmd` (loadfile/pause/seek/glsl-shaders/…) al
// motor libmpv cuando se porte a esta shell.

fn ipc_log(line: &str) {
    let Ok(local) = std::env::var("LOCALAPPDATA") else {
        return;
    };
    let path = format!("{local}\\AniMangaStudio\\ipc.log");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{line}");
    }
}

/// Manda un evento de navegación a la SPA. Lo comparten los DOS caminos por los que puede llegar
/// un botón lateral del ratón (XBUTTON crudo y APPCOMMAND del driver).
fn nav_to_js(json: &str) {
    APP.with(|a| {
        if let Some(app) = a.borrow().as_ref() {
            post_to_js(&app.webview, json);
        }
    });
}

fn post_to_js(webview: &ICoreWebView2, json: &str) {
    let mut buf: Vec<u16> = json.encode_utf16().collect();
    buf.push(0);
    unsafe {
        let _ = webview.PostWebMessageAsJson(PCWSTR(buf.as_ptr()));
    }
}

// Lee un booleano de un JSON plano: "clave":true/false.
fn json_bool(json: &str, key: &str) -> Option<bool> {
    let pat = format!("\"{key}\"");
    let i = json.find(&pat)? + pat.len();
    let after = json[i..].trim_start_matches([' ', ':']);
    if after.starts_with("true") {
        Some(true)
    } else if after.starts_with("false") {
        Some(false)
    } else {
        None
    }
}

// Lee un número de un JSON plano: "clave":<número>.
fn json_num(json: &str, key: &str) -> Option<f64> {
    let pat = format!("\"{key}\"");
    let i = json.find(&pat)? + pat.len();
    let after = json[i..].trim_start_matches([' ', ':']);
    let end = after
        .find(|c: char| !(c.is_ascii_digit() || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E'))
        .unwrap_or(after.len());
    after[..end].parse().ok()
}

// Asegura el motor (creación perezosa al primer comando que lo use) y ejecuta `f`.
fn with_player(f: impl FnOnce(&Player)) {
    let handles = APP.with(|a| {
        a.borrow().as_ref().map(|app| {
            (
                app.hwnd,
                app.device.clone(),
                app.context.clone(),
                app.visual_video.clone(),
                app.dcomp.clone(),
                app.player.is_some(),
            )
        })
    });
    let Some((hwnd, device, context, visual_video, dcomp, exists)) = handles else {
        return;
    };
    if !exists {
        let (w, h) = client_size(hwnd);
        match unsafe { Player::new(hwnd, &device, &context, &visual_video, &dcomp, w, h) } {
            Ok(p) => APP.with(|a| {
                if let Some(app) = a.borrow_mut().as_mut() {
                    app.player = Some(p);
                }
            }),
            Err(e) => {
                ipc_log(&format!("[player] creación falló: {e:?}"));
                return;
            }
        }
    }
    APP.with(|a| {
        if let Some(p) = a.borrow().as_ref().and_then(|app| app.player.as_ref()) {
            f(p);
        }
    });
}

// Como with_player pero SIN crear el motor: los comandos de control (pause/seek/…)
// no deben instanciar mpv si aún no hay episodio cargado.
fn with_existing_player(f: impl FnOnce(&Player)) {
    APP.with(|a| {
        if let Some(p) = a.borrow().as_ref().and_then(|app| app.player.as_ref()) {
            f(p);
        }
    });
}

/* Progreso en el icono de la barra de tareas.
 *
 * `state`: none | normal | indeterminate | error | paused. `value`: 0-100 (sólo con `normal`).
 * COM ya está inicializado en STA por `CoInitializeEx` al arrancar, que es lo que pide
 * ITaskbarList3; por eso esto se llama SIEMPRE desde el hilo de la ventana.
 */
fn set_taskbar(hwnd: HWND, state: &str, value: f64) {
    TASKBAR.with(|slot| {
        let mut slot = slot.borrow_mut();
        if slot.is_none() {
            unsafe {
                match CoCreateInstance::<_, ITaskbarList3>(&TaskbarList, None, CLSCTX_ALL) {
                    Ok(tb) => {
                        // HrInit debe llamarse una vez antes de cualquier otro método.
                        if tb.HrInit().is_ok() {
                            *slot = Some(tb);
                        } else {
                            ipc_log("[taskbar] HrInit falló; se sigue sin barra de progreso");
                        }
                    }
                    // No disponible ≠ error de la app: se anota una vez y se continúa.
                    Err(e) => ipc_log(&format!("[taskbar] no disponible: {e:?}")),
                }
            }
        }
        let Some(tb) = slot.as_ref() else { return };
        let flag = match state {
            "normal" => TBPF_NORMAL,
            "indeterminate" => TBPF_INDETERMINATE,
            "error" => TBPF_ERROR,
            "paused" => TBPF_PAUSED,
            _ => TBPF_NOPROGRESS,
        };
        unsafe {
            let _ = tb.SetProgressState(hwnd, flag);
            if flag == TBPF_NORMAL {
                let v = value.clamp(0.0, 100.0) as u64;
                let _ = tb.SetProgressValue(hwnd, v, 100);
            }
        }
    });
}

fn task_notification_label(kind: &str) -> &'static str {
    match kind {
        "export" => "El tomo",
        "anime_upscale" => "El escalado de anime",
        "upscale" => "El escalado de manga",
        "download" | "versiondl" => "La descarga",
        "subtitle" | "subtitle_batch" => "Los subtítulos",
        "translate" => "La traducción",
        _ => "El trabajo",
    }
}

fn copy_wide<const N: usize>(target: &mut [u16; N], value: &str) {
    for (slot, code) in target.iter_mut().zip(value.encode_utf16()).take(N.saturating_sub(1)) {
        *slot = code;
    }
}

fn show_task_notification(hwnd: HWND, status: &str, kind: &str) {
    if status != "done" && status != "error" {
        return;
    }

    let label = task_notification_label(kind);
    let (title, body, icon_flag) = if status == "error" {
        ("Trabajo con error", format!("{label} falló. Haz clic para ver el detalle en Actividad."), NIIF_ERROR)
    } else {
        ("Trabajo completado", format!("{label} terminó. Haz clic para abrir Actividad."), NIIF_INFO)
    };

    unsafe {
        let mut data = NOTIFYICONDATAW::default();
        data.cbSize = size_of::<NOTIFYICONDATAW>() as u32;
        data.hWnd = hwnd;
        data.uID = TASK_NOTIFICATION_ICON_ID;
        data.hIcon = LoadIconW(None, IDI_APPLICATION).unwrap_or_default();

        if !TASK_NOTIFICATION_ICON.with(Cell::get) {
            data.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
            data.uCallbackMessage = WM_TASK_NOTIFICATION;
            copy_wide(&mut data.szTip, "AniManga Studio");
            if !Shell_NotifyIconW(NIM_ADD, &data).as_bool() {
                return;
            }
            TASK_NOTIFICATION_ICON.with(|registered| registered.set(true));
        }

        data.uFlags = NIF_INFO | NIF_REALTIME;
        data.dwInfoFlags = icon_flag | NIIF_NOSOUND | NIIF_RESPECT_QUIET_TIME;
        copy_wide(&mut data.szInfoTitle, title);
        copy_wide(&mut data.szInfo, &body);
        let _ = Shell_NotifyIconW(NIM_MODIFY, &data);
    }
}

fn remove_task_notification_icon(hwnd: HWND) {
    if !TASK_NOTIFICATION_ICON.with(|registered| registered.replace(false)) {
        return;
    }
    let data = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TASK_NOTIFICATION_ICON_ID,
        ..Default::default()
    };
    unsafe { let _ = Shell_NotifyIconW(NIM_DELETE, &data); }
}

fn handle_ipc(webview: &ICoreWebView2, msg: &str) {
    ipc_log(&format!("→ {msg}"));
    let cmd = json_str(msg, "cmd").unwrap_or_default();
    match cmd.as_str() {
        // Diagnóstico: comprueba el canal de ida y vuelta desde devtools.
        "ping" => post_to_js(webview, r#"{"event":"pong"}"#),
        "fullscreen" => {
            let on = json_bool(msg, "on").unwrap_or(true);
            // Extraer el hwnd y SOLTAR el borrow ANTES de set_fullscreen: SetWindowPos
            // dispara WM_SIZE síncrono, que hace APP.borrow_mut() → si el borrow siguiera
            // vivo, doble-borrow → pánico. Por eso el botón crasheaba y maximizar no.
            let hwnd = APP.with(|a| a.borrow().as_ref().map(|app| app.hwnd));
            if let Some(hwnd) = hwnd {
                unsafe { set_fullscreen(hwnd, on) };
            }
        }
        "loadfile" => {
            if let Some(path) = json_str(msg, "path") {
                // JSON escapa las barras invertidas de rutas Windows; deshacerlo
                // (mpv también acepta '/', ver protocolo en nativeBridge.js).
                let path = path.replace("\\\\", "\\");
                let start = json_num(msg, "start").unwrap_or(0.0);
                with_player(move |p| p.load(&path, start));
            }
        }
        "taskbar" => {
            let state = json_str(msg, "state").unwrap_or_else(|| "none".into());
            let value = json_num(msg, "value").unwrap_or(0.0);
            // Igual que en "fullscreen": sacar el hwnd y SOLTAR el borrow antes de llamar, que
            // los métodos del shell pueden bombear mensajes y volver a entrar en APP.
            let hwnd = APP.with(|a| a.borrow().as_ref().map(|app| app.hwnd));
            if let Some(hwnd) = hwnd {
                set_taskbar(hwnd, &state, value);
            }
        }
        "notify" => {
            let status = json_str(msg, "status").unwrap_or_default();
            let kind = json_str(msg, "kind").unwrap_or_default();
            let hwnd = APP.with(|a| a.borrow().as_ref().map(|app| app.hwnd));
            if let Some(hwnd) = hwnd {
                show_task_notification(hwnd, &status, &kind);
            }
        }
        "stop" => with_existing_player(|p| p.stop()),
        "subadd" => {
            if let Some(path) = json_str(msg, "path") {
                let path = path.replace("\\\\", "\\");
                with_existing_player(move |p| p.sub_add(&path));
            }
        }
        "pause" => {
            let v = json_bool(msg, "value").unwrap_or(true);
            with_existing_player(move |p| p.set_pause(v));
        }
        "seek" => {
            if let Some(pos) = json_num(msg, "pos") {
                with_existing_player(move |p| p.seek_absolute(pos));
            }
        }
        "volume" => {
            if let Some(v) = json_num(msg, "value") {
                with_existing_player(move |p| p.set_volume(v));
            }
        }
        "shaders" => {
            let tier = json_str(msg, "tier").unwrap_or_default();
            with_existing_player(move |p| p.set_shaders(&tier));
        }
        "setprop" => {
            if let (Some(name), Some(val)) = (json_str(msg, "name"), json_str(msg, "value")) {
                with_existing_player(move |p| p.set_prop(&name, &val));
            }
        }
        "bright" => {
            if let Some(v) = json_num(msg, "value") {
                with_existing_player(move |p| p.set_bright(v as f32));
            }
        }
        "sat" => {
            if let Some(v) = json_num(msg, "value") {
                with_existing_player(move |p| p.set_sat(v as f32));
            }
        }
        "cursor" => {
            let hide = json_bool(msg, "hide").unwrap_or(false);
            CURSOR_HIDE.with(|c| c.set(hide));
            // Aplicar ya (SetCursor sólo surte efecto en el hilo dueño de la ventana).
            let cur = if hide {
                HCURSOR::default() // null = cursor oculto
            } else {
                unsafe { LoadCursorW(None, IDC_ARROW) }.unwrap_or_default()
            };
            unsafe { SetCursor(cur) };
        }
        "track" => {
            if let Some(aid) = json_str(msg, "aid") {
                with_existing_player(move |p| p.set_track("aid", &aid));
            }
            if let Some(sid) = json_str(msg, "sid") {
                with_existing_player(move |p| p.set_track("sid", &sid));
            }
        }
        "window" => {
            // Controles de la barra de título propia. Extraer el hwnd y SOLTAR el borrow
            // ANTES de window_command: 'drag'/maximizar disparan WM_SIZE síncrono que hace
            // APP.borrow_mut() → doble-borrow si el borrow siguiera vivo (igual que fullscreen).
            let action = json_str(msg, "action").unwrap_or_default();
            let hwnd = APP.with(|a| a.borrow().as_ref().map(|app| app.hwnd));
            if let Some(hwnd) = hwnd {
                unsafe { window_command(hwnd, &action) };
            }
        }
        _ => {}
    }
}

fn register_ipc(webview: &ICoreWebView2) -> windows::core::Result<()> {
    let wv = webview.clone();
    let handler = WebMessageReceivedEventHandler::create(Box::new(move |_sender, args| {
        let Some(args) = args else {
            return Ok(());
        };
        let mut raw = PWSTR::null();
        unsafe {
            args.WebMessageAsJson(&mut raw)?;
        }
        let msg = unsafe { raw.to_string() }.unwrap_or_default();
        handle_ipc(&wv, &msg);
        Ok(())
    }));
    let mut token = EventRegistrationToken::default();
    unsafe {
        webview.add_WebMessageReceived(&handler, &mut token)?;
    }
    Ok(())
}

// Grosor del borde de redimensionado (px físicos) para una ventana sin marco.
unsafe fn resize_border(hwnd: HWND) -> i32 {
    let dpi = GetDpiForWindow(hwnd);
    (GetSystemMetricsForDpi(SM_CXFRAME, dpi) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)).max(6)
}

// Hit-test de una ventana SIN marco: sintetiza los bordes de redimensionado (el
// resto es HTCLIENT → el WebView recibe el ratón normalmente; el arrastre de la
// barra propia va por IPC). No hay bordes cuando está maximizada.
unsafe fn hit_test(hwnd: HWND, lparam: LPARAM) -> LRESULT {
    let mut pt = POINT {
        x: (lparam.0 & 0xffff) as i16 as i32,
        y: ((lparam.0 >> 16) & 0xffff) as i16 as i32,
    };
    let _ = ScreenToClient(hwnd, &mut pt);
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    // Sin bordes de redimensionado cuando está maximizada o en fullscreen del player
    // (set_fullscreen quita WS_THICKFRAME) → toda la superficie es cliente.
    let style = GetWindowLongW(hwnd, GWL_STYLE) as u32;
    if IsZoomed(hwnd).as_bool() || style & WS_THICKFRAME.0 == 0 {
        return LRESULT(HTCLIENT as isize);
    }
    let b = resize_border(hwnd);
    let top = pt.y < b;
    let bottom = pt.y >= rc.bottom - b;
    let left = pt.x < b;
    let right = pt.x >= rc.right - b;
    let code = match (top, bottom, left, right) {
        (true, _, true, _) => HTTOPLEFT,
        (true, _, _, true) => HTTOPRIGHT,
        (_, true, true, _) => HTBOTTOMLEFT,
        (_, true, _, true) => HTBOTTOMRIGHT,
        (true, ..) => HTTOP,
        (_, true, ..) => HTBOTTOM,
        (_, _, true, _) => HTLEFT,
        (_, _, _, true) => HTRIGHT,
        _ => HTCLIENT,
    };
    LRESULT(code as isize)
}

// Controles de la barra de título propia (IPC {cmd:'window', action}).
unsafe fn window_command(hwnd: HWND, action: &str) {
    match action {
        "minimize" => {
            let _ = ShowWindow(hwnd, SW_MINIMIZE);
        }
        "toggleMaximize" => {
            if IsZoomed(hwnd).as_bool() {
                let _ = ShowWindow(hwnd, SW_RESTORE);
            } else {
                let _ = ShowWindow(hwnd, SW_MAXIMIZE);
            }
        }
        "close" => {
            dbg_log("[ipc] close pressed -> PostMessage WM_CLOSE");
            let _ = PostMessageW(hwnd, WM_CLOSE, WPARAM(0), LPARAM(0));
        }
        "drag" => {
            // Iniciar el bucle de arrastre nativo como si se pulsara la barra de título.
            let _ = ReleaseCapture();
            let mut p = POINT::default();
            let _ = GetCursorPos(&mut p);
            let lp = ((p.y as isize) << 16) | (p.x as isize & 0xffff);
            SendMessageW(hwnd, WM_NCLBUTTONDOWN, WPARAM(HTCAPTION as usize), LPARAM(lp));
        }
        _ => {}
    }
}

extern "system" fn wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        match msg {
            WM_NCCALCSIZE if wparam.0 != 0 => {
                // Quitar la barra de título/marco del sistema: devolvemos el rect tal cual
                // (cliente = ventana entera). Al maximizar, Windows posiciona la ventana
                // desplazada por el grosor del marco → recortaría el contenido y taparía la
                // barra de tareas; lo compensamos metiendo el cliente ese grosor.
                let params = &mut *(lparam.0 as *mut NCCALCSIZE_PARAMS);
                // OJO: el inset de maximizado NO debe aplicarse en pantalla completa borderless.
                // set_fullscreen posiciona la ventana cubriendo el monitor entero, pero si se
                // entró en fullscreen DESDE una ventana maximizada, `IsZoomed` sigue devolviendo
                // true → se recortaba el cliente por los 4 lados y el vídeo del reproductor
                // quedaba con un "borde alrededor". En fullscreen (FS = Some) el cliente debe ser
                // la ventana entera, sin inset.
                let in_fullscreen = FS.with(|f| f.borrow().is_some());
                if IsZoomed(hwnd).as_bool() && !in_fullscreen {
                    let dpi = GetDpiForWindow(hwnd);
                    let fx =
                        GetSystemMetricsForDpi(SM_CXFRAME, dpi) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi);
                    let fy =
                        GetSystemMetricsForDpi(SM_CYFRAME, dpi) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi);
                    params.rgrc[0].left += fx;
                    params.rgrc[0].right -= fx;
                    params.rgrc[0].top += fy;
                    params.rgrc[0].bottom -= fy;
                }
                LRESULT(0)
            }
            WM_NCHITTEST => hit_test(hwnd, lparam),
            WM_MOUSEMOVE | WM_LBUTTONDOWN | WM_LBUTTONUP | WM_RBUTTONDOWN | WM_RBUTTONUP
            | WM_MBUTTONDOWN | WM_MBUTTONUP => {
                let x = (lparam.0 & 0xffff) as i16 as i32;
                let y = ((lparam.0 >> 16) & 0xffff) as i16 as i32;
                // Capturar el ratón mientras se sostiene un botón: así el arrastre (sliders,
                // barra de progreso, selección) sigue recibiendo WM_MOUSEMOVE aunque el
                // cursor salga del rect de la ventana; se suelta al levantar el botón.
                match msg {
                    WM_LBUTTONDOWN | WM_RBUTTONDOWN | WM_MBUTTONDOWN => {
                        let _ = SetCapture(hwnd);
                    }
                    WM_LBUTTONUP | WM_RBUTTONUP | WM_MBUTTONUP => {
                        let _ = ReleaseCapture();
                    }
                    _ => {}
                }
                APP.with(|a| {
                    if let Some(app) = a.borrow().as_ref() {
                        let _ = app.controller.SendMouseInput(
                            COREWEBVIEW2_MOUSE_EVENT_KIND(msg as i32),
                            // Estado de botones/modificadores (MK_* en wparam) para que el
                            // WebView sepa que se está SOSTENIENDO un botón durante el move
                            // → el arrastre/hold funciona (antes iba 0 = sin botón).
                            COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS((wparam.0 & 0xffff) as i32),
                            0,
                            POINT { x, y },
                        );
                    }
                });
                LRESULT(0)
            }
            WM_MOUSEWHEEL => {
                let delta = ((wparam.0 >> 16) & 0xffff) as i16 as i32;
                // WM_MOUSEWHEEL trae coords de PANTALLA → convertir a cliente.
                let mut p = POINT {
                    x: (lparam.0 & 0xffff) as i16 as i32,
                    y: ((lparam.0 >> 16) & 0xffff) as i16 as i32,
                };
                let _ = ScreenToClient(hwnd, &mut p);
                APP.with(|a| {
                    if let Some(app) = a.borrow().as_ref() {
                        let _ = app.controller.SendMouseInput(
                            COREWEBVIEW2_MOUSE_EVENT_KIND(WM_MOUSEWHEEL as i32),
                            COREWEBVIEW2_MOUSE_EVENT_VIRTUAL_KEYS(0),
                            delta as u32,
                            p,
                        );
                    }
                });
                LRESULT(0)
            }
            WM_XBUTTONDOWN => {
                // Botones laterales del ratón (atrás/adelante). En composición NO se
                // reenvían al WebView como los demás, así que los traducimos a una
                // navegación de historial y se la pasamos a la SPA por IPC → history
                // back/forward (mismo recorrido que popstate). XBUTTON1=atrás, 2=adelante.
                let xbtn = ((wparam.0 >> 16) & 0xffff) as u16;
                let json = match xbtn {
                    1 => Some(r#"{"event":"navigate","dir":"back"}"#),
                    2 => Some(r#"{"event":"navigate","dir":"forward"}"#),
                    _ => None,
                };
                if let Some(json) = json {
                    nav_to_js(json);
                }
                LRESULT(1) // TRUE = manejado
            }
            // ⚠️ Windows sintetiza el WM_APPCOMMAND desde el **UP**, no desde el DOWN. Dejar que
            // el UP llegue a DefWindowProc hacía que UNA pulsación del botón lateral emitiera DOS
            // navegaciones: la de arriba (XBUTTON) y otra por el brazo de WM_APPCOMMAND. Con el
            // historial a medias eso no se notaba; ahora que cada paso deja entrada, un solo clic
            // te saltaba DOS pantallas. Se traga el UP y ya no hay APPCOMMAND que sintetizar.
            WM_XBUTTONUP => LRESULT(1),
            WM_APPCOMMAND => {
                // Muchos ratones (Logitech, Razel, y los que pasan por su software) NO emiten
                // XBUTTON: su driver traduce los botones laterales a un APPCOMMAND de navegador.
                // En ese caso el brazo de arriba no llega a ejecutarse nunca y los botones
                // parecen "no hacer nada" — que es exactamente el síntoma reportado.
                // El comando va en el HIWORD del lParam, con banderas en los 4 bits altos.
                // Valores literales a propósito: los `APPCOMMAND_*` del crate `windows` no están
                // en ámbito aquí y, escritos en MAYÚSCULAS dentro de un `match`, Rust los toma
                // como PATRONES DE ENLACE — el primer brazo casaría con todo y cualquier tecla
                // multimedia navegaría hacia atrás. (El compilador lo avisa como "should have a
                // snake case name"; ese warning era el bug.)
                const APPCMD_BACK: u16 = 1; // APPCOMMAND_BROWSER_BACKWARD
                const APPCMD_FWD: u16 = 2; // APPCOMMAND_BROWSER_FORWARD
                let cmd = (((lparam.0 >> 16) & 0xffff) as u16) & 0x0fff;
                let json = match cmd {
                    APPCMD_BACK => Some(r#"{"event":"navigate","dir":"back"}"#),
                    APPCMD_FWD => Some(r#"{"event":"navigate","dir":"forward"}"#),
                    _ => None,
                };
                match json {
                    Some(json) => {
                        nav_to_js(json);
                        LRESULT(1) // manejado
                    }
                    // Volumen, reproducción multimedia… no son nuestros: que sigan su curso.
                    None => DefWindowProcW(hwnd, msg, wparam, lparam),
                }
            }
            WM_SETCURSOR => {
                // En composición el WebView no controla el cursor de la ventana; lo
                // gestiona el host. En el área cliente respetamos CURSOR_HIDE (que pone
                // el JS al auto-ocultar la UI): None = cursor oculto.
                if (lparam.0 & 0xffff) as i32 == HTCLIENT as i32 {
                    let hide = CURSOR_HIDE.with(|c| c.get());
                    let cur = if hide {
                        HCURSOR::default() // null = cursor oculto
                    } else {
                        // Usar el cursor que PIDE el WebView según el elemento bajo el ratón
                        // (manita en enlaces/botones, I-beam en texto, resize, etc.). Antes
                        // forzábamos siempre la flecha → nunca cambiaba a "clickable".
                        let wv_cur = APP.with(|a| {
                            a.borrow().as_ref().and_then(|app| {
                                let mut hc = HCURSOR::default();
                                if app.controller.Cursor(&mut hc).is_ok() && !hc.is_invalid() {
                                    Some(hc)
                                } else {
                                    None
                                }
                            })
                        });
                        wv_cur.unwrap_or_else(|| LoadCursorW(None, IDC_ARROW).unwrap_or_default())
                    };
                    SetCursor(cur);
                    LRESULT(1)
                } else {
                    DefWindowProcW(hwnd, msg, wparam, lparam)
                }
            }
            WM_DPICHANGED => {
                // La ventana cambió de monitor (o de escalado). Windows sugiere el
                // nuevo rect físico en lparam; aplicarlo dispara WM_SIZE → el vídeo
                // se recrea a la resolución nativa del nuevo monitor. También hay que
                // reajustar la escala de rasterizado de la UI del WebView.
                let rect = &*(lparam.0 as *const RECT);
                // SetWindowPos ANTES de tocar APP: dispara WM_SIZE síncrono que hace
                // borrow_mut() (mismo gotcha de reentrancia que en fullscreen).
                let _ = SetWindowPos(
                    hwnd,
                    None,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
                let scale = (wparam.0 & 0xffff) as f64 / 96.0;
                let ctrl3 = APP.with(|a| {
                    a.borrow()
                        .as_ref()
                        .and_then(|app| app.ctrl.cast::<ICoreWebView2Controller3>().ok())
                });
                if let Some(c3) = ctrl3 {
                    let _ = c3.SetRasterizationScale(if scale > 0.0 { scale } else { 1.0 });
                }
                LRESULT(0)
            }
            WM_SIZE => {
                let cw = (lparam.0 & 0xffff) as i32;
                let ch = ((lparam.0 >> 16) & 0xffff) as i32;
                if cw > 0 && ch > 0 {
                    APP.with(|a| {
                        if let Some(app) = a.borrow_mut().as_mut() {
                            let _ = app.ctrl.SetBounds(RECT {
                                left: 0,
                                top: 0,
                                right: cw,
                                bottom: ch,
                            });
                            // Redimensionar el swapchain del vídeo al nuevo tamaño.
                            if let Some(p) = app.player.as_mut() {
                                p.resize(cw as u32, ch as u32);
                            }
                            let _ = app.dcomp.Commit();
                        }
                    });
                }
                // Avisar a la barra de título propia para que alterne el icono maximizar/
                // restaurar (también cuando se maximiza con Win+↑ o arrastre al borde).
                if wparam.0 == SIZE_MAXIMIZED as usize || wparam.0 == SIZE_RESTORED as usize {
                    let json = if wparam.0 == SIZE_MAXIMIZED as usize {
                        r#"{"event":"windowState","maximized":true}"#
                    } else {
                        r#"{"event":"windowState","maximized":false}"#
                    };
                    APP.with(|a| {
                        if let Some(app) = a.borrow().as_ref() {
                            post_to_js(&app.webview, json);
                        }
                    });
                }
                LRESULT(0)
            }
            WM_TIMER => {
                if wparam.0 == TIMER_RENDER {
                    // Un frame del motor de vídeo (Present(1) marca el ritmo a vblank).
                    APP.with(|a| {
                        let app_ref = a.borrow();
                        let Some(app) = app_ref.as_ref() else { return };
                        let Some(p) = app.player.as_ref() else { return };
                        p.render();
                        // Reportar tiempo a JS ~2×/s: con Present(1) a vblank el bucle va a
                        // ~60 fps (no 120), así que 120 frames eran ~2 s y el tiempo saltaba
                        // de 2 en 2 s. 30 frames ≈ 0.5 s → el contador avanza segundo a segundo.
                        FRAME.with(|f| {
                            let mut n = f.borrow_mut();
                            *n = n.wrapping_add(1);
                            if *n % 30 == 0 {
                                // Sospechoso de los tirones: estas lecturas son SÍNCRONAS y
                                // toman el cerrojo del núcleo de mpv; si está ocupado, paran
                                // el hilo de la ventana (y con él el vídeo). Medimos por
                                // separado las propiedades y el salto a WebView2.
                                let t_prop = Instant::now();
                                let vals = p.time_pos().map(|pos| {
                                    (pos, p.duration().unwrap_or(0.0), p.paused())
                                });
                                let prop_ms = t_prop.elapsed().as_millis();
                                let mut post_ms = 0;
                                if let Some((pos, dur, paused)) = vals {
                                    let json = format!(
                                        r#"{{"event":"time","pos":{pos},"duration":{dur},"paused":{paused}}}"#
                                    );
                                    let t_post = Instant::now();
                                    post_to_js(&app.webview, &json);
                                    post_ms = t_post.elapsed().as_millis();
                                }
                                if prop_ms > 50 || post_ms > 50 {
                                    player::log_line(&format!(
                                        "[bloqueo] props={prop_ms}ms post_js={post_ms}ms"
                                    ));
                                }
                            }
                        });
                    });
                } else {
                    // Recomponer por si el 1er paint del WebView llegó tarde.
                    APP.with(|a| {
                        if let Some(app) = a.borrow().as_ref() {
                            let _ = app.dcomp.Commit();
                        }
                    });
                }
                LRESULT(0)
            }
            WM_TASK_NOTIFICATION => {
                match lparam.0 as u32 {
                    NIN_BALLOONUSERCLICK => {
                        remove_task_notification_icon(hwnd);
                        let _ = ShowWindow(hwnd, SW_RESTORE);
                        let _ = SetForegroundWindow(hwnd);
                        let webview = APP.with(|a| a.borrow().as_ref().map(|app| app.webview.clone()));
                        if let Some(webview) = webview {
                            post_to_js(&webview, r#"{"event":"openActivity"}"#);
                        }
                    }
                    NIN_BALLOONHIDE | NIN_BALLOONTIMEOUT => remove_task_notification_icon(hwnd),
                    _ => {}
                }
                LRESULT(0)
            }
            WM_CLOSE => {
                // Ocultar YA la ventana para que el cierre se sienta instantáneo aunque el
                // teardown tarde un pelín; luego cierre autoritativo único (WebView2 +
                // backend WSL + autotermina). El Job Object remata cualquier hijo restante.
                dbg_log("[wm] WM_CLOSE");
                remove_task_notification_icon(hwnd);
                let _ = ShowWindow(hwnd, SW_HIDE);
                shutdown_everything()
            }
            WM_DESTROY => {
                remove_task_notification_icon(hwnd);
                PostQuitMessage(0);
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}
