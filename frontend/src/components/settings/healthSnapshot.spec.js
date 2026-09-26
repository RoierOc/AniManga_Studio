import { describe, expect, it } from 'vitest'
import { healthSnapshotStatus } from './healthSnapshot'

describe('healthSnapshotStatus', () => {
  it('no presenta como sana una consulta fallida', () => {
    expect(healthSnapshotStatus({
      health: null,
      integrity: null,
      healthError: 'No responde',
      integrityError: '',
    })).toBe('incomplete')
  })

  it('distingue una respuesta limpia y completa', () => {
    expect(healthSnapshotStatus({
      health: { ok: true, problems: 0 },
      integrity: { available: true, mismatched: [], checked: 12 },
      healthError: '',
      integrityError: '',
    })).toBe('healthy')
  })

  it('conserva incidencias válidas como problemas, no como error de consulta', () => {
    expect(healthSnapshotStatus({
      health: { ok: true, problems: 1 },
      integrity: { available: true, mismatched: [] },
      healthError: '',
      integrityError: '',
    })).toBe('issues')
  })

  it('no trata una respuesta incompleta como un diagnóstico limpio', () => {
    expect(healthSnapshotStatus({
      health: { ok: true },
      integrity: { available: true },
      healthError: '',
      integrityError: '',
    })).toBe('incomplete')
  })
})
