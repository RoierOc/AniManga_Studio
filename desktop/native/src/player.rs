// Motor de vídeo nativo (Fase 2) — libmpv por render API OpenGL → textura D3D11
// compartida (WGL_NV_DX_interop2) → swapchain de composición que DirectComposition
// muestra en `visual_video`, POR DEBAJO del WebView2 transparente (la UI Vue).
//
// Integrado con la shell nativa:
//   · se crea ON-DEMAND al abrir un episodio (no al arrancar la app);
//   · reutiliza el ID3D11Device de la shell (el mismo con el que se creó DComp);
//   · el bucle de render del PoC se sustituye por render dirigido por WM_TIMER
//     (Present(1,..) marca el ritmo a vblank). El diseño "correcto" usaría el
//     update-callback de mpv; queda como mejora (ver HANDOFF).

use std::cell::{Cell, RefCell};
use std::ffi::{c_void, CString};
use std::time::Instant;
use std::mem::size_of;
use std::os::raw::c_char;

use libmpv2::{
    render::{mpv_render_update, OpenGLInitParams, RenderContext, RenderParam, RenderParamApiType},
    Mpv,
};
use windows::core::{s, Interface, PCSTR};
use windows::Win32::Foundation::{HANDLE, HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::Graphics::Direct3D11::{
    ID3D11Device, ID3D11DeviceContext, ID3D11Resource, ID3D11Texture2D, D3D11_BIND_RENDER_TARGET,
    D3D11_BIND_SHADER_RESOURCE, D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
};
use windows::Win32::Graphics::DirectComposition::{IDCompositionDevice, IDCompositionVisual};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_ALPHA_MODE_IGNORE, DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709,
    DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020, DXGI_FORMAT, DXGI_FORMAT_B8G8R8A8_UNORM,
    DXGI_FORMAT_R10G10B10A2_UNORM, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    IDXGIAdapter, IDXGIDevice, IDXGIFactory2, IDXGIOutput6, IDXGISwapChain1, IDXGISwapChain3,
    IDXGISwapChain4, DXGI_HDR_METADATA_HDR10, DXGI_HDR_METADATA_TYPE_HDR10, DXGI_OUTPUT_DESC1,
    DXGI_PRESENT, DXGI_SCALING_STRETCH, DXGI_SWAP_CHAIN_COLOR_SPACE_SUPPORT_FLAG_PRESENT,
    DXGI_SWAP_CHAIN_DESC1, DXGI_SWAP_CHAIN_FLAG,
    DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, DXGI_USAGE_RENDER_TARGET_OUTPUT,
};
use windows::Win32::Graphics::Gdi::{
    GetDC, GetMonitorInfoW, MonitorFromWindow, HDC, MONITORINFOEXW, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::Devices::Display::{
    DisplayConfigGetDeviceInfo, GetDisplayConfigBufferSizes, QueryDisplayConfig,
    DISPLAYCONFIG_DEVICE_INFO_GET_SDR_WHITE_LEVEL, DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
    DISPLAYCONFIG_MODE_INFO, DISPLAYCONFIG_PATH_INFO, DISPLAYCONFIG_SDR_WHITE_LEVEL,
    DISPLAYCONFIG_SOURCE_DEVICE_NAME, QDC_ONLY_ACTIVE_PATHS,
};
use windows::Win32::Graphics::OpenGL::{
    wglCreateContext, wglGetProcAddress, wglMakeCurrent, ChoosePixelFormat, SetPixelFormat, HGLRC,
    PFD_DOUBLEBUFFER, PFD_DRAW_TO_WINDOW, PFD_MAIN_PLANE, PFD_SUPPORT_OPENGL, PFD_TYPE_RGBA,
    PIXELFORMATDESCRIPTOR,
};
use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetModuleHandleW, GetProcAddress};
use windows::Win32::UI::WindowsAndMessaging::*;

/// Mensaje del temporizador de render (id de SetTimer en la ventana principal).
pub const TIMER_RENDER: usize = 2;

const SHADER_DIR: &str = r"C:/Program Files (x86)/mpv/mpv/shaders";

// "high" = Anime4K Modo A (HQ), preset ESTÁNDAR — EXACTO al mpv.conf del usuario, que en
// mpv.exe se ve limpio. UN SOLO pase de Restore (VL). Antes encadenábamos DOS restore
// (Restore_CNN_UL + Restore_CNN_M): eso NO es un preset estándar, sobre-procesa y amplifica
// el ruido en zonas oscuras → shimmer/parpadeo SUTIL de luminancia (confirmado en vivo:
// con doble restore parpadea, con este preset de un solo restore NO). Ver
// project_native_player_flicker en memoria.
const SHADERS_HIGH: &[&str] = &[
    "Anime4K_Clamp_Highlights.glsl",
    "Anime4K_Restore_CNN_VL.glsl",
    "Anime4K_Upscale_CNN_x2_VL.glsl",
    "Anime4K_AutoDownscalePre_x2.glsl",
    "Anime4K_AutoDownscalePre_x4.glsl",
    "Anime4K_Upscale_CNN_x2_M.glsl",
];
// "ultra" = Modo A (HQ) + Thin_HQ (bordes más nítidos). Igual que "high" (un solo restore,
// sin doble pase que causaba el shimmer) más el thinning de líneas al final.
const SHADERS_ULTRA: &[&str] = &[
    "Anime4K_Clamp_Highlights.glsl",
    "Anime4K_Restore_CNN_VL.glsl",
    "Anime4K_Upscale_CNN_x2_VL.glsl",
    "Anime4K_AutoDownscalePre_x2.glsl",
    "Anime4K_AutoDownscalePre_x4.glsl",
    "Anime4K_Upscale_CNN_x2_M.glsl",
    "Anime4K_Thin_HQ.glsl",
];

fn tier_shaders(tier: &str) -> &'static [&'static str] {
    match tier {
        "high" => SHADERS_HIGH,
        "ultra" => SHADERS_ULTRA,
        _ => &[], // off / none / desconocido
    }
}

