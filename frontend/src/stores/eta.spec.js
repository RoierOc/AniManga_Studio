import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { restante, etaTexto, olvidar } from '@/lib/eta'

describe('eta', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(0) })
  afterEach(() => { vi.useRealTimers() })

  it('no arriesga una estimación con una sola muestra', () => {
    expect(restante('a', 10)).toBe(null)
  })

  it('estima a partir del avance observado', () => {
    olvidar('b')
    restante('b', 0)
    // 20 % en 10 s → quedan 80 % ≈ 40 s
    vi.setSystemTime(10000)
    const seg = restante('b', 20)
    expect(seg).toBeGreaterThan(20)
    expect(seg).toBeLessThan(200)
  })

  it('una pausa sin avance NO dispara la estimación al infinito', () => {
    olvidar('c')
    restante('c', 0)
    vi.setSystemTime(10000)
    const antes = restante('c', 20)
    vi.setSystemTime(30000)          // 20 s sin moverse, como entre lotes de traducción
    const durante = restante('c', 20)
    expect(durante).not.toBe(null)
    expect(durante).toBe(antes)      // la velocidad se conserva, no se hunde a cero
  })

  it('se olvida de la tarea al terminar', () => {
    olvidar('d')
    restante('d', 0)
    vi.setSystemTime(5000)
    restante('d', 50)
    expect(restante('d', 100)).toBe(null)
  })

  it('texto en español, sin segundos sueltos', () => {
    expect(etaTexto(null)).toBe('')
    expect(etaTexto(12)).toBe('~1 min')
    expect(etaTexto(300)).toBe('~5 min')
    expect(etaTexto(3720)).toBe('~1 h 2 min')
    expect(etaTexto(7200)).toBe('~2 h')
  })
})
