import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  mergeNovelProgress,
  normalizeNovelProgress,
  readLocalNovelProgress,
  sameNovelProgress,
  saveLocalNovelProgress,
} from './novelProgress'

const values = new Map()
vi.stubGlobal('localStorage', {
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  clear: () => values.clear(),
})

describe('progreso durable de novelas', () => {
  beforeEach(() => values.clear())

  it('fusiona por fecha y no deja que una respuesta vieja retroceda la lectura', () => {
    const local = { n1: { title: 'Obra', chapterIndex: 8, scroll: 40, at: 200 } }
    const remote = { n1: { title: 'Obra', chapterIndex: 3, scroll: 80, at: 100 }, n2: { title: 'Otra', at: 150 } }

    expect(mergeNovelProgress(local, remote)).toEqual({
      n1: local.n1,
      n2: remote.n2,
    })
  })

  it('persiste una forma limpia y omite entradas corruptas', () => {
    saveLocalNovelProgress({ n1: { title: 'Obra', chapterIndex: 2, unknown: 'no', at: 10 }, n2: null })

    expect(readLocalNovelProgress()).toEqual({ n1: { title: 'Obra', chapterIndex: 2, at: 10 } })
    expect(normalizeNovelProgress(null)).toEqual({})
  })

  it('compara sin depender del orden de las claves de las obras', () => {
    expect(sameNovelProgress({ b: { at: 2 }, a: { at: 1 } }, { a: { at: 1 }, b: { at: 2 } })).toBe(true)
  })
})
