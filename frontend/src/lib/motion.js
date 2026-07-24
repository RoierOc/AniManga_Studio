/* Scroll suave que RESPETA «reducir movimiento».
 *
 * `base.css` ya fuerza `scroll-behavior: auto !important` bajo `prefers-reduced-motion`, pero eso
 * solo gobierna el scroll que decide el CSS: cuando el JS pasa `behavior:'smooth'` en las opciones
 * de `scrollTo`/`scrollBy`/`scrollIntoView`, la opción GANA sobre la hoja de estilos. O sea que
 * los tres scrolls animados de la app (lector, riel, lector de novelas) seguían animándose para
 * quien había pedido al sistema que no lo hiciera — y para esas personas no es una molestia
 * estética, puede provocar mareo.
 *
 * Uso: `el.scrollBy({ top: 300, behavior: smoothBehavior() })`
 */
export function smoothBehavior() {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  } catch {
    return 'smooth'   // sin matchMedia (entorno de test): el comportamiento de siempre
  }
}
