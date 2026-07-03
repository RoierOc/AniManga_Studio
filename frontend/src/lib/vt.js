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

export function vtGo(mutate) {
  const run = async () => { await mutate(); clearMark(); await nextTick() }
  if (!supportsVT || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    run()
    return
  }
  document.startViewTransition(run)
}
