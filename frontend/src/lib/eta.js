/* Cuánto falta, estimado del avance OBSERVADO.
 *
 * Deliberadamente no se pide al backend: cada tipo de tarea mediría el suyo (líneas, páginas,
 * bytes, tiles) y habría que añadir contabilidad de tiempo en cuatro sitios. Aquí basta el par
 * (instante, %) que ya llega por SSE, y sirve igual para descargas, escalados y traducción.
 *
 * La velocidad se suaviza con una media móvil exponencial porque el avance real va a tirones —
 * un lote de traducción salta 8 puntos de golpe y luego no se mueve en 20 s; con la velocidad
 * instantánea el «faltan X» bailaría entre 10 s y 4 min y sería peor que no poner nada.
 */

const ALFA = 0.25            // peso de la última medida en la media móvil
const MIN_AVANCE = 1.5       // % mínimo acumulado antes de arriesgar una estimación
const MIN_MS = 4000          // y al menos este tiempo observado
const CADUCA_MS = 120000     // sin noticias de una tarea, se olvida (evita fugas)

const vistas = new Map()     // id → { t, pct, vel, t0, pct0 }

/** Registra el avance de una tarea y devuelve los SEGUNDOS que faltan, o null si aún no se sabe. */
export function restante(id, pct, activa = true) {
  const ahora = Date.now()
  if (!activa || pct >= 100) { vistas.delete(id); return null }
  // Ojo: 0 % NO es motivo para descartar la tarea. Toda tarea empieza ahí, y si no se anota el
  // instante inicial no hay contra qué medir después (el primer intento se olvidaba de la mitad
  // de las tareas justo cuando el usuario abre el panel).
  if (pct < 0) { vistas.delete(id); return null }

  const v = vistas.get(id)
  if (!v) { vistas.set(id, { t: ahora, pct, vel: 0, t0: ahora, pct0: pct }); return null }

  const dt = ahora - v.t
  if (dt < 500) return estimar(v, pct)          // no re-medir en cada repintado

  if (pct > v.pct) {
    const inst = (pct - v.pct) / dt             // % por milisegundo
    v.vel = v.vel ? v.vel * (1 - ALFA) + inst * ALFA : inst
  }
  // Si no ha avanzado nada, la velocidad NO se toca: bajarla a cero haría saltar la estimación
  // a infinito en cada pausa entre lotes, que es justo cuando el usuario está mirando.
  v.t = ahora
  v.pct = pct
  purgar(ahora)
  return estimar(v, pct)
}

function estimar(v, pct) {
  if (!v.vel) return null
  if (pct - v.pct0 < MIN_AVANCE || Date.now() - v.t0 < MIN_MS) return null
  const ms = (100 - pct) / v.vel
  if (!isFinite(ms) || ms <= 0 || ms > 6 * 3600e3) return null   // > 6 h: no es una estimación útil
  return Math.round(ms / 1000)
}

function purgar(ahora) {
  if (vistas.size < 60) return
  for (const [k, v] of vistas) if (ahora - v.t > CADUCA_MS) vistas.delete(k)
}

/** Segundos → texto corto en español. 95 → «~2 min», 3700 → «~1 h 2 min». */
export function etaTexto(seg) {
  if (seg == null) return ''
  if (seg < 45) return '~1 min'                 // «~12 s» invita a mirar el reloj; no aporta
  const min = Math.round(seg / 60)
  if (min < 60) return `~${min} min`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `~${h} h ${m} min` : `~${h} h`
}

/** ETA de una tarea normalizada del store. Vive aquí para que las TRES superficies de progreso
 *  (Centro de Actividad, cajón de la TopBar, modal de lotes) no la reimplementen: el cajón la
 *  usaba en su plantilla sin haberla definido nunca → «e.eta is not a function» y la vista entera
 *  caía al ErrorState. */
export function etaTarea(t) {
  return etaTexto(restante(t.id, t.pct, t.status === 'running' || t.status === 'queued'))
}

export function olvidar(id) { vistas.delete(id) }
