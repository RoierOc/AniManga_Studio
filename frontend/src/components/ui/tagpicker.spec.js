// @vitest-environment happy-dom
/* El campo de chips de las etiquetas.
 *
 * EL FALLO REAL, y el primero que se llevó el usuario: escribió una etiqueta, pulsó **Guardar**
 * sin haber pulsado Enter antes, y se guardó CERO — el texto se quedaba en el input y se tiraba
 * con el diálogo. Sin un solo error: «puse etiquetas pero no aparece nada».
 *
 * Es la trampa clásica de un campo de chips, y es exactamente la clase de fallo que ningún test
 * de store habría visto, porque el store recibía correctamente la lista vacía que se le mandaba.
 *
 * Correr:  cd frontend && pnpm test
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn().mockResolvedValue({ ok: true })
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: (...a) => post(...a), del: vi.fn() },
}))
vi.mock('@/lib/useModal', () => ({ useModal: vi.fn(), resolveOpen: () => true }))

import TagPicker from './TagPicker.vue'
import { useTagsStore } from '@/stores/tags'

async function abrir(tagsPrevias = []) {
  const tags = useTagsStore()
  tags.manga = { X: [...tagsPrevias] }
  const w = mount(TagPicker, { global: { stubs: { Teleport: true, Transition: false, Icon: true } } })
  tags.openPicker('manga', 'X', 'Título')
  await w.vm.$nextTick()
  return { w, tags, input: w.find('.tgp__field input') }
}
// Lo que se manda al backend en la última llamada.
const guardado = () => post.mock.calls.at(-1)?.[1]?.tags

beforeEach(() => { setActivePinia(createPinia()); post.mockClear() })

describe('TagPicker · confirmar lo que se está escribiendo', () => {
  it('EL FALLO: Guardar sin pulsar Enter antes NO puede tirar el texto', async () => {
    const { w, input } = await abrir()
    await input.setValue('para el finde')
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['para el finde'])
  })

  it('Enter confirma, como siempre', async () => {
    const { w, input } = await abrir()
    await input.setValue('releer')
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.findAll('.tgp__chip')).toHaveLength(1)
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['releer'])
  })

  it('salir del campo también confirma', async () => {
    const { w, input } = await abrir()
    await input.setValue('sin prisa')
    await input.trigger('blur')
    expect(w.findAll('.tgp__chip')).toHaveLength(1)
  })

  it('Cancelar NO guarda, ni siquiera lo que quedó a medio escribir', async () => {
    const { w, input } = await abrir()
    await input.setValue('no-guardar')
    await w.find('.tgp__btn').trigger('click')      // el primer botón es Cancelar
    expect(post).not.toHaveBeenCalled()
  })

  it('no duplica: lo ya confirmado con Enter no se añade otra vez al guardar', async () => {
    const { w, input } = await abrir()
    await input.setValue('releer')
    await input.trigger('keydown', { key: 'Enter' })
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['releer'])
  })

  it('«Releer» y «releer» son la misma etiqueta', async () => {
    const { w, input } = await abrir(['releer'])
    await input.setValue('Releer')
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['releer'])
  })

  it('guardar con el campo vacío deja la lista tal cual', async () => {
    const { w } = await abrir(['releer'])
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['releer'])
  })

  it('quitar un chip lo quita de verdad', async () => {
    const { w } = await abrir(['releer', 'finde'])
    await w.findAll('.tgp__chip button')[0].trigger('click')
    await w.find('.tgp__btn--go').trigger('click')
    expect(guardado()).toEqual(['finde'])
  })
})
