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

function store(translatable = null) {
  const s = useMangaStore()
  s.current = { id: 'T' }
  // Traducir EXIGE las dos fuentes elegidas. El helper no las ponía, así que estos tests
  // probaban una situación imposible: `chain.translate` activo sin con qué traducir — que es
  // exactamente el estado en el que la app soltaba «no está en las DOS fuentes» a quien sólo
  // había pedido descargar y escalar.
  s.tp.artSel = { id: 'arte' }
  s.tp.esSel = { id: 'es' }
  s.upscaleChapter = vi.fn().mockResolvedValue(true)
  s.upscaleChapters = vi.fn()
  s.tpRun = vi.fn()
  // Por defecto, todo lo pedido es traducible; los tests que miren ese filtro pasan su lista.
  s.translatableAmong = vi.fn(async (chs) => (translatable === null ? chs : translatable))
  return s
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('cadena descargar → escalar (el flujo normal)', () => {
  it('escala cada capítulo en cuanto SU descarga acaba, sin esperar al resto', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'downloading') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledWith('1', { silent: true })
    expect(s.upscaleChapter).toHaveBeenCalledTimes(1)   // el 2 sigue bajando
  })

  it("'complete' cuenta como acabada — NO 'done' (es el bug que casi cuelo)", async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledOnce()
    expect(s.chainJobs.T).toBeUndefined()
  })

  it('una descarga fallida no se escala', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'error'), b: dl('T', '2', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledOnce()
    expect(s.upscaleChapter).toHaveBeenCalledWith('2', { silent: true })
  })

  it('un estado terminal no previsto no deja la cadena colgada', async () => {
    // 'no_chapters' lo emite download.py y NO está en _TASK_TERMINAL: listar los terminales en
    // vez de los ACTIVOS dejaba la cadena esperando eternamente.
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'no_chapters') }
    s._reconcileChain()
    expect(s.chainJobs.T).toBeUndefined()
    expect(s.tpRun).not.toHaveBeenCalled()
  })

  it('ignora las descargas de OTRO manga', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1'])
    s.downloads = { a: dl('OTRO', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
    expect(s.chainJobs.T).toBeTruthy()      // sigue esperando SU descarga
  })

  it('sin cadena marcada no encadena nada', async () => {
    const s = store()
    s.setChain({ upscale: false, translate: false })
    await s.armChain(['1'])
    expect(s.chainJobs.T).toBeUndefined()
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
  })
})

describe('cadena con traducción (la excepción)', () => {
  it('NO escala mientras descarga: espera a todos y traduce primero', async () => {
    // El orden no es negociable: traducir reescribe el arte e invalida el 4K.
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'downloading') }
    s._reconcileChain()
    expect(s.upscaleChapter).not.toHaveBeenCalled()
    expect(s.tpRun).not.toHaveBeenCalled()

    s.downloads.b = dl('T', '2', 'complete')
    s._reconcileChain()
    expect(s.tpRun).toHaveBeenCalledWith(['1', '2'])
    expect(s.upscaleChapter).not.toHaveBeenCalled()
  })

  it('escala DESPUÉS de que la traducción termine bien', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.chainJobs.T.stage).toBe('translating')

    s._onTranslateDone({ title: 'T', status: 'done', chapters: ['1'] })
    expect(s.upscaleChapters).toHaveBeenCalledWith(['1'])
    expect(s.chainJobs.T).toBeUndefined()
  })

  it('si la traducción falla NO escala: escalar un capítulo a medio traducir es peor que no escalar', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    s._onTranslateDone({ title: 'T', status: 'error', chapters: ['1'] })
    expect(s.upscaleChapters).not.toHaveBeenCalled()
    expect(s.chainJobs.T).toBeUndefined()
  })

  it('sólo traduce los capítulos que SÍ se descargaron', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1', '2'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '2', 'error') }
    s._reconcileChain()
    expect(s.tpRun).toHaveBeenCalledWith(['1'])
  })
})

describe('capítulos que no se pueden traducir (el caso de Kono Koi)', () => {
  // MEDIDO en la biblioteca real: el arte (MangaFire/fr) tenía 4 capítulos y el ES 8 distintos
  // → sólo el 1 en común. Armar la traducción del 19 hacía que el backend la ejecutara, no
  // hallara el capítulo en el arte y devolviera `status: done` + `note: falta capítulo en arte`.
  // Un "éxito" que se veía EXACTAMENTE igual que "la cadena se saltó la traducción".
  it('si NINGUNO es traducible, no arma traducción y escala igual', async () => {
    const s = store([])                       // ningún capítulo está en ambas fuentes
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['19'])
    expect(s.chainJobs.T.translate).toBe(false)
    s.downloads = { a: dl('T', '19', 'complete') }
    s._reconcileChain()
    expect(s.tpRun).not.toHaveBeenCalled()
    expect(s.upscaleChapter).toHaveBeenCalledWith('19', { silent: true })
  })

  it('traduce sólo los que se pueden y escala todos', async () => {
    const s = store(['1'])                    // de 1 y 19, sólo el 1 es traducible
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1', '19'])
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('T', '19', 'complete') }
    s._reconcileChain()
    expect(s.tpRun).toHaveBeenCalledWith(['1'])
    s._onTranslateDone({ title: 'T', status: 'done', chapters: ['1'] })
    expect(s.upscaleChapters).toHaveBeenCalledWith(['1', '19'])
  })

  it('si no se puede comprobar, no traduce a ciegas', async () => {
    const s = store()
    s.translatableAmong = vi.fn().mockResolvedValue(null)   // fallo al consultar
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['5'])
    expect(s.chainJobs.T.translate).toBe(false)   // "no pude saber" != "sí, adelante"
  })
})

