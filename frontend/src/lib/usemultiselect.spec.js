/* Los casos límite del pincel y del rango, que son los que se rompen al tocarlo: arrastrar dos
 * veces por encima NO debe deshacer, soltar fuera debe soltar el pincel, y el rango debe seguir el
 * orden VISIBLE (no el de los datos) o shift+clic marca cosas que no ves.
 *
 * Se prueba `createMultiSelect` (la máquina de estado). `useMultiSelect` solo le añade el
 * `addEventListener('mouseup')` en onMounted — dos líneas sin lógica, y probarlas exigiría un DOM
 * y `@vue/test-utils`, que este repo no tiene. El `endPaint()` que ese listener invoca SÍ se prueba. */
import { beforeEach, describe, expect, it } from 'vitest'
import { createMultiSelect } from './useMultiSelect'

let order = []
const build = () => createMultiSelect(() => order)
const shift = { shiftKey: true, preventDefault() {} }

beforeEach(() => { order = ['1', '2', '3', '4', '5'] })

describe('clic normal', () => {
  it('marca y desmarca', () => {
    const s = build()
    s.down('2', {})
    expect(s.has('2')).toBe(true)
    s.down('2', {})
    expect(s.has('2')).toBe(false)
  })

  it('las claves se normalizan a texto (el capítulo 2 y el "2" son el mismo)', () => {
    const s = build()
    s.down(2, {})
    expect(s.has('2')).toBe(true)
    expect(s.count.value).toBe(1)
  })
})

describe('shift+clic extiende un rango', () => {
  it('marca todo lo que hay entre el ancla y el destino', () => {
    const s = build()
    s.down('2', {})
    s.down('4', shift)
    expect([...s.selected.value].sort()).toEqual(['2', '3', '4'])
  })

  it('hacia atrás funciona igual', () => {
    const s = build()
    s.down('4', {})
    s.down('2', shift)
    expect([...s.selected.value].sort()).toEqual(['2', '3', '4'])
  })

  it('si el ancla estaba DESMARCADA, el rango desmarca (como un explorador)', () => {
    const s = build()
    s.all()
    s.down('2', {})                       // desmarca el 2 → ancla apagada
    s.down('4', shift)
    expect(s.has('3')).toBe(false)
    expect(s.has('4')).toBe(false)
    expect(s.has('5')).toBe(true)         // fuera del rango: intacto
  })

  it('el rango sigue el orden VISIBLE, no el de los datos', () => {
    const s = build()
    order = ['5', '4', '3', '2', '1']     // la vista está ordenada al revés
    s.down('5', {})
    s.down('3', shift)
    expect([...s.selected.value].sort()).toEqual(['3', '4', '5'])
  })

  it('un elemento que ya no se ve no arrastra el rango', () => {
    const s = build()
    s.down('1', {})
    order = ['3', '4', '5']               // se filtró la lista; el ancla ya no está
    s.down('5', shift)
    expect([...s.selected.value].sort()).toEqual(['1'])   // no marca nada nuevo, no revienta
  })

  it('sin ancla previa, shift+clic es un clic normal', () => {
    const s = build()
    s.down('3', shift)
    expect([...s.selected.value]).toEqual(['3'])
  })
})

describe('pincel (arrastrar)', () => {
  it('arrastrar desde un elemento sin marcar MARCA los que toca', () => {
    const s = build()
    s.down('1', {})
    s.over('2'); s.over('3')
    expect([...s.selected.value].sort()).toEqual(['1', '2', '3'])
  })

  it('arrastrar desde uno marcado DESMARCA (no alterna)', () => {
    const s = build()
    s.all()
    s.down('1', {})
    s.over('2'); s.over('3')
    expect([...s.selected.value].sort()).toEqual(['4', '5'])
  })

  it('pasar dos veces por encima NO deshace el trabajo', () => {
    const s = build()
    s.down('1', {})
    s.over('2'); s.over('3'); s.over('2')   // vuelves atrás sin soltar
    expect(s.has('2')).toBe(true)
  })

  it('sin botón pulsado, pasar por encima no hace nada', () => {
    const s = build()
    s.over('3')
    expect(s.count.value).toBe(0)
  })

  it('soltar el ratón fuera de la lista suelta el pincel', () => {
    const s = build()
    s.down('1', {})
    s.endPaint()                            // esto es lo que dispara el mouseup de window
    s.over('2')
    expect(s.has('2')).toBe(false)
  })
})

describe('clear / all', () => {
  it('all marca lo VISIBLE, no todo lo que hubiera', () => {
    const s = build()
    order = ['2', '3']                      // lista filtrada
    s.all()
    expect([...s.selected.value].sort()).toEqual(['2', '3'])
  })

  it('clear borra la selección y el ancla', () => {
    const s = build()
    s.down('2', {})
    s.clear()
    expect(s.count.value).toBe(0)
    // Sin ancla, el shift+clic siguiente es un clic normal (no revive el rango viejo).
    s.down('4', shift)
    expect([...s.selected.value]).toEqual(['4'])
  })
})
