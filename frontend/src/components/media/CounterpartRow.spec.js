// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { get } }))

import CounterpartRow from './CounterpartRow.vue'

describe('CounterpartRow — errores de AniList', () => {
  it('muestra el mensaje del error y no el cuerpo JSON crudo', async () => {
    get.mockRejectedValueOnce(Object.assign(new Error('AniList está temporalmente no disponible.'), {
      body: '{"error":"AniList está temporalmente no disponible."}',
    }))

    const wrapper = mount(CounterpartRow, { props: { alId: 21, from: 'anime' } })
    await flushPromises()

    expect(wrapper.text()).toContain('AniList está temporalmente no disponible.')
    expect(wrapper.text()).not.toContain('{"error"')
  })
})
