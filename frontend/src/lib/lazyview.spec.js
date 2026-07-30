import { describe, it, expect, vi } from 'vitest'
import { lazyView, RELOAD_KEY, esFalloDeChunk } from './lazyView'

/* La costura peligrosa aquí es la GUARDA: recargar arregla un chunk viejo, pero si el fallo es
 * permanente (el servidor no sirve ese fichero, punto) recargar en bucle deja la app inutilizable
 * y sin forma de contarte qué pasa. Eso es lo que se fija. */

function almacen(inicial = {}) {
  const d = { ...inicial }
  return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = String(v) }, _d: d }
}

// `defineAsyncComponent` no ejecuta el loader hasta que se monta; se invoca a mano.
const cargar = (comp) => comp.__asyncLoader()

describe('lazyView', () => {
  it('recarga UNA vez cuando el chunk no llega', async () => {
    const reload = vi.fn()
    const store = almacen()
    const comp = lazyView(() => Promise.reject(new Error('Failed to fetch dynamically imported module')),
                          { now: () => 1_000_000, reload, storage: store })
    cargar(comp)
    await new Promise(r => setTimeout(r, 0))
    expect(reload).toHaveBeenCalledTimes(1)
    expect(store.getItem(RELOAD_KEY)).toBe('1000000')
  })

  it('NO recarga en bucle: si acaba de recargar, deja que el error se vea', async () => {
    const reload = vi.fn()
    const store = almacen({ [RELOAD_KEY]: '999000' })   // hace 1 s
    const comp = lazyView(() => Promise.reject(new Error('Failed to fetch dynamically imported module')),
                          { now: () => 1_000_000, reload, storage: store })
    await expect(cargar(comp)).rejects.toThrow(/dynamically imported/)
    expect(reload).not.toHaveBeenCalled()
  })

  it('pasada la ventana, un fallo NUEVO sí puede recargar otra vez', async () => {
    const reload = vi.fn()
    const store = almacen({ [RELOAD_KEY]: '900000' })   // hace 100 s
    const comp = lazyView(() => Promise.reject(new Error('Failed to fetch dynamically imported module')),
                          { now: () => 1_000_000, reload, storage: store })
    cargar(comp)
    await new Promise(r => setTimeout(r, 0))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('no toca nada cuando la vista carga bien', async () => {
    const reload = vi.fn()
    const store = almacen()
    const comp = lazyView(() => Promise.resolve({ default: { render: () => null } }),
                          { now: () => 1, reload, storage: store })
    await cargar(comp)
    expect(reload).not.toHaveBeenCalled()
    expect(store.getItem(RELOAD_KEY)).toBe(null)
  })
})

describe('esFalloDeChunk', () => {
  it('reconoce los mensajes que sueltan los navegadores', () => {
    expect(esFalloDeChunk('Failed to fetch dynamically imported module: http://x/a.js')).toBe(true)
    expect(esFalloDeChunk('Importing a module script failed.')).toBe(true)   // Safari
  })
  it('no se traga un error normal de la vista', () => {
    expect(esFalloDeChunk("Cannot read properties of undefined (reading 'map')")).toBe(false)
  })
})
