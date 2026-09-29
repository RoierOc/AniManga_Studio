// @vitest-environment happy-dom
/* El calendario no puede llamar «vacío» a un fallo de AniList. */
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/anime', () => ({ useAnimeStore: vi.fn() }))

import Schedule from './Schedule.vue'
import { useAnimeStore } from '@/stores/anime'

const EmptyStub = {
  template: '<div class="schedule-empty">{{ title }}</div>',
  props: ['title'],
}
const ErrorStub = {
  template: '<button class="schedule-error" @click="$emit(\'retry\')">{{ title }} {{ detail }}</button>',
  props: ['title', 'detail'],
  emits: ['retry'],
}

function render(overrides = {}) {
  const store = reactive({
    library: [], airing: {}, seasonalPopular: [], seasonal: [], seasonalLoading: false,
    seasonalError: '', loadSeasonal: vi.fn(), loadAiring: vi.fn(),
    openDetail: vi.fn(), openPreview: vi.fn(),
    ...overrides,
  })
  useAnimeStore.mockReturnValue(store)
  const wrapper = mount(Schedule, {
    global: { stubs: { EmptyState: EmptyStub, ErrorState: ErrorStub, Icon: true } },
  })
  return { store, wrapper }
}

describe('estado de carga del calendario', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra el error de AniList y permite reintentar', async () => {
    const { store, wrapper } = render({ seasonalError: 'AniList no responde' })

    expect(wrapper.find('.schedule-error').text()).toContain('No se pudo cargar el calendario.')
    expect(wrapper.find('.schedule-error').text()).toContain('AniList no responde')
    expect(wrapper.find('.schedule-empty').exists()).toBe(false)

    await wrapper.find('.schedule-error').trigger('click')
    expect(store.loadSeasonal).toHaveBeenCalledTimes(2) // onMounted + el reintento
    wrapper.unmount()
  })

  it('mantiene el estado vacío cuando la respuesta sí fue correcta', () => {
    const { wrapper } = render()

    expect(wrapper.find('.schedule-empty').exists()).toBe(true)
    expect(wrapper.find('.schedule-error').exists()).toBe(false)
    wrapper.unmount()
  })

  it('muestra el fallo de emisiones de la biblioteca aunque Temporada haya respondido', () => {
    const { store, wrapper } = render({ airingError: 'AniList caído' })

    expect(wrapper.find('.schedule-error').text()).toContain('AniList caído')
    expect(wrapper.find('.schedule-empty').exists()).toBe(false)

    wrapper.unmount()
  })
})
