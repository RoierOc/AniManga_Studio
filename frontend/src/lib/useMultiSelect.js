/* Selección múltiple con shift+clic (rango) y clic-arrastre (pincel).
 *
 * El mecanismo se escribió para la lista de capítulos de `MangaModal` y era, de largo, la mejor
 * interacción de la app… y la disfrutaba UNA sola pantalla. Esto lo saca de ahí sin cambiarlo:
 * mismas reglas, mismos casos límite ya aprendidos. `MangaModal` lo usa igual que antes.
 *
 * Se conservan las DOS formas de seleccionar porque cubren cosas distintas: arrastrar va bien para
 * rachas cortas y adyacentes, pero para marcar 50 elementos tendrías que arrastrar por una lista
 * que scrollea; shift+clic lo hace en dos clics y es el estándar que todo el mundo ya conoce
 * (Explorador, Gmail).
 *
 * Uso:
 *   const sel = useMultiSelect(() => visibleItems.value.map(x => x.id))
 *   <div @mousedown="sel.down(item.id, $event)" @mouseenter="sel.over(item.id)"
 *        :class="{ 'is-sel': sel.has(item.id) }">
 *
 * `order()` debe devolver las claves EN EL ORDEN QUE SE VEN: el rango de shift+clic se calcula
 * sobre esa lista, así que si la vista está filtrada u ordenada, el rango sigue lo que el usuario
 * tiene delante y no el orden interno de los datos.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

/* La máquina de estado, sin ciclo de vida de Vue: así se prueba en node sin DOM ni
 * `@vue/test-utils`. `useMultiSelect` es esto + las dos líneas que enganchan el `mouseup`. */
export function createMultiSelect(order) {
  const selected = ref(new Set())
  const lastTouched = ref(null)   // ancla del rango: último elemento tocado con un clic normal
  const painting = ref(null)      // null | true (pintando selección) | false (pintando borrado)

  const k = (x) => String(x)
  const has = (key) => selected.value.has(k(key))
  const count = computed(() => selected.value.size)
  const active = computed(() => selected.value.size > 0)

  function set(key, on) {
    const s = new Set(selected.value)
    on ? s.add(k(key)) : s.delete(k(key))
    selected.value = s
  }

  function applyRange(from, to, on) {
    const keys = (order() || []).map(k)
    const i = keys.indexOf(k(from)), j = keys.indexOf(k(to))
    if (i < 0 || j < 0) return
    const s = new Set(selected.value)
    for (const key of keys.slice(Math.min(i, j), Math.max(i, j) + 1)) on ? s.add(key) : s.delete(key)
    selected.value = s
  }

  // mousedown (no click): hay que empezar a pintar ANTES de soltar el botón.
  function down(key, ev) {
    if (ev?.shiftKey && lastTouched.value != null) {
      // Rango: extiende con el mismo estado que tenga el ancla, como en un explorador.
      applyRange(lastTouched.value, key, has(lastTouched.value))
      ev.preventDefault()        // evita que shift+clic seleccione texto de la lista
      return
    }
    const on = !has(key)
    set(key, on)
    lastTouched.value = key
    painting.value = on          // arrastrar sigue haciendo LO MISMO que el primer clic
  }

  // Al entrar en otro elemento con el botón pulsado se pinta igual que el primero: si empezaste
  // marcando, marcas; si empezaste desmarcando, desmarcas. Nunca alterna (eso haría que pasar por
  // encima dos veces deshiciera el trabajo).
  function over(key) {
    if (painting.value === null) return
    set(key, painting.value)
    lastTouched.value = key
  }

  function clear() { selected.value = new Set(); lastTouched.value = null }
  function all() { selected.value = new Set((order() || []).map(k)); lastTouched.value = null }

  function endPaint() { painting.value = null }

  return { selected, count, active, has, down, over, set, clear, all, endPaint }
}

export function useMultiSelect(order) {
  const ms = createMultiSelect(order)
  /* El mouseup se escucha en window, no en la lista: si sueltas fuera (muy fácil al arrastrar hasta
   * el borde para scrollear) el pincel se quedaría pegado y seguirías seleccionando sin pulsar. */
  onMounted(() => window.addEventListener('mouseup', ms.endPaint))
  onBeforeUnmount(() => window.removeEventListener('mouseup', ms.endPaint))
  return ms
}
