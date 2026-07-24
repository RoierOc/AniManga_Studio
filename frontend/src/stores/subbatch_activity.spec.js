/* El lote de subtítulos en el Centro de Actividad.
 *
 * Contrato: un lote muestra UNA tarjeta agregada (5/12) y sus traducciones IA hijas (las que
 * llevan `batch_id`) NO se cuentan aparte — si no, el episodio en curso aparecería dos veces.
 * Se prueba a través del store real (normalizeTask es privado; el getter lo usa).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// El store de manga lee localStorage al construirse; el entorno de test no lo trae.
const _mem = {}
vi.stubGlobal('localStorage', {
  getItem: (k) => (k in _mem ? _mem[k] : null),
  setItem: (k, v) => { _mem[k] = String(v) },
  removeItem: (k) => { delete _mem[k] },
})

import { useMangaStore } from './manga'

describe('lote de subtítulos · Centro de Actividad', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('el lote sale como UNA tarjeta y la hija con batch_id se suprime', () => {
    const s = useMangaStore()
    s.subtitleBatches = {
      b1: { batch_id: 'b1', status: 'running', done: 5, total: 12, title: 'Uma Musume' },
    }
    s.subtitles = {
      // hija del lote: NO debe contarse (la cubre el agregado)
      child: { status: 'translating', progress: 40, title: 'Uma Musume', episode: 6, batch_id: 'b1' },
    }
    const batchTasks = s.normalizedTasks.filter(t => t.kind === 'subtitle_batch')
    const subChildren = s.normalizedTasks.filter(t => t.kind === 'subtitle')
    expect(batchTasks).toHaveLength(1)
    expect(batchTasks[0].label).toContain('5/12')
    expect(batchTasks[0].pct).toBe(42)                 // 5/12
    expect(subChildren).toHaveLength(0)                // la hija con batch_id se suprimió
  })

  it('una traducción suelta (sin lote) SÍ se cuenta', () => {
    const s = useMangaStore()
    s.subtitles = { solo: { status: 'translating', progress: 30, title: 'Otro', episode: 2 } }
    const subs = s.normalizedTasks.filter(t => t.kind === 'subtitle')
    expect(subs).toHaveLength(1)
    expect(subs[0].label).toContain('Ep. 2')
  })

  it('un lote en curso está vivo; terminado pasa al historial', () => {
    const s = useMangaStore()
    s.subtitleBatches = { r: { batch_id: 'r', status: 'running', done: 1, total: 3, title: 'A' } }
    expect(s.liveTasks.some(t => t.kind === 'subtitle_batch')).toBe(true)

    s.subtitleBatches = { d: { batch_id: 'd', status: 'done', done: 3, total: 3, title: 'A', ended_at: 1000 } }
    expect(s.liveTasks.some(t => t.kind === 'subtitle_batch')).toBe(false)
    expect(s.historyTasks.some(t => t.kind === 'subtitle_batch')).toBe(true)
  })
})
