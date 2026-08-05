/* View Transitions (grid → detalle y cambios de vista).
 *
 * vtTag(el, selector) marca la imagen de la card clickeada con
 * view-transition-name:'detail-poster' — el póster "vuela" hasta el hero del
 * detalle (que declara el mismo nombre en su CSS). vtGo(mutate) envuelve el
 * cambio de estado: captura el antes, aplica el cambio, limpia la marca (dos
 * elementos con el mismo nombre abortan el grupo) y anima al después.
 *
 * OJO: dentro del callback de startViewTransition el render está pausado —
 * nunca esperar requestAnimationFrame ahí (deadlock). Solo nextTick. */
import { nextTick } from 'vue'

export const supportsVT = typeof document !== 'undefined' && !!document.startViewTransition

let marked = null

/* Qué tarjeta fue la última de la que se salió, para poder señalarla al volver.
 *
 * Al retroceder, la posición del scroll ya se restauraba bien, pero aterrizabas en una rejilla
 * de decenas de pósters iguales sin ninguna pista de cuál acababas de mirar. Es una lectura
 * reactiva (un `ref`), no un `let`, para que las vistas puedan pintarla sin sondear. */
import { ref } from 'vue'
export const ultimaTarjeta = ref('')

/** La marca dura poco a propósito: es un guiño al volver, no un estado seleccionado. */
export function marcarTarjeta(id) {
  ultimaTarjeta.value = String(id ?? '')
}

export function vtTag(root, selector = 'img') {
  if (!supportsVT || !root) return
  const el = root.querySelector?.(selector) || root
  try {
    el.style.viewTransitionName = 'detail-poster'
    marked = el
  } catch (_) {}
}

function clearMark() {
  if (!marked) return
  try { marked.style.viewTransitionName = '' } catch (_) {}
  marked = null
}

/* Devuelve una promesa que se resuelve cuando la MUTACIÓN ya se ha aplicado (no cuando acaba la
   animación). Importa: `startViewTransition` NO ejecuta el callback en el acto, así que quien
   llame y siga leyendo el estado justo después lo leería viejo. Pasó de verdad — al envolver
   `manga.open()`, el `await api.get(.../${this.current.id})` de la línea siguiente corría con el
   `current` ANTERIOR (o `null` en la primera apertura), reventaba dentro del `try` y el modal
   decía «No se pudieron cargar los capítulos» sin haber pedido nada. */
export function vtGo(mutate) {
  const run = async () => { await mutate(); clearMark(); await nextTick() }
  if (!supportsVT || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    return run()
  }
  // Si otra navegación interrumpe la transición, `.finished`/`.ready` se rechazan con AbortError:
  // sin este catch sale como error no manejado en consola (y no significa nada).
  const t = document.startViewTransition(run)
  t.ready?.catch(() => {})
  t.finished?.catch(() => {})
  // `updateCallbackDone` = el DOM ya está mutado; la animación sigue después, sin bloquear.
  return t.updateCallbackDone?.catch(() => {}) ?? Promise.resolve()
}
