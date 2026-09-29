// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/anime', () => ({ useAnimeStore: vi.fn() }))

import Search from './Search.vue'
import { useAnimeStore } from '@/stores/anime'

function render(overrides = {}) {
  const store = reactive({
    torrentAnime: null, searchQuery: '', searchResults: [], searchError: '', searchLoading: false,
    searchCompleted: false, checkQbt: vi.fn(), searchAnime: vi.fn(), submitSearch: vi.fn(),
    onSearchInput: vi.fn(),
    ...overrides,
  })
  useAnimeStore.mockReturnValue(store)
  const wrapper = mount(Search, { global: { stubs: { Icon: true, ErrorState: true, Skeleton: true, DiscoverCard: true } } })
  return { store, wrapper }
}

describe('búsqueda Anime · cero coincidencias', () => {
  beforeEach(() => vi.clearAllMocks())

  it('distingue una búsqueda completada sin resultados de la vista inicial', () => {
    const { wrapper } = render({ searchQuery: 'Naruto perdido', searchCompleted: true })

    expect(wrapper.get('.empty-state__title').text()).toContain('Naruto perdido')
    expect(wrapper.get('.empty-state__action button').text()).toBe('Limpiar búsqueda')
    expect(wrapper.find('.hint').exists()).toBe(false)
    wrapper.unmount()
  })

  it('mantiene la invitación inicial antes de que la búsqueda termine', () => {
    const { wrapper } = render({ searchQuery: 'Naruto' })

    expect(wrapper.find('.hint').exists()).toBe(true)
    expect(wrapper.find('.empty-state').exists()).toBe(false)
    wrapper.unmount()
  })
})