describe('preferencia de la cadena', () => {
  it('por defecto escala y no traduce (el flujo real del usuario)', async () => {
    expect(store().chain).toEqual({ upscale: true, translate: false })
  })

  it('se recuerda entre sesiones', async () => {
    store().setChain({ translate: true })
    expect(JSON.parse(localStorage.getItem('manga-chain')).translate).toBe(true)
    setActivePinia(createPinia())
    expect(useMangaStore().chain.translate).toBe(true)
  })
})

// ── El bug real, reproducido ──────────────────────────────────────────────────────────────────
// MEDIDO en Witch Hat Atelier: 20 capítulos descargados en la misma tanda, sólo 10 escalados.
// En disco quedaron sin escalar EXACTAMENTE los que terminaron de descargar ANTES — es decir,
// los de la PRIMERA cadena armada. `armChain` hacía `this.chainJob = {…}`, así que la segunda
// tanda tiraba la primera y sus capítulos se quedaban descargados y sin 4K para siempre, sin un
// solo error en ninguna parte.
describe('armar una segunda tanda no puede tirar la cadena en curso', () => {
  it('FUSIONA los capítulos nuevos con los que ya esperaban', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['11', '12'])          // 1ª tanda: aún bajando
    await s.armChain(['1', '2'])            // 2ª tanda mientras la 1ª sigue viva
    expect([...s.chainJobs.T.waiting].sort()).toEqual(['1', '11', '12', '2'])

    s.downloads = {
      a: dl('T', '11', 'complete'), b: dl('T', '12', 'complete'),
      c: dl('T', '1', 'complete'),  d: dl('T', '2', 'complete'),
    }
    s._reconcileChain()
    // Los CUATRO se escalan. Antes sólo lo hacían los dos de la última tanda.
    expect(s.upscaleChapter.mock.calls.map(c => c[0]).sort()).toEqual(['1', '11', '12', '2'])
  })

  it('una selección sin nada que descargar NO borra la cadena viva', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1'])
    await s.armChain([])                    // "Descargar" sobre capítulos ya locales
    expect(s.chainJobs.T).toBeTruthy()
    s.downloads = { a: dl('T', '1', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter).toHaveBeenCalledWith('1', { silent: true })
  })

  it('dos mangas encadenan a la vez sin pisarse', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: false })
    await s.armChain(['1'])                 // manga T
    s.current = { id: 'U' }
    await s.armChain(['7'])                 // manga U
    expect(s.chainJobs.T).toBeTruthy()
    expect(s.chainJobs.U).toBeTruthy()
    s.downloads = { a: dl('T', '1', 'complete'), b: dl('U', '7', 'complete') }
    s._reconcileChain()
    expect(s.upscaleChapter.mock.calls.map(c => c[0]).sort()).toEqual(['1', '7'])
  })
})


describe('sin fuentes de traducción: la cadena NO habla de traducir', () => {
  // `chain` se persiste en localStorage: basta haber activado ES una vez para que
  // `chain.translate` siga true en un manga donde no hay fuentes elegidas. La UI ya lo pintaba
  // apagado ("Descargar y escalar") pero `armChain` miraba `chain.translate` a secas y avisaba
  // de un tercer paso que nadie había pedido. Pides dos cosas → te contestan por las dos.
  it('con translate persistido pero sin arte/ES: ni comprueba traducibles ni avisa', async () => {
    const s = store()
    s.tp.artSel = null
    s.tp.esSel = null
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    expect(s.translatableAmong).not.toHaveBeenCalled()
    expect(s.willTranslate).toBe(false)
    expect(s.chainJobs.T.translate).toBe(false)
    expect(s.chainJobs.T.upscale).toBe(true)
  })

  it('sólo falta el español: tampoco cuenta como traducible', async () => {
    const s = store()
    s.tp.esSel = null
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    expect(s.translatableAmong).not.toHaveBeenCalled()
    expect(s.chainJobs.T.translate).toBe(false)
  })

  it('con las dos fuentes sí comprueba (no se ha apagado la traducción de verdad)', async () => {
    const s = store()
    s.setChain({ upscale: true, translate: true })
    await s.armChain(['1'])
    expect(s.translatableAmong).toHaveBeenCalledWith(['1'])
    expect(s.chainJobs.T.translate).toBe(true)
  })
})
