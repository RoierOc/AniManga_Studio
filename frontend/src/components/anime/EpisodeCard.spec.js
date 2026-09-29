// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setImageRevisions } from '@/lib/img'

const { animeStore, upscaleStore } = vi.hoisted(() => ({
  animeStore: {
    epMeta: {}, epInfo: {}, epInfoOpen: null, subTasks: {}, subFetching: null,
    subKey: () => '', loadSkip: vi.fn(),
  },
  upscaleStore: { trabajoDe: () => null, disponible: false, calidades: [] },
}))

vi.mock('@/stores/anime', () => ({ useAnimeStore: () => animeStore }))
vi.mock('@/stores/animeUpscale', () => ({ useAnimeUpscaleStore: () => upscaleStore }))

import EpisodeCard from './EpisodeCard.vue'

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  animeStore.epMeta = {}
  setImageRevisions([])
})

describe('EpisodeCard · imagen local no disponible', () => {
  it('usa el still TMDB si falla el fotograma local', async () => {
    const still = 'https://image.tmdb.org/t/p/original/nisekoi-e2.jpg'
    animeStore.epMeta = { 18897: { s01e002: { still } } }
    setImageRevisions([{ id: '18897', cover_rev: 3 }])
    wrapper = mount(EpisodeCard, {
      props: {
        anime: { id: '18897', title: 'Nisekoi', cover: 'https://image.tmdb.org/p.jpg', cover_rev: 3 },
        ep: { num: 2, season: 1, episode_key: 's01e002', ep_type: 'episode', in_local: true },
        batch: { hasSelection: false, batchDone: false, hasBatch: false },
      },
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    const image = wrapper.get('.ep__img')
    expect(image.attributes('src')).toContain('/api/anime/thumb/18897/2')
    await image.trigger('error')

    expect(wrapper.get('.ep__img').attributes('src')).toContain('/api/img?u=')
    expect(decodeURIComponent(wrapper.get('.ep__img').attributes('src'))).toContain(still)

    await wrapper.get('.ep__img').trigger('error')
    expect(wrapper.get('.ep__img').element.style.display).toBe('none')
  })
})