// --- Constantes GL / WGL_NV_DX_interop ---
const GL_TEXTURE_2D: u32 = 0x0DE1;
const GL_FRAMEBUFFER: u32 = 0x8D40;
const GL_COLOR_ATTACHMENT0: u32 = 0x8CE0;
const GL_FRAMEBUFFER_COMPLETE: u32 = 0x8CD5;
const WGL_ACCESS_WRITE_DISCARD_NV: u32 = 0x0002;

struct Gl {
    gen_textures: extern "system" fn(i32, *mut u32),
    gen_framebuffers: extern "system" fn(i32, *mut u32),
    bind_framebuffer: extern "system" fn(u32, u32),
    framebuffer_texture_2d: extern "system" fn(u32, u32, u32, u32, i32),
    check_framebuffer_status: extern "system" fn(u32) -> u32,
    flush: extern "system" fn(),
    dx_open_device: extern "system" fn(*mut c_void) -> HANDLE,
    dx_register_object: extern "system" fn(HANDLE, *mut c_void, u32, u32, u32) -> HANDLE,
    dx_unregister_object: extern "system" fn(HANDLE, HANDLE) -> i32,
    dx_lock_objects: extern "system" fn(HANDLE, i32, *mut HANDLE) -> i32,
    dx_unlock_objects: extern "system" fn(HANDLE, i32, *mut HANDLE) -> i32,
}

unsafe fn load<T>(name: &str) -> T {
    let p = gl_addr(&(), name);
    assert!(!p.is_null(), "no se pudo cargar {name}");
    std::mem::transmute_copy::<_, T>(&(p as usize))
}

impl Gl {
    unsafe fn load() -> Gl {
        Gl {
            gen_textures: load("glGenTextures"),
            gen_framebuffers: load("glGenFramebuffers"),
            bind_framebuffer: load("glBindFramebuffer"),
            framebuffer_texture_2d: load("glFramebufferTexture2D"),
            check_framebuffer_status: load("glCheckFramebufferStatus"),
            flush: load("glFlush"),
            dx_open_device: load("wglDXOpenDeviceNV"),
            dx_register_object: load("wglDXRegisterObjectNV"),
            dx_unregister_object: load("wglDXUnregisterObjectNV"),
            dx_lock_objects: load("wglDXLockObjectsNV"),
            dx_unlock_objects: load("wglDXUnlockObjectsNV"),
        }
    }
}

// get_proc_address para libmpv: wgl para GL >1.1 y extensiones; opengl32.dll para
// las de GL 1.1 (glFlush, glGenTextures) que wglGetProcAddress devuelve null.
fn gl_addr(_ctx: &(), name: &str) -> *mut c_void {
    let c = CString::new(name).unwrap();
    let pcstr = PCSTR(c.as_ptr() as *const u8);
    unsafe {
        if let Some(p) = wglGetProcAddress(pcstr) {
            return p as usize as *mut c_void;
        }
        if let Ok(module) = GetModuleHandleA(s!("opengl32.dll")) {
            if let Some(p) = GetProcAddress(module, pcstr) {
                return p as usize as *mut c_void;
            }
        }
    }
    std::ptr::null_mut()
}

fn mpv_command_args(mpv: &Mpv, args: &[&str]) -> i32 {
    let cstrs: Vec<CString> = args.iter().map(|s| CString::new(*s).unwrap()).collect();
    let mut argv: Vec<*const c_char> = cstrs.iter().map(|c| c.as_ptr()).collect();
    argv.push(std::ptr::null());
    unsafe { libmpv2_sys::mpv_command(mpv.ctx.as_ptr(), argv.as_mut_ptr()) }
}

extern "system" fn gl_host_wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
}

/// Descriptor del monitor donde está `hwnd` (colorspace + luminancias + primarios).
/// Base para decidir HDR y para etiquetar el swapchain con metadata HDR10.
unsafe fn monitor_output_desc(device: &ID3D11Device, hwnd: HWND) -> Option<DXGI_OUTPUT_DESC1> {
    let hmon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    let dxgi_device = device.cast::<IDXGIDevice>().ok()?;
    let adapter: IDXGIAdapter = dxgi_device.GetAdapter().ok()?;
    let mut i = 0u32;
    while let Ok(output) = adapter.EnumOutputs(i) {
        i += 1;
        if let Ok(o6) = output.cast::<IDXGIOutput6>() {
            if let Ok(desc) = o6.GetDesc1() {
                if desc.Monitor == hmon {
                    return Some(desc);
                }
            }
        }
    }
    None
}

/// Resolución FÍSICA (píxeles reales) del monitor, de DesktopCoordinates. Sirve para
/// verificar que el vídeo se renderiza a la nativa (clave para Anime4K: si renderizamos
/// a menos —p.ej. lógico 1280x720 en una TV 4K al 300%—, la CNN sube 2x sobre pocos
/// píxeles y el resultado sale suave; mpv a pantalla completa renderiza a la física).
fn monitor_physical(desc: &Option<DXGI_OUTPUT_DESC1>) -> (i32, i32) {
    match desc {
        Some(d) => (
            d.DesktopCoordinates.right - d.DesktopCoordinates.left,
            d.DesktopCoordinates.bottom - d.DesktopCoordinates.top,
        ),
        None => (0, 0),
    }
}

/// (¿HDR?, pico en nits) a partir del descriptor del monitor.
fn desc_hdr(desc: &Option<DXGI_OUTPUT_DESC1>) -> (bool, f32) {
    match desc {
        Some(d) => (
            d.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020,
            d.MaxLuminance,
        ),
        None => (false, 0.0),
    }
}

