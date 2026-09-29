// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import MdCard from './MdCard.vue'
import { useDiscoveryStore } from '@/stores/discovery'

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

  it('apila la nota y «La tienes» para que ninguna etiqueta tape a la otra', () => {
    useDiscoveryStore().libraryTitles = ['ambas etiquetas']
    const wrapper = mount(MdCard, {
      props: { manga: { id: 'manga-3', title: 'Ambas etiquetas', cover: '' }, score: 82 },
      global: { stubs: { Icon: true } },
    })

    expect(wrapper.find('.mc__badges .mc__score').exists()).toBe(true)
    expect(wrapper.find('.mc__badges .mc__have').exists()).toBe(true)
  })
})
