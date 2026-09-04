import { describe, expect, it } from 'vitest'
import { gridMoveIndex } from './useGridKeyboard'

describe('navegación de rejillas', () => {
  it('mueve horizontalmente y no sale por los extremos', () => {
    expect(gridMoveIndex(0, 'ArrowLeft', 8, 4)).toBe(0)
    expect(gridMoveIndex(2, 'ArrowRight', 8, 4)).toBe(3)
    expect(gridMoveIndex(7, 'ArrowRight', 8, 4)).toBe(7)
  })

  it('mueve una fila real según el número de columnas', () => {
    expect(gridMoveIndex(1, 'ArrowDown', 12, 4)).toBe(5)
    expect(gridMoveIndex(9, 'ArrowUp', 12, 4)).toBe(5)
  })

  it('en la última fila aterriza en el último elemento disponible', () => {
    expect(gridMoveIndex(6, 'ArrowDown', 7, 4)).toBe(6)
    expect(gridMoveIndex(1, 'ArrowDown', 6, 4)).toBe(5)
  })

  it('una entrada inválida no rompe el foco', () => {
    expect(gridMoveIndex(-1, 'ArrowDown', 0, 4)).toBe(-1)
    expect(gridMoveIndex(2, 'PageDown', 8, 4)).toBe(2)
  })
})
