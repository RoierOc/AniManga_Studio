/* Tooltips propios — la última pieza de chrome del sistema operativo que quedaba en la app.
 *
 * Había 300 `title="…"` repartidos por la interfaz. El tooltip nativo tarda ~1 s en salir, se
 * pinta con la fuente y el marco del SO (ignora el tema), no se puede colocar, y con el teclado
 * no aparece NUNCA. Es la misma historia que los 22 `<select>` nativos (→ `ui/Select.vue`) y el
 * `confirm()` del navegador (→ `ConfirmDialog`).
 *
 * Mecanismo: UNA burbuja y UN juego de escuchas delegadas en `document`, no una directiva por
 * componente ni un nodo por botón. Así funciona igual en markup que aún no existe (rejillas que
 * se repintan, modales que se montan) y no hay nada que importar en cada vista: basta con que el
 * elemento lleve `data-tip`.
 *
 * Accesibilidad: al quitar `title` se perdería el NOMBRE accesible de los botones que sólo tienen
 * un icono. Un observador copia `data-tip` a `aria-label` en cuanto aparecen en el DOM, y sólo
 * cuando el elemento no tiene ya texto ni etiqueta propia (si lo tiene, `aria-label` la pisaría).
 * Y la burbuja también sale con el foco del teclado, cosa que el tooltip nativo no hace.
 */

const DELAY = 380          // lo que tardas en "posarte" de verdad sobre algo
const WARM = 320           // recién visto otro: sale al instante, como en un menú
const GAP = 8

let el = null              // la burbuja (una sola, perezosa)
let showTimer = 0
let lastHide = 0
let current = null

function bubble() {
  if (el?.isConnected) return el
  if (el) { document.body.appendChild(el); return el }   // alguien vació <body>: se recoloca
  el = document.createElement('div')
  el.className = 'tipbubble'
  el.setAttribute('role', 'tooltip')
  el.setAttribute('aria-hidden', 'true')
  document.body.appendChild(el)
  return el
}

function place(target) {
  const b = bubble()
  const r = target.getBoundingClientRect()
  const tb = b.getBoundingClientRect()
  // Debajo por defecto; arriba si no cabe (nunca fuera de la ventana).
  const below = r.bottom + GAP + tb.height <= window.innerHeight
  const top = below ? r.bottom + GAP : r.top - GAP - tb.height
  const left = Math.max(GAP, Math.min(
    window.innerWidth - tb.width - GAP,
    r.left + r.width / 2 - tb.width / 2,
  ))
  b.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`
  b.dataset.side = below ? 'below' : 'above'
}

function show(target, text) {
  current = target
  const b = bubble()
  b.textContent = text
  b.classList.add('is-on')
  b.setAttribute('aria-hidden', 'false')
  b.style.transform = 'translate(-9999px, -9999px)'   // medir antes de colocar
  place(target)
}

function hide() {
  clearTimeout(showTimer)
  showTimer = 0
  if (!current) return
  current = null
  lastHide = performance.now()
  if (el) { el.classList.remove('is-on'); el.setAttribute('aria-hidden', 'true') }
}

function tipOf(node) {
  const t = node?.closest?.('[data-tip]')
  const text = t?.getAttribute('data-tip')?.trim()
  return text ? [t, text] : []
}

/* Un control DESHABILITADO no despacha eventos de ratón en Chrome, y son justo los que más
 * necesitan explicarse ("¿por qué no puedo pulsar esto?"). El hit-test sí los ve, así que
 * cuando el evento llega al padre se pregunta qué hay realmente bajo el cursor. */
function tipUnderCursor(e) {
  const [t, text] = tipOf(e.target)
  if (t) return [t, text]
  return tipOf(document.elementFromPoint(e.clientX, e.clientY))
}

/* Se escucha `mousemove` y no `mouseover` por lo mismo: entrar en el hijo deshabilitado no
 * genera un `mouseover` nuevo (el objetivo sigue siendo el padre). Se limita a una comprobación
 * por fotograma, que es exactamente lo que hace el navegador para su propio tooltip. */
let pending = null
let rafId = 0
function onMove(e) {
  pending = e
  if (!rafId) rafId = requestAnimationFrame(() => { rafId = 0; const ev = pending; pending = null; if (ev) onOver(ev) })
}

function onOver(e) {
  const [target, text] = tipUnderCursor(e)
  if (!target) { if (current) hide(); return }
  if (target === current) return
  hide()
  const warm = performance.now() - lastHide < WARM
  clearTimeout(showTimer)
  if (warm) show(target, text)
  else showTimer = setTimeout(() => show(target, text), DELAY)
}

function onFocus(e) {
  const [target, text] = tipOf(e.target)
  if (!target) { hide(); return }
  hide()
  show(target, text)          // con teclado no se "posa": si llegaste ahí, lo quieres ya
}

/* El nombre accesible: un botón que sólo tiene un icono se queda mudo sin `title`. */
function nameIcons(root) {
  const nodes = [...(root.querySelectorAll?.('[data-tip]:not([aria-label])') || [])]
  if (root.matches?.('[data-tip]:not([aria-label])')) nodes.push(root)   // el propio nodo añadido
  for (const n of nodes) {
    if (!n.textContent.trim()) n.setAttribute('aria-label', n.getAttribute('data-tip'))
  }
}

export function installTooltips() {
  if (typeof document === 'undefined') return    // vitest corre en node sin DOM
  document.addEventListener('mousemove', onMove, { passive: true })
  document.addEventListener('mouseleave', hide, { passive: true })
  document.addEventListener('focusin', onFocus, { passive: true })
  document.addEventListener('focusout', hide, { passive: true })
  // Cualquier cosa que mueva o tape el objetivo invalida la burbuja: no dejarla flotando.
  document.addEventListener('mousedown', hide, { passive: true, capture: true })
  document.addEventListener('keydown', hide, { passive: true, capture: true })
  window.addEventListener('scroll', hide, { passive: true, capture: true })
  window.addEventListener('blur', hide, { passive: true })

  nameIcons(document)
  new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) if (n.nodeType === 1) { nameIcons(n); }
      if (m.type === 'attributes' && m.target.nodeType === 1) nameIcons(m.target.parentNode || document)
    }
  }).observe(document.body, { childList: true, subtree: true, attributeFilter: ['data-tip'] })
}
