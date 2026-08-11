/* El horneado Anime4K en el Centro de Actividad.
 *
 * Contrato: es la tarea MÁS LARGA de la app y tiene que salir donde salen todas las demás. Era la
 * única que no llegaba: tenía su propio /status para su propia pantalla y nunca se enganchó al
 * retrato agregado, así que un proceso de horas parecía colgado porque no aparecía en ningún sitio.
 *
 * Se prueba a través del store real (normalizeTask es privado; el getter lo usa).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const _mem = {}
vi.stubGlobal('localStorage', {
  getItem: (k) => (k in _mem ? _mem[k] : null),
  setItem: (k, v) => { _mem[k] = String(v) },
  removeItem: (k) => { delete _mem[k] },
})

import { useMangaStore } from './manga'

describe('horneado Anime4K · Centro de Actividad', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('un horneado en curso sale como tarea activa, agrupada por SERIE y no por fichero', () => {
    const s = useMangaStore()
    s.animeUpscale = {
      t1: { status: 'running', progress: 37.4, title: 'Awajima Hyakkei', quality: 'Máxima',
        file: '[Erai-raws] Awajima Hyakkei - 04 [1080p].mkv', eta: 1604 },
    }
    const tareas = s.normalizedTasks.filter(t => t.kind === 'anime_upscale')
    expect(tareas).toHaveLength(1)
    expect(tareas[0].pct).toBe(37)
    expect(tareas[0].label).toContain('Máxima')
    // Agrupada como anime: sin esto el clic abriría el modal de MANGA y buscaría una portada
    // en /api/library/thumb/<serie>, que no existe.
    expect(tareas[0].isAnime).toBe(true)
    expect(tareas[0].mangaId).toBe('anime:Awajima Hyakkei')
    // La tarea está VIVA: es lo que hace que el indicador de la barra la cuente.
    expect(s.liveTasks.some(t => t.kind === 'anime_upscale')).toBe(true)
  })

  it('cancelado y con error caen al historial, no se quedan cargando para siempre', () => {
    const s = useMangaStore()
    const ayer = Math.floor(Date.now() / 1000)
    s.animeUpscale = {
      t1: { status: 'cancelled', progress: 0, title: 'A', ended_at: ayer },
      t2: { status: 'error', progress: 12, title: 'B', error: 'ffmpeg salió con 1', ended_at: ayer },
    }
    expect(s.liveTasks.filter(t => t.kind === 'anime_upscale')).toHaveLength(0)
    expect(s.historyTasks.filter(t => t.kind === 'anime_upscale')).toHaveLength(2)
  })
})
