// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ContinueRail from './ContinueRail.vue'

describe('ContinueRail · activación por teclado', () => {
  it('reproduce la tarjeta con Enter y Espacio', async () => {
    const wrapper = mount(ContinueRail, {
      props: { items: [{ id: 'ep-1', title: 'Serie', subtitle: 'Episodio 1' }] },
      global: { stubs: { Icon: true } },
    })
    const card = wrapper.get('.cwc')

    await card.trigger('keydown', { key: 'Enter' })
    await card.trigger('keydown', { key: ' ' })

    expect(card.attributes('role')).toBe('button')
    expect(card.attributes('tabindex')).toBe('0')
    expect(wrapper.emitted('play')).toHaveLength(2)
    wrapper.unmount()
  })
})
