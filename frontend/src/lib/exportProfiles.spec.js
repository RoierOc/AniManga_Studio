import { describe, expect, it } from 'vitest'
import { EXPORT_PROFILES, exportProfileValues } from './exportProfiles'

describe('perfiles de exportacion', () => {
  it('expone perfiles conocidos y no muta sus valores', () => {
    expect(EXPORT_PROFILES.map(p => p.value)).toEqual(['manual', 'compatible', 'tablet', 'quality', 'maximum'])
    const first = exportProfileValues('tablet')
    first.quality = 100
    expect(exportProfileValues('tablet').quality).toBe(85)
  })

  it('devuelve null para un perfil desconocido', () => {
    expect(exportProfileValues('inventado')).toBeNull()
  })
})