/// Etiqueta el swapchain con metadata HDR10 = capacidades reales del TV (luminancia
/// y primarios). Sin esto el TV asume masterización a 10000 nits y re-tonemapea el
/// contenido (se ve más apagado que mpv, que sí manda esta metadata).
unsafe fn apply_hdr_metadata(swapchain: &IDXGISwapChain1, desc: &DXGI_OUTPUT_DESC1) {
    let Ok(sc4) = swapchain.cast::<IDXGISwapChain4>() else {
        return;
    };
    let c = |v: f32| (v * 50000.0) as u16; // cromaticidad → unidades 0.00002
    let md = DXGI_HDR_METADATA_HDR10 {
        RedPrimary: [c(desc.RedPrimary[0]), c(desc.RedPrimary[1])],
        GreenPrimary: [c(desc.GreenPrimary[0]), c(desc.GreenPrimary[1])],
        BluePrimary: [c(desc.BluePrimary[0]), c(desc.BluePrimary[1])],
        WhitePoint: [c(desc.WhitePoint[0]), c(desc.WhitePoint[1])],
        MaxMasteringLuminance: desc.MaxLuminance as u32,
        MinMasteringLuminance: (desc.MinLuminance * 10000.0) as u32,
        MaxContentLightLevel: desc.MaxLuminance as u16,
        MaxFrameAverageLightLevel: desc.MaxFullFrameLuminance as u16,
    };
    let bytes = std::slice::from_raw_parts(
        &md as *const _ as *const u8,
        size_of::<DXGI_HDR_METADATA_HDR10>(),
    );
    let _ = sc4.SetHDRMetaData(DXGI_HDR_METADATA_TYPE_HDR10, Some(bytes));
}

/// Blanco SDR (nits) que Windows aplica al contenido SDR en el monitor de `hwnd`
/// cuando está en HDR (slider "Brillo del contenido SDR"). mpv.exe lo respeta; hay
/// que igualarlo para que el brillo coincida. Devuelve 0 si no se puede leer.
unsafe fn sdr_white_nits(hwnd: HWND) -> f32 {
    let hmon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    let mut mi = MONITORINFOEXW::default();
    mi.monitorInfo.cbSize = size_of::<MONITORINFOEXW>() as u32;
    if !GetMonitorInfoW(hmon, &mut mi.monitorInfo as *mut _).as_bool() {
        return 0.0;
    }
    let dev = mi.szDevice;

    let (mut n_path, mut n_mode) = (0u32, 0u32);
    if GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &mut n_path, &mut n_mode).0 != 0 {
        return 0.0;
    }
    let mut paths = vec![DISPLAYCONFIG_PATH_INFO::default(); n_path as usize];
    let mut modes = vec![DISPLAYCONFIG_MODE_INFO::default(); n_mode as usize];
    if QueryDisplayConfig(
        QDC_ONLY_ACTIVE_PATHS,
        &mut n_path,
        paths.as_mut_ptr(),
        &mut n_mode,
        modes.as_mut_ptr(),
        None,
    )
    .0
        != 0
    {
        return 0.0;
    }

    for p in paths.iter().take(n_path as usize) {
        // ¿Este path corresponde al monitor de la ventana? Comparar por nombre GDI.
        let mut src = DISPLAYCONFIG_SOURCE_DEVICE_NAME::default();
        src.header.r#type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
        src.header.size = size_of::<DISPLAYCONFIG_SOURCE_DEVICE_NAME>() as u32;
        src.header.adapterId = p.sourceInfo.adapterId;
        src.header.id = p.sourceInfo.id;
        if DisplayConfigGetDeviceInfo(&mut src.header) != 0 || src.viewGdiDeviceName != dev {
            continue;
        }
        let mut sdr = DISPLAYCONFIG_SDR_WHITE_LEVEL::default();
        sdr.header.r#type = DISPLAYCONFIG_DEVICE_INFO_GET_SDR_WHITE_LEVEL;
        sdr.header.size = size_of::<DISPLAYCONFIG_SDR_WHITE_LEVEL>() as u32;
        sdr.header.adapterId = p.targetInfo.adapterId;
        sdr.header.id = p.targetInfo.id;
        if DisplayConfigGetDeviceInfo(&mut sdr.header) != 0 {
            return 0.0;
        }
        // nits = valor / 1000 * 80.
        return sdr.SDRWhiteLevel as f32 / 1000.0 * 80.0;
    }
    0.0
}

/// Log de diagnóstico a ipc.log.
fn log_line(msg: &str) {
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(r"C:/Users/Example/AppData/Local/AniMangaStudio/ipc.log")
    {
        use std::io::Write as _;
        let _ = writeln!(f, "{msg}");
    }
}

/// Formato del swapchain/textura interop según HDR: 10-bit para HDR10, 8-bit para SDR.
fn hdr_format(hdr: bool) -> DXGI_FORMAT {
    if hdr {
        DXGI_FORMAT_R10G10B10A2_UNORM
    } else {
        DXGI_FORMAT_B8G8R8A8_UNORM
    }
}

/// Etiqueta el swapchain como SDR sRGB (G22/P709). Es el DEFAULT: la inmensa mayoría
/// del anime es SDR y, replicando a mpv.exe, el contenido SDR se presenta como SDR
/// aunque el display esté en HDR — Windows lo compone y le aplica el slider "brillo de
/// contenido SDR". apply_content_colorspace() (en el impl) sube a PQ solo si el
/// CONTENIDO es HDR real (PQ/HLG). En display SDR también es este el colorspace.
unsafe fn set_srgb_colorspace(swapchain: &IDXGISwapChain1) {
    if let Ok(sc3) = swapchain.cast::<IDXGISwapChain3>() {
        let _ = sc3.SetColorSpace1(DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709);
    }
}

pub struct Player {
    // OJO al orden: los campos se destruyen en orden de declaración, y
    // RenderContext (mpv_render_context_free) debe liberarse ANTES que Mpv.
    render_ctx: RenderContext,
    mpv: Mpv,
    gl: Gl,
    gl_dx_device: HANDLE,
    gl_obj: HANDLE,
    gl_tex: u32,
    fbo: u32,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    // Option para poder soltarlas antes de ResizeBuffers (exige 0 refs al backbuffer).
    backbuffer: Option<ID3D11Resource>,
    shared_res: Option<ID3D11Resource>,
    swapchain: IDXGISwapChain1,
    w: u32,
    h: u32,
    // Ventana principal (para re-detectar HDR del monitor actual en resize/move).
    main_hwnd: HWND,
    // ¿El display actual es HDR-capaz? Decide el FORMATO del swapchain (10-bit vs 8-bit).
    // Se recalcula en resize (la ventana pudo moverse de un monitor SDR al TV HDR).
    display_hdr: Cell<bool>,
    // ¿Estamos emitiendo PQ/bt.2020 ahora mismo? Solo cierto con contenido HDR real en
    // display HDR. El contenido SDR (la mayoría) va como SDR sRGB aunque el display sea
    // HDR — igual que mpv.exe con target-colorspace-hint=yes (Windows aplica el slider).
    pq: Cell<bool>,
    // Control manual OPCIONAL de gamma del usuario (neutro por defecto; el match con mpv
    // ya es automático vía colorspace, esto solo permite gusto personal fino).
    bright: Cell<f32>,
    // Último tier de shaders aplicado (para re-aplicarlo al cambiar de HDR/monitor).
    tier: RefCell<String>,
    // Solo renderizamos (Present) cuando hay vídeo activo: así el motor puede
    // pre-crearse al arrancar (play instantáneo) sin gastar GPU en reposo.
    active: Cell<bool>,
    // Se registran los parámetros de render (colorspace/gamma/etc.) una vez por archivo.
    logged: Cell<bool>,
    // Render gateado por frame nuevo (ver render()). `needs_render` fuerza al menos un
    // dibujado tras load/resize aunque mpv aún no reporte FRAME. Los contadores instrumentan
    // la cadencia (ticks del timer vs presents reales) para el diagnóstico del parpadeo.
    needs_render: Cell<bool>,
    tick_count: Cell<u64>,
    present_count: Cell<u64>,
    last_stat: Cell<Instant>,
}

