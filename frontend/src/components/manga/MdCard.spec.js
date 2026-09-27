// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import MdCard from './MdCard.vue'

beforeEach(() => setActivePinia(createPinia()))

describe('MdCard · acciones accesibles sin clic derecho', () => {
  it('expone un botón de menú y no abre la ficha al usarlo', async () => {
    const manga = { id: 'manga-1', title: 'Manga de prueba', cover: '' }
    const wrapper = mount(MdCard, { props: { manga }, global: { stubs: { Icon: true } } })

    const menuButton = wrapper.get('button[aria-label="Más acciones para Manga de prueba"]')
    expect(menuButton.exists()).toBe(true)
    await menuButton.trigger('click')

    expect(wrapper.emitted('menu')?.[0]?.[0].manga).toMatchObject({ id: manga.id, title: manga.title })
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('Enter dentro del botón no abre también la ficha', async () => {
    const wrapper = mount(MdCard, {
      props: { manga: { id: 'manga-2', title: 'Otra prueba', cover: '' } },
      global: { stubs: { Icon: true } },
    })

    await wrapper.get('.mc__actions').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('open')).toBeUndefined()
  })
})
