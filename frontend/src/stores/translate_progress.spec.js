// La barra de la traducción de manga.
//
// El bug que fija este test: `rescue` corre DESPUÉS de `compose` (rescatar de otra fuente ES las
// páginas que quedaron en inglés), y la fracción de página sólo se contaba en `compose`. Al pasar
// a `rescue` la fracción volvía a 0 y **la barra retrocedía** a mitad de trabajo. Además durante
// `download` no se movía nada, así que un capítulo grande parecía colgado.
//
// Es exactamente el tipo de fallo que no revienta: la traducción termina bien, sólo que la barra
// miente mientras tanto.
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMangaStore } from './manga'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn() }))
if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear(),
  }
}

const ID = 'Obra_transplant_ch'

function pctDe(estado) {
  const s = useMangaStore()
  s.transplant = { [ID]: { status: 'running', title: 'Obra', ...estado } }
  const t = s.liveTasks.find(x => x.id === ID)
  return t ? t : null
}

describe('progreso de traducción de manga', () => {
  beforeEach(() => { setActivePinia(createPinia()) })

  it('la barra NUNCA retrocede a lo largo de un capítulo', () => {
    const secuencia = [
      { phase: 'download', chapter: 5, chapterDone: 1, chapterTotal: 2 },
      { phase: 'compose',  chapter: 5, chapterDone: 1, chapterTotal: 2, pageDone: 1,  pageTotal: 30 },
      { phase: 'compose',  chapter: 5, chapterDone: 1, chapterTotal: 2, pageDone: 29, pageTotal: 30 },
      { phase: 'rescue',   chapter: 5, chapterDone: 1, chapterTotal: 2 },
      { phase: 'download', chapter: 6, chapterDone: 2, chapterTotal: 2 },
      { phase: 'compose',  chapter: 6, chapterDone: 2, chapterTotal: 2, pageDone: 15, pageTotal: 30 },
    ]
    let previo = -1
    for (const paso of secuencia) {
      const pct = pctDe(paso).pct
      expect(pct, `retrocede en ${paso.phase} cap ${paso.chapter}`).toBeGreaterThanOrEqual(previo)
      previo = pct
    }
  })

  it('durante la descarga la barra ya se mueve (no parece colgada)', () => {
    expect(pctDe({ phase: 'download', chapter: 1, chapterDone: 1, chapterTotal: 1 }).pct).toBeGreaterThan(0)
  })

  it('`rescue` se nombra y no se confunde con componer', () => {
    const t = pctDe({ phase: 'rescue', chapter: 5, chapterDone: 1, chapterTotal: 2,
                      note: '12 pág. en inglés → probando Mangas.in' })
    expect(t.label).toContain('rescatando')
    // El relato del trasplante viaja en `note`, no en `message`.
    expect(t.msg).toBe('12 pág. en inglés → probando Mangas.in')
  })

  it('sin total todavía, no inventa un porcentaje', () => {
    const t = pctDe({ phase: 'list', chapterDone: 0, chapterTotal: 0 })
    expect(t.pct).toBe(0)
    expect(t.label).toBe('Preparando…')
  })
})
