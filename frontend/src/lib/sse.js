/* Single shared EventSource to /api/status/stream.

   The backend pushes ONE aggregated message every 500ms:
     { downloads: {...}, upscale: {...}, exports: {...}, events?: [{seq,type,...}] }

   - Aggregated status (downloads/upscale/exports) → onStatus() subscribers.
   - Each entry in `events[]` (watched/position/download_complete/…) → onSSE(type) subscribers. */

let source = null
// Highest event seq seen so far. null = no event seen yet (first-ever connect
// this page load) — in that case we deliberately do NOT send `since`, so the
// backend skips its 500-event backlog instead of replaying old events (e.g. a
// stale 'watched' from a previous episode, which would re-open the autoplay
// countdown after the user already dismissed it). On a *reconnect* we always
// have a lastSeq, so we resume from exactly there — no gaps, no replays.
let lastSeq = null
const evListeners = new Map()   // event type -> Set<fn>
const statusListeners = new Set()

/* Estado REAL de la conexión, para que el chip del sidebar diga la verdad: antes se pintaba
   "Conectado" siempre, estuviera el backend vivo o muerto, y era el único indicador de salud
   de la app. 'connecting' | 'live' | 'down'. */
import { ref } from 'vue'
export const sseState = ref('connecting')
// Con el SSE en silencio (sólo emite cuando algo cambia) un mensaje puede tardar; el latido
// del backend llega cada 15 s, así que damos margen antes de declarar la conexión caída.
let _hbTimer = null
function _alive() {
  sseState.value = 'live'
  clearTimeout(_hbTimer)
  _hbTimer = setTimeout(() => { if (sseState.value === 'live') sseState.value = 'down' }, 45000)
}

function ensure() {
  if (source) return
  try {
    const url = lastSeq != null ? `/api/status/stream?since=${lastSeq}` : '/api/status/stream'
    source = new EventSource(url)
    source.onopen = _alive
    source.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      if (!data || typeof data !== 'object' || Array.isArray(data)) return
      if (data._error) {
        clearTimeout(_hbTimer)
        sseState.value = 'down'
        return
      }
      const hasStatus = ['downloads', 'upscale', 'exports', 'transplant', 'subtitles', 'subtitle_batches', 'anime_upscale']
        .some(key => Object.prototype.hasOwnProperty.call(data, key))
      if (!hasStatus && !Array.isArray(data.events) && data.hb !== 1) return
      _alive()

      // Aggregated status snapshot
      if (data.downloads || data.upscale || data.exports || data.transplant || data.subtitles || data.subtitle_batches || data.anime_upscale) {
        statusListeners.forEach(fn => { try { fn(data) } catch (_) {} })
      }
      // Event bus
      if (Array.isArray(data.events)) {
        for (const ev of data.events) {
          if (ev.seq > lastSeq) lastSeq = ev.seq
          const set = evListeners.get(ev.type)
          if (set) set.forEach(fn => { try { fn(ev) } catch (_) {} })
        }
      }
    }
    source.onerror = () => {
      clearTimeout(_hbTimer)
      sseState.value = 'down'
      if (source && source.readyState === EventSource.CLOSED) {
        source = null
        sseState.value = 'connecting'
        setTimeout(ensure, 3000)
      }
    }
  } catch (_) { sseState.value = 'down' /* SSE unavailable — app still works via manual refresh */ }
}

export function onSSE(type, fn) {
  ensure()
  if (!evListeners.has(type)) evListeners.set(type, new Set())
  evListeners.get(type).add(fn)
  return () => evListeners.get(type)?.delete(fn)
}

export function onStatus(fn) {
  ensure()
  statusListeners.add(fn)
  return () => statusListeners.delete(fn)
}

/* Fuerza una reconexión inmediata desde una vista que detectó `down`. El EventSource nativo ya
 * reintenta por su cuenta, pero el usuario no debería tener que esperar su backoff para volver a
 * ver Actividad después de arrancar el servidor. `lastSeq` se conserva para no perder eventos. */
export function retrySse() {
  clearTimeout(_hbTimer)
  if (source) source.close()
  source = null
  sseState.value = 'connecting'
  ensure()
}
