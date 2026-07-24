/* Comportamiento común de un diálogo modal: Escape cierra, el foco no se escapa por detrás y
 * la página de debajo no scrollea.
 *
 * Sin esto, de siete modales sólo UNO cerraba con Escape, y en ninguno había trampa de foco: con
 * Tab te ibas caminando por los botones de la vista que está detrás del scrim, invisible pero
 * enfocable. Se arregla una vez, aquí.
 *
 *   const el = ref(null)
 *   useModal(() => !!store.current, () => store.close(), el)   // <div ref="el" …>
 */
import { isRef, nextTick, onBeforeUnmount, watch } from 'vue'

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])', 'select:not([disabled])',
  'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

// Pila de modales: con dos abiertos (p.ej. la ficha + el diálogo de confirmar) Escape debe cerrar
// SOLO el de encima, y el scroll del fondo no debe soltarse hasta que se cierre el último.
const stack = []

function focusables(root) {
  if (!root) return []
  return [...root.querySelectorAll(FOCUSABLE)].filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  )
}

/* ¿Está abierto el modal? Vive aparte y exportada porque AQUÍ estuvo un fallo que dejó la app
 * ENTERA sin scroll: se pasó `() => !!d` con `d` siendo un `computed`. Un ref es SIEMPRE truthy,
 * así que cinco modales se creyeron abiertos desde el arranque y bloquearon el scroll para
 * siempre — mudo y desconcertante, porque ningún modal se veía.
 *
 * Ahora se desenvuelve el ref y se avisa por consola en vez de romper. Probada en usemodal.spec.js.
 */
export function resolveOpen(isOpen) {
  const v = typeof isOpen === 'function' ? isOpen() : isOpen
  if (isRef(v)) {
    console.warn('[useModal] la condición devuelve un ref: usa `.value` (un ref siempre es truthy)')
    return !!v.value
  }
  return !!v
}

export function useModal(isOpen, close, elRef, opts = {}) {
  const { closeOnEsc = true, trapFocus = true, lockScroll = true } = opts
  let restoreTo = null

  const open_ = () => resolveOpen(isOpen)

  function onKey(e) {
    // Sólo actúa el modal que está ARRIBA de la pila.
    if (stack[stack.length - 1] !== entry) return
    if (e.key === 'Escape' && closeOnEsc) {
      e.preventDefault()
      e.stopPropagation()
      close()
      return
    }
    if (e.key !== 'Tab' || !trapFocus) return
    const items = focusables(elRef?.value)
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    // Si el foco se ha ido fuera (o aún no ha entrado), tráelo de vuelta al borde correcto.
    if (!elRef.value.contains(document.activeElement)) {
      e.preventDefault()
      ;(e.shiftKey ? last : first).focus()
      return
    }
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  }

  const entry = { onKey }

  async function open() {
    if (stack.includes(entry)) return
    restoreTo = document.activeElement
    stack.push(entry)
    if (lockScroll && stack.length === 1) document.documentElement.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey, true)   // captura: gana a los atajos de la vista
    await nextTick()
    // El primer foco va al contenedor, no al primer botón: enfocar "Eliminar" nada más abrir
    // invita a un Enter accidental.
    const el = elRef?.value
    if (el && !el.contains(document.activeElement)) {
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1')
      el.focus({ preventScroll: true })
    }
  }

  function teardown() {
    const i = stack.indexOf(entry)
    if (i < 0) return
    stack.splice(i, 1)
    window.removeEventListener('keydown', onKey, true)
    if (lockScroll && !stack.length) document.documentElement.style.overflow = ''
    // Devuelve el foco a lo que lo tenía antes: sin esto, al cerrar te quedas al principio del
    // documento y el siguiente Tab empieza desde cero.
    try { restoreTo?.focus?.({ preventScroll: true }) } catch { /* el elemento pudo desaparecer */ }
    restoreTo = null
  }

  watch(open_, (v) => (v ? open() : teardown()), { immediate: true })
  onBeforeUnmount(teardown)
}
