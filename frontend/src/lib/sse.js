/* Single shared EventSource to /api/status/stream.

   The backend pushes ONE aggregated message every 500ms:
     { downloads: {...}, upscale: {...}, exports: {...}, events?: [{seq,type,...}] }

   - Aggregated status (downloads/upscale/exports) → onStatus() subscribers.
   - Each entry in `events[]` (watched/position/download_complete/…) → onSSE(type) subscribers. */

let source = null
const evListeners = new Map()   // event type -> Set<fn>
const statusListeners = new Set()

function ensure() {
  if (source) return
  try {
    source = new EventSource('/api/status/stream')
    source.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }

      // Aggregated status snapshot
      if (data.downloads || data.upscale || data.exports) {
        statusListeners.forEach(fn => { try { fn(data) } catch (_) {} })
      }
      // Event bus
      if (Array.isArray(data.events)) {
        for (const ev of data.events) {
          const set = evListeners.get(ev.type)
          if (set) set.forEach(fn => { try { fn(ev) } catch (_) {} })
        }
      }
    }
    source.onerror = () => {
      if (source && source.readyState === EventSource.CLOSED) {
        source = null
        setTimeout(ensure, 3000)
      }
    }
  } catch (_) { /* SSE unavailable — app still works via manual refresh */ }
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
