/* Formatting helpers — identical behaviour to the original app. */

export function formatBytes(b) {
  if (!b) return '0 B'
  if (b < 1024) return b + ' B'
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'
  return (b / 1073741824).toFixed(2) + ' GB'
}

export function formatSpeed(bps) {
  if (!bps) return '0 KB/s'
  if (bps < 1048576) return Math.round(bps / 1024) + ' KB/s'
  return (bps / 1048576).toFixed(1) + ' MB/s'
}

export function formatEta(secs) {
  if (!secs || secs >= 8640000) return '∞'
  if (secs < 60) return secs + 's'
  if (secs < 3600) return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's'
  return Math.floor(secs / 3600) + 'h ' + Math.floor((secs % 3600) / 60) + 'm'
}

const QBT_STATE = {
  downloading: 'Descargando', uploading: 'Subiendo',
  stalledDL: 'Sin seeds', stalledUP: 'Pausado',
  pausedDL: 'Pausado', pausedUP: 'Completado/Pausado',
  checkingDL: 'Verificando', checkingUP: 'Verificando',
  queuedDL: 'En cola', queuedUP: 'En cola',
  error: 'Error', missingFiles: 'Archivos faltantes',
  forcedDL: 'Descargando', forcedUP: 'Subiendo',
}
export const qbtStateLabel = (s) => QBT_STATE[s] || s

export function relativeTime(ts) {
  if (!ts) return ''
  const diff = Math.floor(Date.now() / 1000) - ts
  if (diff < 60) return 'hace un momento'
  if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`
  if (diff < 604800) return `hace ${Math.floor(diff / 86400)} d`
  return new Date(ts * 1000).toLocaleDateString('es')
}
