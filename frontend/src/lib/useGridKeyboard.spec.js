import { describe, expect, it, vi } from 'vitest'
import { gridMoveIndex, gridSelectionRange, useGridKeyboard } from './useGridKeyboard'

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

  it('calcula el rango sobre el orden visible, también hacia arriba', () => {
    expect(gridSelectionRange(['a', 'b', 'c', 'd'], 'b', 'd')).toEqual(['b', 'c', 'd'])
    expect(gridSelectionRange(['a', 'b', 'c', 'd'], 'd', 'b')).toEqual(['b', 'c', 'd'])
    expect(gridSelectionRange(['a', 'b'], 'x', 'b')).toEqual([])
  })

  it('alterna con Ctrl y extiende con Shift sin convertir el clic normal', () => {
    const sel = useGridKeyboard(() => ['a', 'b', 'c'])
    const event = (mods) => ({ ...mods, preventDefault: vi.fn(), currentTarget: { focus: vi.fn() } })

    expect(sel.onSelect('a', event({}))).toBe(false)
    expect([...sel.selected.value]).toEqual([])
    sel.onSelect('a', event({ ctrlKey: true }))
    sel.onSelect('c', event({ shiftKey: true }))
    expect([...sel.selected.value]).toEqual(['a', 'b', 'c'])
    sel.onSelect('b', event({ ctrlKey: true }))
    expect([...sel.selected.value]).toEqual(['a', 'c'])
  })
})
