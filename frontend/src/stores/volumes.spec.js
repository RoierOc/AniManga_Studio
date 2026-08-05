// Auto-organización de tomos (MangaDex volumes → CBZ por tomo).
//
// La parte frágil es el MAPEO volumen→capítulos locales y el LOTE:
//   - un tomo con rango debe seleccionar solo los caps locales dentro del rango;
//   - volumePlan() no debe repetir un capítulo en dos tomos (dedup);
//   - exportAllVolumes() debe encolar UNA exportación por tomo con caps y saltar los vacíos.
// Todo aislado: api mockeada, sin red.
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useMangaStore } from './manga'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn(), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))

if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), clear: () => m.clear() }
}

function withChapters(nums) {
  const s = useMangaStore()
  s.current = { id: 'T', name: 'T' }
  s.chapters = nums.map(n => ({ chapter: String(n) }))
  return s
}

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

describe('mapeo de tomo → capítulos locales', () => {
  it('selecciona solo los caps locales dentro del rango del tomo', () => {
    const s = withChapters([1, 2, 3, 4, 5])
    s.mdex.volumes = [{ volume: '1', label: 'Tomo 1', chapters: ['1', '2', '3'] }]
    const { selected } = s._mapVolumeToLocal(s.mdex.volumes[0])
    expect(selected.sort()).toEqual(['1', '2', '3'])
  })
})

describe('volumePlan()', () => {
  it('reparte caps entre tomos sin repetir ninguno (dedup)', () => {
    const s = withChapters([1, 2, 3, 4])
    s.mdex.volumes = [
      { volume: '1', label: 'Tomo 1', chapters: ['1', '2'] },
      { volume: '2', label: 'Tomo 2', chapters: ['3', '4'] },
    ]
    const plan = s.volumePlan()
    expect(plan.map(t => t.label)).toEqual(['Tomo 1', 'Tomo 2'])
    const all = plan.flatMap(t => t.chapters)
    expect(new Set(all).size).toBe(all.length)      // sin duplicados
    expect(all.sort()).toEqual(['1', '2', '3', '4'])
  })

  it('omite tomos sin capítulos descargados', () => {
    const s = withChapters([1, 2])
    s.mdex.volumes = [
      { volume: '1', label: 'Tomo 1', chapters: ['1', '2'] },
      { volume: '2', label: 'Tomo 2', chapters: ['9', '10'] },   // ninguno local
    ]
    const plan = s.volumePlan()
    expect(plan.map(t => t.label)).toEqual(['Tomo 1'])
  })
})

describe('exportAllVolumes()', () => {
  it('encola una exportación por tomo con sus capítulos', async () => {
    api.post.mockResolvedValue({ task_id: 'x' })
    const s = withChapters([1, 2, 3, 4])
    s.mdex.volumes = [
      { volume: '1', label: 'Tomo 1', chapters: ['1', '2'] },
      { volume: '2', label: 'Tomo 2', chapters: ['3', '4'] },
    ]
    const r = await s.exportAllVolumes({ coverPerVolume: false })
    expect(r).toEqual({ queued: 2, planned: 2 })
    const exportCalls = api.post.mock.calls.filter(c => c[0] === '/api/export/start')
    expect(exportCalls.length).toBe(2)
    expect(exportCalls[0][1].chapters).toEqual(['1', '2'])
    expect(exportCalls[0][1].volume_name).toBe('Tomo 1')
  })

  it('no encola nada si ningún tomo tiene caps locales', async () => {
    api.post.mockResolvedValue({ task_id: 'x' })
    const s = withChapters([1])
    s.mdex.volumes = [{ volume: '5', label: 'Tomo 5', chapters: ['50'] }]
    const r = await s.exportAllVolumes({ coverPerVolume: false })
    expect(r.queued).toBe(0)
    expect(api.post).not.toHaveBeenCalledWith('/api/export/start', expect.anything())
  })
})

// El nombre del tomo ES el nombre del fichero exportado: "Tomo 1.cbz" suelto no dice de qué obra
// es, y dos series distintas se pisan el nombre en la carpeta de destino.
describe('_tomoName: el CBZ lleva el nombre de la obra', () => {
  it('antepone la obra al tomo', () => {
    const s = withChapters([1])
    s.current = { id: 'Witch Hat Atelier', name: 'Witch Hat Atelier' }
    expect(s._tomoName({ volume: '1' })).toBe('Witch Hat Atelier - Tomo 1')
  })

  it('no la repite si la etiqueta de MangaDex ya la trae', () => {
    const s = withChapters([1])
    s.current = { id: 'Witch Hat Atelier', name: 'Witch Hat Atelier' }
    expect(s._tomoName({ volume: '1', label: 'Witch Hat Atelier Vol. 1' })).toBe('Witch Hat Atelier Vol. 1')
  })

  it('sin obra conocida, deja el tomo tal cual', () => {
    const s = withChapters([1])
    s.current = {}
    expect(s._tomoName({ volume: '3' })).toBe('Tomo 3')
  })

  it('el lote automático exporta con el nombre completo', () => {
    const s = withChapters([1, 2])
    s.current = { id: 'Witch Hat Atelier', name: 'Witch Hat Atelier' }
    s.mdex.volumes = [{ volume: '1', chapters: ['1', '2'] }]
    expect(s.volumePlan()[0].label).toBe('Witch Hat Atelier - Tomo 1')
  })
})

/* Las portadas de MangaDex inventan tomos: `loadMdexVolumes` añade un chip por cada portada cuyo
 * volumen no esté en el mapa. Las ediciones francesas y alemanas se numeran «1.1», «3.1», «12.1»
 * → chips de tomo que no casan con ningún capítulo. */
describe('portadas en otros idiomas no inventan tomos', () => {
  it('descarta las portadas fr/de antes de derivar tomos', async () => {
    const s = withChapters([1, 2])
    s.mdex.id = 'X'
    s.resolveMdexId = async () => 'X'
    api.get.mockImplementation((url) => {
      if (url.includes('/volumes/')) return Promise.resolve([{ volume: '1', label: 'Tomo 1', chapters: ['1', '2'], count: 2 }])
      if (url.includes('/covers/')) return Promise.resolve([
        { id: 'a', volume: '1', locale: 'es', url: 'u1' },
        { id: 'b', volume: '1.1', locale: 'fr', url: 'u2' },
        { id: 'c', volume: '3.1', locale: 'de', url: 'u3' },
      ])
      return Promise.resolve([])
    })
    await s.loadMdexVolumes()
    expect(s.mdex.volumes.map(v => v.volume)).toEqual(['1'])
  })

  it('pide el mapa de tomos filtrado por idioma', async () => {
    const s = withChapters([1])
    s.resolveMdexId = async () => 'X'
    api.get.mockResolvedValue([])
    await s.loadMdexVolumes()
    const pedido = api.get.mock.calls.map(c => c[0]).find(u => u.includes('/volumes/'))
    // Sin `lang`, `aggregate` declara capítulos de CUALQUIER idioma y el tomo sale «5/6».
    expect(pedido).toContain('lang=')
  })
})
