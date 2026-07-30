import { defineAsyncComponent } from 'vue'

/* Carga perezosa de una vista que SÍ se recupera cuando el chunk no llega.
 *
 * MEDIDO: si el `import()` de un chunk falla, la vista quedaba muerta **para siempre**. Con la
 * red ya restablecida, ni «Reintentar» ni salir de la vista y volver la recuperaban — el
 * navegador memoriza el módulo fallido en su module map, así que volver a importarlo devuelve
 * exactamente el mismo rechazo. O sea, un botón primario que no puede cumplir lo que promete.
 *
 * Y el caso que de verdad ocurre no es un corte de red: es **actualizar la app con la pestaña
 * abierta**. El chunk cambia de nombre (`ActivityView-CnDf0x29.js` → `ActivityView-CZtXeULx.js`)
 * y el viejo deja de existir, así que la pestaña pide un fichero que ya no está. Verificado
 * reconstruyendo el frontend en caliente: vista muerta, y «Reintentar» tampoco.
 *
 * Lo único que arregla ambos casos es recargar: trae el `index.html` nuevo y con él los nombres
 * de chunk correctos. Se hace UNA vez, con guarda en `sessionStorage` para que un fallo
 * persistente no se convierta en un bucle de recargas.
 */
export const RELOAD_KEY = 'chunk-reload-at'
const VENTANA_MS = 10000

export function lazyView(loader, { now = Date.now, reload = () => window.location.reload(), storage = null } = {}) {
  const store = storage || (typeof sessionStorage !== 'undefined' ? sessionStorage : null)
  return defineAsyncComponent(() => loader().catch((err) => {
    const ultimo = Number(store?.getItem(RELOAD_KEY) || 0)
    if (now() - ultimo > VENTANA_MS) {
      store?.setItem(RELOAD_KEY, String(now()))
      reload()
      // La página se va: no resolver evita pintar un error de medio segundo antes de irse.
      return new Promise(() => {})
    }
    // Ya se recargó hace nada y sigue fallando → no es un chunk viejo, es algo peor.
    // Que lo cuente la pantalla de error, que para eso está.
    throw err
  }))
}

/** ¿Este error es «no pude descargar el trozo de la app», y no un fallo de la vista en sí? */
export function esFalloDeChunk(msg) {
  return /dynamically imported module|Importing a module script failed|Failed to fetch/i.test(String(msg || ''))
}
