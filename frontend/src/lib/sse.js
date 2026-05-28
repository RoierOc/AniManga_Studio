/* Single shared EventSource to /api/status/stream.
   Components subscribe by event type; the connection auto-reconnects. */

let source = null
const listeners = new Map() // type -> Set<fn>

function ensure() {
  if (source) return
  try {
    source = new EventSource('/api/status/stream')
    source.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      const type = data.type
      const set = listeners.get(type)
      if (set) set.forEach(fn => { try { fn(data) } catch (_) {} })
    }
    source.onerror = () => {
      // EventSource reconnects on its own; drop the handle if fully closed.
      if (source && source.readyState === EventSource.CLOSED) {
        source = null
        setTimeout(ensure, 3000)
      }
    }
  } catch (_) { /* SSE unavailable — app still works via manual refresh */ }
}

export function onSSE(type, fn) {
  ensure()
  if (!listeners.has(type)) listeners.set(type, new Set())
  listeners.get(type).add(fn)
  return () => listeners.get(type)?.delete(fn)
}
