import { onMounted, onUnmounted, ref } from 'vue'

/* Ancho REAL de la caja del póster, para pedirle al proxy el peldaño que toca.
 *
 * Un ancho fijo en el componente no sabe nada de la pantalla ni de la densidad de rejilla:
 * MEDIDO a 2560x1440 con dpr 1,5 y tarjetas grandes, la portada se pintaba a 639 px reales con
 * un fichero de 460 → borrosa. El observador es lo que lo mantiene cierto al cambiar la densidad
 * o el tamaño de ventana; sin él haría falta recargar para recuperar la nitidez.
 *
 * Devuelve `[el, w]`: `el` va en el `ref` del contenedor, `w` en `imgProxy(url, w)`.
 */
export function useBoxWidth(fallback = 300) {
  const el = ref(null)
  const w = ref(fallback)
  let ro = null
  onMounted(() => {
    if (!el.value) return
    const medir = () => { const n = el.value?.clientWidth; if (n) w.value = n }
    medir()
    if ('ResizeObserver' in window) { ro = new ResizeObserver(medir); ro.observe(el.value) }
  })
  onUnmounted(() => ro?.disconnect())
  return [el, w]
}
