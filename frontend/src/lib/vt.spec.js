// @vitest-environment happy-dom
/* `vtGo` envuelve un cambio de estado en una View Transition. La trampa: `startViewTransition`
 * **NO ejecuta el callback en el acto** — lo hace cuando el navegador ha capturado el fotograma
 * "antes". Quien llame y siga leyendo el estado justo después lo lee VIEJO.
 *
 * Pasó de verdad y llegó al usuario: al envolver `manga.open()`, el `await api.get(…/${
 * this.current.id})` de la línea siguiente corría con el manga ANTERIOR (o `null` en la primera
 * apertura), reventaba dentro del `try` y el modal decía «No se pudieron cargar los capítulos»
 * sin haber pedido nada. Sin errores en consola, porque el `catch` se lo comía.
 *
 * El contrato que se fija: vtGo devuelve una promesa que NO resuelve hasta que la mutación se ha
 * aplicado, con y sin soporte de View Transitions.
 *
 * `supportsVT` se calcula al importar el módulo, así que cada caso importa en limpio con el
 * `document` ya preparado — si no, se probaría siempre la misma rama.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  delete document.startViewTransition
})

describe('vtGo · la mutación ya está aplicada cuando resuelve', () => {
  it('sin soporte de View Transitions (el camino de reduced-motion y de los tests)', async () => {
    const { vtGo } = await import('./vt')
    let valor = 'viejo'
    await vtGo(() => { valor = 'nuevo' })
    expect(valor).toBe('nuevo')
  })

  it('CON soporte, aunque el navegador retrase el callback', async () => {
    // Así se comporta el de verdad: guarda el callback y lo ejecuta más tarde.
    let pendiente = null
    document.startViewTransition = (cb) => {
      pendiente = cb
      const done = new Promise((res) => setTimeout(async () => { await cb(); res() }, 0))
      return { ready: Promise.resolve(), finished: Promise.resolve(), updateCallbackDone: done }
    }
    const { vtGo } = await import('./vt')
    let valor = 'viejo'
    const p = vtGo(() => { valor = 'nuevo' })
    expect(valor).toBe('viejo')      // EL FALLO: aquí es donde `manga.open()` leía el estado
    await p
    expect(valor).toBe('nuevo')      // y aquí es donde debe leerlo
    expect(pendiente).not.toBeNull()
  })

  it('una transición abortada no deja tirado al que hizo await', async () => {
    // Si otra navegación interrumpe la transición, el navegador rechaza sus promesas con
    // AbortError. Eso no puede tumbar la apertura del manga: debe continuar igual.
    document.startViewTransition = (cb) => {
      cb()
      return {
        ready: Promise.reject(new Error('AbortError')),
        finished: Promise.reject(new Error('AbortError')),
        updateCallbackDone: Promise.reject(new Error('AbortError')),
      }
    }
    const { vtGo } = await import('./vt')
    let valor = 'viejo'
    await expect(vtGo(() => { valor = 'nuevo' })).resolves.toBeUndefined()
    expect(valor).toBe('nuevo')
  })
})
