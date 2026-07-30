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

/* qBittorrent v5 renombró `paused*` a `stopped*` y aquí sólo estaban los nombres de la v4, así
   que el 95 % de la cola pintaba el estado CRUDO en inglés (`stoppedUP`). Y `stalledUP` no es
   «pausado»: es sembrando sin nadie al otro lado. */
const QBT_STATE = {
  downloading: 'Descargando', forcedDL: 'Descargando',
  uploading: 'Sembrando', forcedUP: 'Sembrando', stalledUP: 'Sembrando',
  stalledDL: 'Sin seeds',
  pausedDL: 'Pausado', stoppedDL: 'Pausado',
  pausedUP: 'Terminado', stoppedUP: 'Terminado',
  checkingDL: 'Verificando', checkingUP: 'Verificando', checkingResumeData: 'Verificando',
  queuedDL: 'En cola', queuedUP: 'En cola',
  moving: 'Moviendo', metaDL: 'Buscando metadatos',
  error: 'Error', missingFiles: 'Archivos faltantes',
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

/* Estados de serie/película tal cual los devuelven Sonarr, Radarr y TMDB. Se colaban CRUDOS en
   la UI: barrido de las 9 vistas → sólo en Cine, pero ahí «ended» x4, «continuing» y «announced»,
   y encima duplicando lo que la línea ya decía en español («Completa · 2008 · ended»). */
const MEDIA_STATE = {
  continuing: 'En emisión', ended: 'Terminada', upcoming: 'Próximamente', deleted: 'Eliminada',
  announced: 'Anunciada', incinemas: 'En cines', released: 'Estrenada',
  'returning series': 'En emisión', canceled: 'Cancelada', cancelled: 'Cancelada',
  'in production': 'En producción', 'post production': 'En posproducción', planned: 'Planeada',
  pilot: 'Piloto',
}
export const mediaStatusLabel = (s) => {
  const k = String(s || '').trim().toLowerCase()
  return k ? (MEDIA_STATE[k] || s) : ''
}
