/* `useModal` bloquea el scroll de la página mientras hay un modal abierto. Un fallo aquí no se
 * ve como "un modal raro": se ve como que **la app entera deja de scrollear**, sin ningún modal
 * en pantalla. Pasó: cinco modales se registraron con `() => !!d` siendo `d` un `computed`, y un
 * ref es SIEMPRE truthy → todos "abiertos" desde el arranque.
 *
 * Se prueba `resolveOpen`, que es exactamente la costura que falló.
 */
import { describe, it, expect, vi } from 'vitest'
import { computed, ref, shallowRef } from 'vue'
import { resolveOpen } from './useModal'

describe('useModal · resolveOpen', () => {
  it('cerrado es cerrado (computed vacío, con .value)', () => {
    const d = computed(() => null)
    expect(resolveOpen(() => !!d.value)).toBe(false)
  })

  it('abierto es abierto', () => {
    const d = computed(() => ({ id: 1 }))
    expect(resolveOpen(() => !!d.value)).toBe(true)
  })

  it('EL FALLO: un ref sin .value no puede dar "abierto"', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const vacio = computed(() => null)
    const lleno = computed(() => ({ id: 1 }))
    // `!!ref` es siempre true; resolveOpen lo desenvuelve en vez de creérselo.
    expect(resolveOpen(() => vacio)).toBe(false)
    expect(resolveOpen(() => lleno)).toBe(true)
    expect(warn).toHaveBeenCalled()   // y además lo dice, no lo tapa
    warn.mockRestore()
  })

  it('acepta también un ref o un booleano directos', () => {
    expect(resolveOpen(ref(false))).toBe(false)
    expect(resolveOpen(shallowRef(true))).toBe(true)
    expect(resolveOpen(false)).toBe(false)
    expect(resolveOpen(true)).toBe(true)
  })

  it('sigue el valor del ref al cambiar (se re-evalúa, no se congela)', () => {
    const open = ref(false)
    const cond = () => open.value
    expect(resolveOpen(cond)).toBe(false)
    open.value = true
    expect(resolveOpen(cond)).toBe(true)
  })
})
