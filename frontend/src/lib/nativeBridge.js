// Puente IPC con la shell nativa de Windows (desktop/native — Fase 2).
//
// Cuando la SPA corre DENTRO de la shell nativa, `window.chrome.webview` existe:
//   · postMessage(obj)  → llega a Rust (add_WebMessageReceived).
//   · Rust responde con PostWebMessageAsJson → evento 'message' aquí.
// En un navegador normal `isNative()` es false y `send()` es no-op, así que el
// mismo código Vue sirve para el player web y para el nativo (reversibilidad).
//
// Protocolo (contrato con desktop/native/src/main.rs::handle_ipc):
//   JS → Rust : { cmd: '<verbo>', ...payload }
//     cmd: 'ping'                      → diagnóstico (Rust responde {event:'pong'})
//     cmd: 'loadfile'  { path }        → abrir episodio (crea el motor perezosamente)
//     cmd: 'stop'                      → descargar el archivo (al cerrar)
//     cmd: 'pause'     { value:bool }  → pausar/reanudar
//     cmd: 'seek'      { pos:number }  → buscar (segundos absolutos)
//     cmd: 'shaders'   { tier:string } → tier Anime4K en vivo ('off' | 'high')
//     cmd: 'track'     { aid?, sid? }  → pista audio/subs (id numérico | 'no' | 'auto')
//     cmd: 'subadd'    { path }        → añade un sidecar (.srt/.ass) como pista mpv
//     cmd: 'volume'    { value:0..100 }→ volumen
//     cmd: 'window'    { action }      → controles de la barra de título propia:
//        'minimize' | 'toggleMaximize' | 'close' | 'drag' (inicia el arrastre)
//   Rust → JS : { event: '<nombre>', ... }
//     event: 'pong'
//     event: 'time'   { pos, duration, paused }   → ~1×/s mientras hay vídeo
//     event: 'windowState' { maximized:bool }     → al maximizar/restaurar la ventana
//     event: 'navigate' { dir:'back'|'forward' }  → botones laterales del ratón
//        (en composición no llegan al DOM; Rust los traduce a historial)

const wv = typeof window !== 'undefined' ? window.chrome?.webview : undefined
const listeners = new Set()

if (wv) {
  wv.addEventListener('message', (e) => {
    // WebView2 ya deserializa el JSON de PostWebMessageAsJson en e.data.
    const data = e.data
    for (const fn of listeners) {
      try {
        fn(data)
      } catch (err) {
        console.error('[nativeBridge] listener falló', err)
      }
    }
  })
}

/** ¿Estamos hospedados en la shell nativa (WebView2)? */
export function isNative() {
  return !!wv
}

/** Manda un comando al motor nativo. Devuelve false si no hay shell nativa. */
export function send(cmd, payload = {}) {
  if (!wv) return false
  wv.postMessage({ cmd, ...payload })
  return true
}

/** Ajusta una propiedad de mpv en vivo (IPC 'setprop'). Para diagnóstico/A-B. */
export function setProp(name, value) {
  return send('setprop', { name, value: String(value) })
}

// ── Diagnóstico del parpadeo de brillo (root-cause por aislamiento en vivo) ──
// Permite A/B durante el OP de Atelier SIN recompilar: cada llamada cambia una
// propiedad de mpv y se observa si el parpadeo de luminancia desaparece. El
// principal sospechoso es hdr-compute-peak (detección dinámica de pico → "pumping"
// de brillo, colores intactos, peor en escenas oscuras). Uso desde la consola de
// devtools del WebView:  __mpvDiag.noPeak()  /  __mpvDiag.peak()  /  __mpvDiag.set('dither-depth','no')
if (typeof window !== 'undefined' && wv) {
  window.__mpvDiag = {
    set: (name, value) => { console.log('[mpvDiag] set', name, '=', value); return setProp(name, value) },
    // Sospechoso #1: pico dinámico HDR. Apagarlo fija el pico y elimina el pumping.
    noPeak: () => window.__mpvDiag.set('hdr-compute-peak', 'no'),
    peak:   () => window.__mpvDiag.set('hdr-compute-peak', 'yes'),
    // Sospechoso #2: tone-mapping activo. 'clip' = sin curva → descarta el tonemap.
    noTonemap: () => window.__mpvDiag.set('tone-mapping', 'clip'),
    tonemap:   () => window.__mpvDiag.set('tone-mapping', 'bt.2390'),
    // Sospechoso #3: dither temporal en el swapchain de 8-bit.
    noDither: () => window.__mpvDiag.set('dither-depth', 'no'),
    dither:   () => window.__mpvDiag.set('dither-depth', 'auto'),
    // Sospechoso #4: deband (grano aleatorio por frame en zonas planas oscuras).
    noDeband: () => window.__mpvDiag.set('deband', 'no'),
  }

  // ── A/B de shaders Anime4K (el parpadeo aparece SOLO con shaders) ──
  // En Windows el separador de listas de mpv es ';', así que los drive-letter (C:) no rompen.
  const _SH = 'C:/Program Files (x86)/mpv/mpv/shaders'
  const _list = (...names) => names.map((n) => `${_SH}/${n}.glsl`).join(';')
  window.__mpvDiag.shadersOff = () => window.__mpvDiag.set('glsl-shaders', '')
  // Preset Mode A (HQ) EXACTO del mpv.conf del usuario: UN solo restore (VL). Limpio en mpv.exe.
  window.__mpvDiag.modeA = () => window.__mpvDiag.set('glsl-shaders', _list(
    'Anime4K_Clamp_Highlights', 'Anime4K_Restore_CNN_VL', 'Anime4K_Upscale_CNN_x2_VL',
    'Anime4K_AutoDownscalePre_x2', 'Anime4K_AutoDownscalePre_x4', 'Anime4K_Upscale_CNN_x2_M'))
  // Nuestro tier "high" actual: DOBLE restore (UL + M) — sospechoso del shimmer.
  window.__mpvDiag.ourHigh = () => window.__mpvDiag.set('glsl-shaders', _list(
    'Anime4K_Clamp_Highlights', 'Anime4K_Restore_CNN_UL', 'Anime4K_Upscale_CNN_x2_UL',
    'Anime4K_AutoDownscalePre_x2', 'Anime4K_AutoDownscalePre_x4',
    'Anime4K_Restore_CNN_M', 'Anime4K_Upscale_CNN_x2_M'))
}

/** Suscríbete a los mensajes de Rust. Devuelve una función para desuscribirse. */
export function onMessage(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** Diagnóstico de canal: send('ping') → Rust responde {event:'pong'}. */
export function ping() {
  return send('ping')
}

/**
 * Control de la ventana desde la barra de título propia (shell nativa).
 * action: 'minimize' | 'toggleMaximize' | 'close' | 'drag'.
 * En navegador es no-op (la barra propia solo se muestra si isNative()).
 */
export function windowControl(action) {
  return send('window', { action })
}
