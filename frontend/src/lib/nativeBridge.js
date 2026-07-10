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