impl Player {
    /// Crea el motor y engancha su swapchain a `visual_video`. `hwnd` es la ventana
    /// principal (destino del WM_TIMER de render). Requiere el device/context D3D de
    /// la shell (el mismo con el que se creó DComp).
    pub unsafe fn new(
        hwnd: HWND,
        device: &ID3D11Device,
        context: &ID3D11DeviceContext,
        visual_video: &IDCompositionVisual,
        dcomp: &IDCompositionDevice,
        w: u32,
        h: u32,
    ) -> windows::core::Result<Player> {
        let hinstance: HINSTANCE = GetModuleHandleW(None)?.into();

        // Ventana OCULTA que hospeda el contexto GL (pixel format normal; la
        // principal es NOREDIRECTIONBITMAP y no admite pixel format GL).
        let gl_class = windows::core::w!("AniMangaGLHost");
        let gl_wc = WNDCLASSW {
            style: CS_OWNDC,
            lpfnWndProc: Some(gl_host_wndproc),
            hInstance: hinstance,
            lpszClassName: gl_class,
            ..Default::default()
        };
        RegisterClassW(&gl_wc);
        let gl_hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            gl_class,
            windows::core::w!("gl-host"),
            WS_OVERLAPPEDWINDOW,
            0,
            0,
            8,
            8,
            None,
            None,
            hinstance,
            None,
        )?;
        let gl_hdc: HDC = GetDC(gl_hwnd);
        let pfd = PIXELFORMATDESCRIPTOR {
            nSize: size_of::<PIXELFORMATDESCRIPTOR>() as u16,
            nVersion: 1,
            dwFlags: PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER,
            iPixelType: PFD_TYPE_RGBA,
            cColorBits: 32,
            cDepthBits: 24,
            cStencilBits: 8,
            iLayerType: PFD_MAIN_PLANE.0 as u8,
            ..Default::default()
        };
        let fmt = ChoosePixelFormat(gl_hdc, &pfd);
        SetPixelFormat(gl_hdc, fmt, &pfd)?;
        let glrc: HGLRC = wglCreateContext(gl_hdc)?;
        wglMakeCurrent(gl_hdc, glrc)?;
        let gl = Gl::load();

        // FORMATO del swapchain = por capacidad del display. En un display HDR usamos
        // 10-bit (R10G10B10A2 = el A2B10G10R10 que libplacebo elige para AMBOS casos,
        // SDR sRGB y PQ — evita banding). El COLORSPACE (sRGB vs PQ) NO lo decide el
        // display sino el CONTENIDO: se fija por defecto a sRGB aquí y apply_content_
        // colorspace() lo sube a PQ al cargar contenido HDR real. Esto replica exactamente
        // a mpv.exe, que para contenido SDR elige surface SRGB_NONLINEAR aunque haya
        // PQ/HDR10 disponible y target-colorspace-hint=yes (medido en su log).
        let mdesc = monitor_output_desc(device, hwnd);
        let (want_hdr, peak) = desc_hdr(&mdesc);
        let dxfmt = hdr_format(want_hdr);

        // Swapchain de composición (lo que verá DComp en visual_video).
        let dxgi_device: IDXGIDevice = device.cast()?;
        let factory: IDXGIFactory2 = dxgi_device.GetAdapter()?.GetParent()?;
        let sc_desc = DXGI_SWAP_CHAIN_DESC1 {
            Width: w,
            Height: h,
            Format: dxfmt,
            Stereo: false.into(),
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
            BufferCount: 2,
            Scaling: DXGI_SCALING_STRETCH,
            SwapEffect: DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
            AlphaMode: DXGI_ALPHA_MODE_IGNORE,
            Flags: 0,
        };
        let swapchain: IDXGISwapChain1 =
            factory.CreateSwapChainForComposition(device, &sc_desc, None)?;
        // Colorspace por defecto = SDR sRGB (mayoría del anime). Al cargar, render()
        // llama apply_content_colorspace() que sube a PQ solo si el contenido es HDR.
        set_srgb_colorspace(&swapchain);
        let (phys_w, phys_h) = monitor_physical(&mdesc);
        log_line(&format!(
            "[render-res] swapchain={w}x{h}  monitor_fisico={phys_w}x{phys_h}  (si difieren en fullscreen = render por debajo de la nativa -> Anime4K suave)"
        ));
        log_line(&format!(
            "[hdr] display_hdr={want_hdr} peak={} nits fmt={} sdr_white={:.0} (colorspace inicial=SDR sRGB; el contenido decide PQ)",
            peak as u32,
            if want_hdr { "R10G10B10A2" } else { "B8G8R8A8" },
            if want_hdr { sdr_white_nits(hwnd) } else { 0.0 },
        ));

