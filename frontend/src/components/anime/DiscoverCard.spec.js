// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DiscoverCard from './DiscoverCard.vue'

const { animeStore } = vi.hoisted(() => ({
  animeStore: { openTorrents: vi.fn(), isInLibrary: () => false, addToLibrary: vi.fn() },
}))
vi.mock('@/stores/anime', () => ({ useAnimeStore: () => animeStore }))

beforeEach(() => vi.clearAllMocks())

describe('DiscoverCard · acción secundaria', () => {
  it('Enter en «Mi Anime» no abre además la búsqueda de torrents', async () => {
    const wrapper = mount(DiscoverCard, {
      props: { anime: { al_id: 7, title: 'Anime', cover: '' } },
      global: { stubs: { Icon: true } },
    })
    const add = wrapper.get('.sc__add')

    await add.trigger('keydown', { key: 'Enter' })
    expect(animeStore.openTorrents).not.toHaveBeenCalled()
    await add.trigger('click')

    expect(animeStore.addToLibrary).toHaveBeenCalledTimes(1)
    expect(animeStore.openTorrents).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
