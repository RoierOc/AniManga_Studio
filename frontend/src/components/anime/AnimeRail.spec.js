// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AnimeRail from './AnimeRail.vue'

const item = { anime: { id: 7, al_id: 70, title: 'Nisekoi', cover: '/cover.jpg', format: 'TV' }, ep: { num: 3 } }

describe('AnimeRail · navegación accesible', () => {
  it('expone episodios como botones con nombre y acción', async () => {
    const wrapper = mount(AnimeRail, {
      props: { title: 'Continuar', items: [item], variant: 'episode' },
      global: { stubs: { Icon: true } },
    })

    const action = wrapper.get('button.ecard')
    expect(action.attributes('aria-label')).toBe('Reproducir Nisekoi, episodio 3')
    await action.trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(item)
    wrapper.unmount()
  })

  it('expone las portadas como botones con nombre y acción', async () => {
    const wrapper = mount(AnimeRail, {
      props: { title: 'Para ti', items: [item], variant: 'poster' },
      global: { stubs: { Icon: true } },
    })

    const action = wrapper.get('button.pcard')
    expect(action.attributes('aria-label')).toBe('Abrir Nisekoi')
    await action.trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(item)
    wrapper.unmount()
  })
})
