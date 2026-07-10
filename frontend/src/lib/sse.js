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

function ensure() {
  if (source) return
  try {
    const url = lastSeq != null ? `/api/status/stream?since=${lastSeq}` : '/api/status/stream'
    source = new EventSource(url)
    source.onmessage = (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }

      // Aggregated status snapshot
      if (data.downloads || data.upscale || data.exports || data.transplant || data.subtitles) {
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
