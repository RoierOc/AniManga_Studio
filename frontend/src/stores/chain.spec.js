// La cadena descargar → (traducir) → escalar.
//
// Es la primera lógica del frontend con tests, y no por capricho: al escribirla metí DOS bugs que
// habrían fallado EN SILENCIO (la cadena simplemente no haría nada, indistinguible de "no la usé"):
//
//   1. comparar con 'done' cuando las descargas terminan en 'complete' (download.py) → nunca
//      dispara, y encima cada descarga buena caía en la rama de fallo;
//   2. armar la cadena con los capítulos MARCADOS en vez de con los que downloadChapters
//      realmente arrancó → espera para siempre a los ya locales, que nunca tienen tarea.
//
// Correr:  pnpm vitest run
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMangaStore } from './manga'

// get/post devuelven una promesa: el código real les encadena .then/.catch (p.ej. _onTranslateDone
// recarga los capítulos al acabar), y un mock que devuelva undefined revienta ahí.
vi.mock('@/lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    del: vi.fn().mockResolvedValue({}),
  },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

// localStorage de mentira: estos tests corren en Node y no necesitan un DOM entero (jsdom) sólo
// para leer y escribir una preferencia.
if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  }
}

const dl = (title, chapter, status) => ({ title, chapter, status })

function store() {
  const s = useMangaStore()
  s.current = { id: 'T' }
  s.upscaleChapter = vi.fn().mockResolvedValue(true)
  s.upscaleChapters = vi.fn()
  s.tpRun = vi.fn()
  return s
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('cadena descargar → escalar (el flujo normal)', () => {
  it('escala cada capítulo en cuanto SU descarga acaba, sin esperar al resto', () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'downloading') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledWith('1', { silent: true })
    expect(s.upscaleChapter).toHaveBeenCalledTimes(1)   // el 2 sigue bajando
  })

  it("'complete' cuenta como acabada — NO 'done' (es el bug que casi cuelo)", () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledOnce()
    expect(s.chainJob).toBeNull()
  })

  it('una descarga fallida no se escala', () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'error'), b: dl('T', '2', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledOnce()
    expect(s.upscaleChapter).toHaveBeenCalledWith('2', { silent: true })
  })

  it('un estado terminal no previsto no deja la cadena colgada', () => {
    // 'no_chapters' lo emite download.py y NO está en _TASK_TERMINAL: listar los terminales en
    // vez de los ACTIVOS dejaba la cadena esperando eternamente.
    const s = store()
    s.setChain({ upscale: true, translate: true })
    s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'no_chapters') }
    s._reconcileChain()
    expect(s.chainJob).toBeNull()
    expect(s.tpRun).not.toHaveBeenCalled()
  })

  it('ignora las descargas de OTRO manga', () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    s.armChain(['1'])
    s.downloads = { a: dl('OTRO', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
    expect(s.chainJob).not.toBeNull()      // sigue esperando SU descarga
  })

  it('sin cadena marcada no encadena nada', () => {
    const s = store()
    s.setChain({ upscale: false, translate: false })
    s.armChain(['1'])
    expect(s.chainJob).toBeNull()
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
  })
})

describe('cadena con traducción (la excepción)', () => {
  it('NO escala mientras descarga: espera a todos y traduce primero', () => {
    // El orden no es negociable: traducir reescribe el arte e invalida el 4K.
    const s = store()
    s.setChain({ upscale: true, translate: true })
    s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'downloading') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
    expect(s.tpRun).not.toHaveBeenCalled()

    s.downloads.b = dl('T', '2', 'complete')
    s._reconcileChain()
    expect(s.tpRun).toHaveBeenCalledWith(['1', '2'])
    expect(s.upscaleChapter).not.toHaveBeenCalled()
  })

  it('escala DESPUÉS de que la traducción termine bien', () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.chainJob.stage).toBe('translating')

    s._onTranslateDone({ title: 'T', status: 'done', chapters: ['1'] })
    expect(s.upscaleChapters).toHaveBeenCalledWith(['1'])
    expect(s.chainJob).toBeNull()
  })

  it('si la traducción falla NO escala: escalar un capítulo a medio traducir es peor que no escalar', () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    s._onTranslateDone({ title: 'T', status: 'error', chapters: ['1'] })
    expect(s.upscaleChapters).not.toHaveBeenCalled()
    expect(s.chainJob).toBeNull()
  })

  it('sólo traduce los capítulos que SÍ se descargaron', () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'error') }
    s._reconcileChain()
    expect(s.tpRun).toHaveBeenCalledWith(['1'])
  })
})

describe('preferencia de la cadena', () => {
  it('por defecto escala y no traduce (el flujo real del usuario)', () => {
    expect(store().chain).toEqual({ upscale: true, translate: false })
  })

  it('se recuerda entre sesiones', () => {
    store().setChain({ translate: true })
    expect(JSON.parse(localStorage.getItem('manga-chain')).translate).toBe(true)
    setActivePinia(createPinia())
    expect(useMangaStore().chain.translate).toBe(true)
  })
})
