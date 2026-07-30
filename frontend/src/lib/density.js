/* Densidad de las rejillas de biblioteca (manga, anime, series/películas).
 *
 * Una biblioteca de 86 series en un 1440p entra a ~5 por fila y no hay forma de cambiarlo: el
 * ancho de tarjeta estaba fijo en el CSS de cada vista. Plex, Steam y Kavita dejan elegir porque
 * "cuánto quiero ver de un vistazo" es preferencia, no diseño.
 *
 * Mecanismo: una variable `--dens` en la raíz que MULTIPLICA el ancho base de cada rejilla
 * (`minmax(calc(14.0625rem * var(--dens)), 1fr)`). Así cada vista conserva su base propia —manga
 * y anime 14,06rem, series 11rem— y el control es uno solo. Nada de tres constantes por vista.
 */
import { ref } from 'vue'

const KEY = 'grid-density'   // sincronizado por lib/prefs.js (es preferencia de PANTALLA, portable)

export const DENSITIES = [
  { id: 'comfy', label: 'Tarjetas grandes', scale: 1.2, bars: 2 },
  { id: 'normal', label: 'Tamaño normal', scale: 1, bars: 3 },
  { id: 'dense', label: 'Más por fila', scale: 0.76, bars: 4 },
]

export const density = ref(read())

function read() {
  let saved = null
  try { saved = localStorage.getItem(KEY) } catch { /* sin almacenamiento */ }
  return DENSITIES.some(d => d.id === saved) ? saved : 'normal'
}

// Releer lo guardado. `hydratePrefs()` baja las preferencias del backend DESPUÉS de que este
// módulo se importe, así que sin esto una máquina nueva ignoraría la densidad sincronizada.
export function reloadDensity() {
  density.value = read()
  applyDensity()
}

export function scaleOf(id) {
  return (DENSITIES.find(d => d.id === id) || DENSITIES[1]).scale
}

// Pintar es escribir la variable; el CSS de las rejillas hace el resto.
export function applyDensity() {
  if (typeof document === 'undefined') return   // vitest corre en node sin DOM
  document.documentElement.style.setProperty('--dens', String(scaleOf(density.value)))
}

export function setDensity(id) {
  if (!DENSITIES.some(d => d.id === id)) return
  density.value = id
  try { localStorage.setItem(KEY, id) } catch { /* sin almacenamiento: se pierde al recargar, no es un fallo */ }
  applyDensity()
}

applyDensity()
