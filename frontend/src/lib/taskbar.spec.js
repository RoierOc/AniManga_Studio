/* La traducción a la barra de tareas de Windows. Lo que se puede romper en silencio es el caso
 * «hay tarea pero aún no hay porcentaje»: pintar 0 % deja el icono como si estuviera atascado,
 * que engaña más que no decir el número.
 */
import { describe, it, expect } from 'vitest'
import { estadoBarra } from './taskbar'

describe('estadoBarra', () => {
  it('sin tareas, la barra desaparece', () => {
    expect(estadoBarra(0, 0)).toEqual({ state: 'none', value: 0 })
    expect(estadoBarra(0, 55)).toEqual({ state: 'none', value: 0 })
  })

  it('con tarea y sin porcentaje aún, indeterminada (no 0 %)', () => {
    expect(estadoBarra(1, 0)).toEqual({ state: 'indeterminate', value: 0 })
  })

  it('con porcentaje, barra normal redondeada', () => {
    expect(estadoBarra(2, 42.6)).toEqual({ state: 'normal', value: 43 })
  })

  it('nunca se pasa de 100', () => {
    expect(estadoBarra(1, 140)).toEqual({ state: 'normal', value: 100 })
  })
})
