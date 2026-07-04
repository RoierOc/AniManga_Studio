// Portable UI preferences bridge — mirrors a whitelist of localStorage keys to the
// backend (/api/config/prefs) so "Recuperar biblioteca" brings them back on a fresh
// machine. Non-invasive: it does NOT wrap every setItem call. It hydrates once on
// startup, then pushes a snapshot whenever the whitelisted keys change.
//
// Only genuine DISPLAY preferences are portable. Excluded on purpose:
//  - GPU/machine-specific (anime-a4k*, volumes) — differ per device
//  - data, not prefs (manga-progress-v1, act-hidden) — progress rides reading_history
//  - keys with their own backend toggle (upscale-eco, tp-qa)
import { api } from '@/lib/api'

export const PORTABLE_PREFS = [
  'lib-sort', 'anime-libsort', 'anime-epview',
  'reader-spread', 'reader-mode', 'reader-fit', 'reader-dir',
  'anime-player-mode',
]

let _snapshot = '{}'

function _current() {
  const o = {}
  for (const k of PORTABLE_PREFS) {
    const v = localStorage.getItem(k)
    if (v != null) o[k] = v
  }
  return o
}

// Pull backend prefs into localStorage BEFORE the app mounts, so components that
// read localStorage at setup get the synced values. Baseline the snapshot so we
// don't immediately echo them back.
export async function hydratePrefs() {
  try {
    const remote = await api.get('/api/config/prefs')
    if (remote && typeof remote === 'object') {
      for (const k of PORTABLE_PREFS) {
        if (remote[k] != null) localStorage.setItem(k, String(remote[k]))
      }
    }
  } catch (_) { /* offline / first run — keep local values */ }
  _snapshot = JSON.stringify(_current())
}

function _pushIfChanged() {
  const cur = JSON.stringify(_current())
  if (cur === _snapshot) return
  _snapshot = cur
  api.post('/api/config/prefs', JSON.parse(cur)).catch(() => {})
}

// Watch for changes without touching call sites: light poll + flush on tab hide/unload.
export function startPrefSync() {
  setInterval(_pushIfChanged, 4000)
  window.addEventListener('visibilitychange', () => { if (document.hidden) _pushIfChanged() })
  window.addEventListener('beforeunload', _pushIfChanged)
}