        // Textura intermedia donde pinta mpv (vía GL interop). Misma medida y formato.
        let tex_desc = D3D11_TEXTURE2D_DESC {
            Width: w,
            Height: h,
            MipLevels: 1,
            ArraySize: 1,
            Format: dxfmt,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_SHADER_RESOURCE.0) as u32,
            ..Default::default()
        };
        let mut shared_tex: Option<ID3D11Texture2D> = None;
        device.CreateTexture2D(&tex_desc, None, Some(&mut shared_tex))?;
        let shared_tex = shared_tex.unwrap();

        // Interop GL↔D3D: registrar la textura como textura GL en un FBO.
        let gl_dx_device = (gl.dx_open_device)(device.as_raw());
        assert!(!gl_dx_device.is_invalid(), "wglDXOpenDeviceNV falló");
        let mut gl_tex: u32 = 0;
        (gl.gen_textures)(1, &mut gl_tex);
        let gl_obj = (gl.dx_register_object)(
            gl_dx_device,
            shared_tex.as_raw(),
            gl_tex,
            GL_TEXTURE_2D,
            WGL_ACCESS_WRITE_DISCARD_NV,
        );
        assert!(!gl_obj.is_invalid(), "wglDXRegisterObjectNV falló");
        let mut fbo: u32 = 0;
        (gl.gen_framebuffers)(1, &mut fbo);
        // La textura interop SOLO tiene storage válido con el objeto LOCKED.
        let mut obj0 = gl_obj;
        (gl.dx_lock_objects)(gl_dx_device, 1, &mut obj0);
        (gl.bind_framebuffer)(GL_FRAMEBUFFER, fbo);
        (gl.framebuffer_texture_2d)(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, gl_tex, 0);
        let status = (gl.check_framebuffer_status)(GL_FRAMEBUFFER);
        (gl.dx_unlock_objects)(gl_dx_device, 1, &mut obj0);
        assert_eq!(status, GL_FRAMEBUFFER_COMPLETE, "FBO incompleto: {status:#x}");

        // libmpv por render API OpenGL.
        let mut mpv = Mpv::with_initializer(|init| {
            init.set_property("vo", "libmpv")?;
            init.set_property("hwdec", "auto-safe")?;
            init.set_property("keep-open", "yes")?;
            // Selección automática de pista: SIEMPRE audio japonés + subtítulos español.
            // mpv elige la primera pista cuyo idioma coincida (probando varios códigos ISO
            // y etiquetas comunes de fansubs). Si no hay match, cae a su default.
            let _ = init.set_property("alang", "jpn,ja,japanese,jp");
            let _ = init.set_property("slang", "spa,es,esp,spanish,español,lat,es-419,es-la");
            // Carga de sidecars: NO automática. La app enumera los sidecar (backend
            // _sidecar_subs) y los añade explícitamente por IPC 'subadd' → así el sid que
            // asigna mpv coincide con el índice de la lista del gestor. Si dejáramos
            // sub-auto en 'exact', mpv cargaría el `video.spa.srt` por su cuenta y el sid
            // se desalinearía con la UI (justo el bug de .m2ts).
            let _ = init.set_property("sub-auto", "no");
            // Diagnóstico: log de mpv para ver resolución de render / scalers / hwdec.
            let _ = init.set_property(
                "log-file",
                "C:/Users/Example/AppData/Local/AniMangaStudio/mpv.log",
            );
            let _ = init.set_property("msg-level", "all=v");
            // Calidad del renderer — réplica exacta del mpv.conf del usuario
            // (sin estas opciones mpv escala en bilinear y la imagen se ve
            // suave/lavada aunque los shaders Anime4K corran).
            init.set_property("scale", "ewa_lanczossharp")?;
            init.set_property("dscale", "mitchell")?;
            init.set_property("cscale", "ewa_lanczossoft")?;
            init.set_property("correct-downscaling", "yes")?;
            init.set_property("dither-depth", "auto")?;
            // Réplica del bloque HDR del mpv.conf del usuario. Solo tocan contenido HDR;
            // inofensivas para SDR.
            init.set_property("hdr-compute-peak", "yes")?;
            init.set_property("tone-mapping", "bt.2390")?;
            // target-prim/trc/peak los fija apply_content_colorspace() al cargar, según el
            // CONTENIDO (SDR -> auto/sRGB nativo; HDR -> pq/bt.2020). Arrancar en auto = SDR.
            Ok(())
        })
        .expect("crear mpv");
        let render_ctx = RenderContext::new(
            mpv.ctx.as_mut(),
            vec![
                RenderParam::ApiType(RenderParamApiType::OpenGl),
                RenderParam::InitParams(OpenGLInitParams {
                    get_proc_address: gl_addr,
                    ctx: (),
                }),
            ],
        )
        .expect("crear RenderContext");

        // Colgar el swapchain de la visual de vídeo (debajo del WebView).
        visual_video.SetContent(&swapchain)?;
        dcomp.Commit()?;

        let backbuffer: ID3D11Resource = swapchain.GetBuffer(0)?;
        let shared_res: ID3D11Resource = shared_tex.cast()?;

        // Arrancar el temporizador de render (~120 Hz; Present(1) marca el ritmo).
        SetTimer(hwnd, TIMER_RENDER, 8, None);

        Ok(Player {
            mpv,
            render_ctx,
            gl,
            gl_dx_device,
            gl_obj,
            gl_tex,
            fbo,
            device: device.clone(),
            context: context.clone(),
            backbuffer: Some(backbuffer),
            shared_res: Some(shared_res),
            swapchain,
            w,
            h,
            main_hwnd: hwnd,
            display_hdr: Cell::new(want_hdr),
            pq: Cell::new(false),
            bright: Cell::new(1.0),
            tier: RefCell::new(String::new()),
            active: Cell::new(false),
            logged: Cell::new(false),
            needs_render: Cell::new(false),
            tick_count: Cell::new(0),
            present_count: Cell::new(0),
            last_stat: Cell::new(Instant::now()),
        })
    }

    /// Un frame: mpv → FBO (interop lock) → CopyResource → Present.
    ///
    /// GATEADO POR FRAME NUEVO (fix del parpadeo sutil de luminancia): el timer de render
    /// dispara ~120 Hz, pero SOLO renderizamos cuando mpv reporta un frame NUEVO
    /// (`mpv_render_context_update()` con flag FRAME) o hay un redibujado forzado tras
    /// load/resize. ANTES se llamaba a `render_ctx.render()` en CADA tick sobre frames
    /// REPETIDOS: eso re-ejecutaba todo el pipeline temporal de mpv (incl. detección de
    /// pico) ~5× por frame real, haciendo derivar la luminancia → parpadeo sutil en escenas
    /// oscuras, ausente en mpv.exe (que dibuja 1× por frame). Los contadores instrumentan
    /// la cadencia (ticks del timer vs presents reales), logueados 1×/s.
    pub fn render(&self) {
        if !self.active.get() {
            return; // motor pre-creado pero sin vídeo: no gastar GPU en reposo
        }
        self.tick_count.set(self.tick_count.get() + 1);
        // ¿mpv tiene un frame nuevo listo? (poll del flag; no requiere update-callback)
        let has_frame = matches!(
            self.render_ctx.update(),
            Ok(f) if f & mpv_render_update::Frame != 0
        );
        if !has_frame && !self.needs_render.get() {
            self.log_cadence();
            return; // nada nuevo que dibujar: NO re-renderizar el mismo frame
        }
        // Una vez por archivo, cuando ya hay parámetros de salida: fijar el colorspace
        // según el CONTENIDO (SDR sRGB vs PQ, igual que mpv.exe) y volcar params al log.
        if !self.logged.get() {
            if let Ok(pf) = self.mpv.get_property::<String>("video-out-params/pixelformat") {
                if !pf.is_empty() {
                    self.logged.set(true);
                    unsafe { self.apply_content_colorspace(); }
                    self.log_params();
                }
            }
        }
        let (Some(bb), Some(sr)) = (self.backbuffer.as_ref(), self.shared_res.as_ref()) else {
            return; // en pleno resize (needs_render sigue puesto → se dibuja al reponerse)
        };
        let gl = &self.gl;
        let mut obj = self.gl_obj;
        unsafe {
            (gl.dx_lock_objects)(self.gl_dx_device, 1, &mut obj);
            (gl.bind_framebuffer)(GL_FRAMEBUFFER, self.fbo);
            // flip=false: textura D3D top-down + CopyResource ya invierte en Y.
            let _ = self
                .render_ctx
                .render::<()>(self.fbo as i32, self.w as i32, self.h as i32, false);
            (gl.flush)();
            (gl.dx_unlock_objects)(self.gl_dx_device, 1, &mut obj);
            self.context.CopyResource(bb, sr);
            let _ = self.swapchain.Present(1, DXGI_PRESENT(0));
        }
        self.needs_render.set(false);
        self.present_count.set(self.present_count.get() + 1);
        self.log_cadence();
    }

    /// Vuelca 1×/s la cadencia real: ticks del timer vs presents (frames dibujados). Con el
    /// gateo, presents ≈ fps del vídeo (~24) frente a ticks ~120. Confirma que ya no
    /// re-renderizamos frames repetidos (raíz del parpadeo de brillo).
    fn log_cadence(&self) {
        let now = Instant::now();
        if now.duration_since(self.last_stat.get()).as_millis() >= 1000 {
            log_line(&format!(
                "[cadence] ticks/s={} presents/s={}",
                self.tick_count.get(),
                self.present_count.get()
            ));
            self.tick_count.set(0);
            self.present_count.set(0);
            self.last_stat.set(now);
        }
    }

    /// Redimensiona el swapchain y la textura interop al nuevo tamaño de ventana.
    /// Sin esto, al agrandar/maximizar el vídeo se quedaría al tamaño inicial.
    pub unsafe fn resize(&mut self, w: u32, h: u32) {
        // Re-detectar capacidad HDR del display: la ventana pudo moverse de un monitor SDR
        // al TV HDR (o viceversa), lo que exige recrear el swapchain en el formato correcto
        // (10-bit vs 8-bit).
        let (want_hdr, _peak) = desc_hdr(&monitor_output_desc(&self.device, self.main_hwnd));
        let dxfmt = hdr_format(want_hdr);
        if w == 0 || h == 0 || (w == self.w && h == self.h && want_hdr == self.display_hdr.get()) {
            return;
        }
        let gl = &self.gl;
        // Soltar interop + refs a los buffers (ResizeBuffers exige 0 refs).
        (gl.dx_unregister_object)(self.gl_dx_device, self.gl_obj);
        self.backbuffer = None;
        self.shared_res = None;
        if self
            .swapchain
            .ResizeBuffers(0, w, h, dxfmt, DXGI_SWAP_CHAIN_FLAG(0))
            .is_err()
        {
            return;
        }
        // ResizeBuffers resetea el colorspace del swapchain; hay que re-etiquetarlo.
        // Default sRGB y luego re-aplicar según contenido (sube a PQ si es HDR).
        set_srgb_colorspace(&self.swapchain);
        let was_hdr_display = self.display_hdr.get();
        self.display_hdr.set(want_hdr);
        let (phys_w, phys_h) = monitor_physical(&monitor_output_desc(&self.device, self.main_hwnd));
        log_line(&format!(
            "[resize] {}x{} -> {}x{}  display_hdr {} -> {}  monitor_fisico={phys_w}x{phys_h}",
            self.w, self.h, w, h, was_hdr_display, want_hdr
        ));
        // Nueva textura intermedia al nuevo tamaño y formato.
        let tex_desc = D3D11_TEXTURE2D_DESC {
            Width: w,
            Height: h,
            MipLevels: 1,
            ArraySize: 1,
            Format: dxfmt,
            SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: (D3D11_BIND_RENDER_TARGET.0 | D3D11_BIND_SHADER_RESOURCE.0) as u32,
            ..Default::default()
        };
        let mut shared_tex: Option<ID3D11Texture2D> = None;
        if self
            .device
            .CreateTexture2D(&tex_desc, None, Some(&mut shared_tex))
            .is_err()
        {
            return;
        }
        let shared_tex = shared_tex.unwrap();
        // Re-registrar el interop y re-montar el FBO (dentro de un lock).
        self.gl_obj = (gl.dx_register_object)(
            self.gl_dx_device,
            shared_tex.as_raw(),
            self.gl_tex,
            GL_TEXTURE_2D,
            WGL_ACCESS_WRITE_DISCARD_NV,
        );
        let mut obj0 = self.gl_obj;
        (gl.dx_lock_objects)(self.gl_dx_device, 1, &mut obj0);
        (gl.bind_framebuffer)(GL_FRAMEBUFFER, self.fbo);
        (gl.framebuffer_texture_2d)(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.gl_tex, 0);
        (gl.dx_unlock_objects)(self.gl_dx_device, 1, &mut obj0);
        self.shared_res = shared_tex.cast().ok();
        self.backbuffer = self.swapchain.GetBuffer(0).ok();
        // Re-aplicar el colorspace según el contenido actual (el swapchain se reetiquetó
        // a sRGB arriba; esto lo sube a PQ si el contenido es HDR y el display ahora es
        // HDR). Cubre mover la ventana entre monitor SDR y TV HDR con vídeo cargado.
        self.apply_content_colorspace();
        self.w = w;
        self.h = h;
        self.needs_render.set(true); // redibujar ya al nuevo tamaño (aún sin FRAME nuevo)
    }

    pub fn load(&self, path: &str, start: f64) {
        // Fijar el inicio ANTES de cargar es fiable; un seek justo tras loadfile
        // puede ejecutarse antes de que el archivo cargue y quedar ignorado.
        let s = if start > 1.0 { format!("{start}") } else { "none".to_string() };
        let _ = self.mpv.set_property("start", s.as_str());
        self.logged.set(false); // volver a registrar params para el archivo nuevo
        self.active.set(true); // empezar a renderizar
        self.needs_render.set(true); // garantizar el primer dibujado del archivo nuevo
        if mpv_command_args(&self.mpv, &["loadfile", path, "replace"]) < 0 {
            eprintln!("[player] loadfile falló: {path}");
        }
    }

    pub fn stop(&self) {
        let _ = mpv_command_args(&self.mpv, &["stop"]);
        self.active.set(false); // volver a reposo (sin gasto de GPU)
    }

    pub fn set_pause(&self, paused: bool) {
        let _ = self.mpv.set_property("pause", paused);
    }

    pub fn seek_absolute(&self, secs: f64) {
        let _ = mpv_command_args(&self.mpv, &["seek", &secs.to_string(), "absolute"]);
        self.needs_render.set(true); // en pausa, garantizar el redibujo del frame buscado
    }

    pub fn set_volume(&self, vol: f64) {
        let _ = self.mpv.set_property("volume", vol);
    }

    pub fn set_track(&self, prop: &str, id: &str) {
        // prop = "aid" (audio) o "sid" (subtítulos); id numérico o "no"/"auto".
        let _ = mpv_command_args(&self.mpv, &["set", prop, id]);
        self.needs_render.set(true); // el cambio de pista de subs debe verse ya en pausa
    }

    /// Añade un subtítulo externo (sidecar) como pista mpv, SIN seleccionarlo aún
    /// (`auto`). Se llama una vez por sidecar tras loadfile, en el mismo orden que la
    /// lista del gestor → los sid quedan alineados con la UI. Al ser pistas normales,
    /// heredan sub-delay/sub-scale/estilo como cualquier subtítulo incrustado.
    pub fn sub_add(&self, path: &str) {
        let _ = mpv_command_args(&self.mpv, &["sub-add", path, "auto"]);
    }

    /// Fija una propiedad mpv arbitraria por valor de texto (mpv convierte el tipo):
    /// speed, sub-scale, sub-delay, etc. Un solo comando IPC para varios controles.
    pub fn set_prop(&self, name: &str, val: &str) {
        let _ = self.mpv.set_property(name, val);
        self.needs_render.set(true); // reflejar sub-scale/sub-delay/etc. aunque esté en pausa
    }

    /// Aplica un tier de Anime4K en vivo (una ruta por comando; NUNCA unir con ':'
    /// porque el 'C:' del drive rompe la lista y no carga ningún shader).
    pub fn set_shaders(&self, tier: &str) {
        *self.tier.borrow_mut() = tier.to_string();
        let _ = mpv_command_args(&self.mpv, &["change-list", "glsl-shaders", "clr", ""]);
        for s in tier_shaders(tier) {
            let path = format!("{SHADER_DIR}/{s}");
            let _ = mpv_command_args(&self.mpv, &["change-list", "glsl-shaders", "append", &path]);
        }
        // (Sin shader de ganancia: distorsionaba el color tras Anime4K —rojo/saturado— y
        // metía un pase extra que lagueaba. El match de brillo con mpv es ahora automático
        // vía el colorspace correcto por contenido, ver apply_content_colorspace.)
        self.needs_render.set(true); // aplicar el tier al frame actual aunque esté en pausa
    }

    /// Ajuste de gamma del usuario, por el ecualizador NATIVO de mpv (propiedad `gamma`,
    /// -100..100). Sube/baja los medios tonos para calzar el gamma un pelín superior de
    /// mpv. Gratis (pase principal), sin shader → sin rojo ni lag. mult 1.0 = neutro;
    /// pasos finos (cada 0.05 ≈ 2.5 de gamma).
    pub fn set_bright(&self, mult: f32) {
        let m = mult.clamp(0.5, 3.0);
        self.bright.set(m);
        let gamma = ((m - 1.0) * 50.0).round().clamp(-100.0, 100.0) as i64;
        let _ = self.mpv.set_property("gamma", gamma);
        log_line(&format!("[bright] mult={m:.3} -> gamma={gamma}"));
        self.needs_render.set(true);
    }

    /// Ajuste de saturación (ecualizador NATIVO de mpv `saturation`, -100..100). Sirve
    /// para devolver el "punch" que el gamma le quita a los medios tonos (evita el look
    /// lavado). mult 1.0 = neutro; cada 0.05 ≈ 2.5 de saturación.
    pub fn set_sat(&self, mult: f32) {
        let m = mult.clamp(0.5, 3.0);
        let sat = ((m - 1.0) * 50.0).round().clamp(-100.0, 100.0) as i64;
        let _ = self.mpv.set_property("saturation", sat);
        log_line(&format!("[sat] mult={m:.3} -> saturation={sat}"));
        self.needs_render.set(true);
    }

    /// Replica EXACTAMENTE la decisión de mpv.exe (medida en su log de vo/gpu-next sobre
    /// la TV HDR): el colorspace de salida lo dicta el CONTENIDO, no el display.
    ///
    ///  · Contenido SDR (la mayoría del anime, p.ej. Atelier bt.709/bt.1886): swapchain
    ///    SDR sRGB (G22/P709) + salida SDR NATIVA (target-* = auto). En un display HDR,
    ///    Windows lo compone y le aplica el slider "brillo de contenido SDR" (~240 nits en
    ///    la TV del usuario). ESA es la razón de que mpv se viera más brillante/cálido:
    ///    antes forzábamos PQ con blanco de referencia fijo 203 nits, saltándonos el slider.
    ///    mpv.exe elige el surface SRGB_NONLINEAR para este contenido AUNQUE PQ/HDR10 esté
    ///    disponible y target-colorspace-hint=yes.
    ///  · Contenido HDR real (PQ/HLG): swapchain PQ/bt.2020 (si lo acepta) + target-trc=pq,
    ///    target-prim=bt.2020, metadata HDR10 del TV. Igual que el camino HDR de mpv.exe.
    ///
    /// Devuelve si quedó en PQ. Se llama al cargar (primer frame, en render) y tras resize.
    unsafe fn apply_content_colorspace(&self) -> bool {
        let display_hdr = self.display_hdr.get();
        // ¿El CONTENIDO es HDR? gamma pq/hlg o pico de señal > 1 (SDR = 0/1).
        let src_gamma = self
            .mpv
            .get_property::<String>("video-params/gamma")
            .unwrap_or_default();
        let src_peak = self
            .mpv
            .get_property::<f64>("video-params/sig-peak")
            .unwrap_or(0.0);
        let content_hdr = src_gamma == "pq" || src_gamma == "hlg" || src_peak > 1.01;

        let Ok(sc3) = self.swapchain.cast::<IDXGISwapChain3>() else {
            return false;
        };

        if display_hdr && content_hdr {
            // Camino HDR real: emitir PQ solo si el swapchain de composición lo ACEPTA
            // (CheckColorSpaceSupport + SetColorSpace1 OK); si no, cae a SDR abajo.
            let cs = DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020;
            let supported = matches!(
                sc3.CheckColorSpaceSupport(cs),
                Ok(sup) if sup & DXGI_SWAP_CHAIN_COLOR_SPACE_SUPPORT_FLAG_PRESENT.0 as u32 != 0
            );
            if supported && sc3.SetColorSpace1(cs).is_ok() {
                let _ = self.mpv.set_property("target-prim", "bt.2020");
                let _ = self.mpv.set_property("target-trc", "pq");
                let _ = self.mpv.set_property("target-peak", "auto");
                if let Some(d) = monitor_output_desc(&self.device, self.main_hwnd) {
                    apply_hdr_metadata(&self.swapchain, &d);
                }
                self.pq.set(true);
                log_line(&format!(
                    "[cs] contenido HDR ({src_gamma}) + display HDR -> salida PQ/bt.2020 real"
                ));
                return true;
            }
            log_line("[cs] contenido HDR pero swapchain NO aceptó PQ -> SDR sRGB");
        }

        // Camino SDR (default): swapchain sRGB + salida SDR nativa. En display HDR, Windows
        // sube el blanco al slider "brillo de contenido SDR" == comportamiento de mpv.exe.
        let _ = sc3.SetColorSpace1(DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709);
        let _ = self.mpv.set_property("target-prim", "auto");
        let _ = self.mpv.set_property("target-trc", "auto");
        let _ = self.mpv.set_property("target-peak", "auto");
        self.pq.set(false);
        log_line(&format!(
            "[cs] contenido {} + display_hdr={display_hdr} -> salida SDR sRGB (Windows blanco SDR {:.0} nits)",
            if content_hdr { "HDR-sin-PQ" } else { "SDR" },
            if display_hdr { sdr_white_nits(self.main_hwnd) } else { 0.0 }
        ));
        false
    }

    /// Vuelca TODOS los parámetros de color/render que ve mpv, para compararlos
    /// contra mpv.exe y calzarlos exactamente. Se llama una vez por archivo, cuando
    /// ya hay `video-out-params` (tras decodificar el primer frame).
    fn log_params(&self) {
        let g = |name: &str| -> String {
            self.mpv
                .get_property::<String>(name)
                .unwrap_or_else(|_| "?".into())
        };
        log_line("---- [params] AniManga (vo=libmpv / gpu) ----");
        // Lado FUENTE: cómo viene el vídeo decodificado.
        for k in [
            "pixelformat", "w", "h", "colormatrix", "colorlevels", "primaries",
            "gamma", "sig-peak", "light", "chroma-location",
        ] {
            log_line(&format!("  video-params/{k} = {}", g(&format!("video-params/{k}"))));
        }
        // Lado SALIDA: lo que mpv manda al display tras su conversión de color.
        for k in [
            "pixelformat", "colormatrix", "colorlevels", "primaries", "gamma",
            "sig-peak", "light",
        ] {
            log_line(&format!(
                "  video-out-params/{k} = {}",
                g(&format!("video-out-params/{k}"))
            ));
        }
        // Ajustes de destino/tonemap efectivos (deben igualar el mpv.conf del usuario).
        for k in [
            "target-prim", "target-trc", "target-peak", "target-colorspace-hint",
            "tone-mapping", "tone-mapping-param", "gamut-mapping-mode", "hdr-compute-peak",
            "icc-profile", "icc-profile-auto", "scale", "dscale", "cscale",
            "dither-depth", "gamma", "brightness", "contrast", "saturation", "hue",
            "hwdec-current", "current-vo", "video-target-params/sig-peak",
        ] {
            log_line(&format!("  {k} = {}", g(k)));
        }
        log_line("---- [params] end ----");
    }

    // Estado para reportar a JS (resume/visto). Se leen por polling en el timer de
    // render, evitando montar el event-loop de mpv (bajo riesgo).
    pub fn time_pos(&self) -> Option<f64> {
        self.mpv.get_property("time-pos").ok()
    }
    pub fn duration(&self) -> Option<f64> {
        self.mpv.get_property("duration").ok()
    }
    pub fn paused(&self) -> bool {
        self.mpv.get_property("pause").unwrap_or(false)
    }
}
