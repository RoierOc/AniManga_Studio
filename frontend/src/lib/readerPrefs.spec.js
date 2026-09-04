import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  effectiveReaderPrefs,
  readReaderPrefs,
  readerDefaults,
  readerPrefsKey,
  saveReaderPrefs,
} from './readerPrefs'

const values = new Map()
vi.stubGlobal('localStorage', {
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
  clear: () => values.clear(),
})

describe('preferencias del lector por obra', () => {
  beforeEach(() => values.clear())

  it('usa los valores globales como fallback', () => {
    values.set('reader-mode', 'webtoon')
    values.set('reader-fit', 'height')
    values.set('reader-dir', 'ltr')

    expect(readerDefaults()).toEqual({ mode: 'webtoon', fit: 'height', dir: 'ltr' })
    expect(effectiveReaderPrefs('obra-nueva')).toEqual({ mode: 'webtoon', fit: 'height', dir: 'ltr' })
  })

  it('guarda sólo valores válidos y no contamina otra obra', () => {
    saveReaderPrefs('obra:uno', { mode: 'webtoon', fit: 'original', dir: 'invalid' })
    saveReaderPrefs('obra:uno', { dir: 'ltr' })

    expect(readReaderPrefs('obra:uno')).toEqual({ mode: 'webtoon', fit: 'original', dir: 'ltr' })
    expect(readReaderPrefs('obra:dos')).toEqual({})
    expect(values.has(readerPrefsKey('obra:dos'))).toBe(false)
  })

  it('conserva el modo antiguo por serie hasta que la obra tenga uno nuevo', () => {
    expect(effectiveReaderPrefs('obra', 'webtoon').mode).toBe('webtoon')
    saveReaderPrefs('obra', { mode: 'paged' })
    expect(effectiveReaderPrefs('obra', 'webtoon').mode).toBe('paged')
  })

  it('descarta JSON corrupto y opciones inválidas', () => {
    values.set(readerPrefsKey('obra'), '{ roto')
    expect(readReaderPrefs('obra')).toEqual({})
    values.set(readerPrefsKey('otra'), JSON.stringify({ mode: 'nope', fit: 'width', extra: true }))
    expect(readReaderPrefs('otra')).toEqual({ fit: 'width' })
  })
})
