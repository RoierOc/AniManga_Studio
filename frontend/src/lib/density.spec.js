/* vitest corre en node SIN DOM y sin localStorage: `density.js` está escrito para sobrevivir a
 * eso (guardas en `applyDensity` y en la lectura), y este banco lo comprueba — si alguien quita
 * la guarda, el módulo reventaría al importarse desde cualquier test. */
import { describe, it, expect, beforeEach } from 'vitest'
import { DENSITIES, density, setDensity, scaleOf, reloadDensity } from './density'

describe('densidad de la rejilla', () => {
  beforeEach(() => { setDensity('normal') })

  it('el valor por defecto es normal y escala 1 (no cambia nada de lo que ya había)', () => {
    expect(density.value).toBe('normal')
    expect(scaleOf('normal')).toBe(1)
  })

  it('cambia a un paso válido', () => {
    setDensity('dense')
    expect(density.value).toBe('dense')
    expect(scaleOf('dense')).toBeLessThan(1)   // menos ancho = más tarjetas por fila
  })

  it('un id inválido NO cambia el estado (un pref corrupto no debe romper la rejilla)', () => {
    setDensity('comfy')
    setDensity('gigante')
    expect(density.value).toBe('comfy')
    expect(scaleOf('gigante')).toBe(1)   // cae al normal, nunca a undefined
  })

  it('los tres pasos van de más ancho a menos, sin empates', () => {
    const s = DENSITIES.map(d => d.scale)
    expect(s).toEqual([...s].sort((a, b) => b - a))
    expect(new Set(s).size).toBe(s.length)
  })

  it('recargar sin almacenamiento vuelve a normal en vez de lanzar', () => {
    setDensity('dense')
    expect(() => reloadDensity()).not.toThrow()
    expect(density.value).toBe('normal')
  })
})
