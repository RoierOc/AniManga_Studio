// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setImageRevisions } from '@/lib/img'

const { animeStore } = vi.hoisted(() => ({
  animeStore: {
    epMeta: {}, epInfo: {}, epInfoOpen: null, subTasks: {}, subFetching: null,
    subKey: () => '', loadSkip: vi.fn(), play: vi.fn(),
  },
}))

vi.mock('@/stores/anime', () => ({ useAnimeStore: () => animeStore }))

import EpisodeRow from './EpisodeRow.vue'

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  animeStore.epMeta = {}
  animeStore.play.mockClear()
  setImageRevisions([])
})

function render() {
  const still = 'https://image.tmdb.org/t/p/original/nisekoi-e2.jpg'
  animeStore.epMeta = { 18897: { s01e002: { still } } }
  setImageRevisions([{ id: '18897', cover_rev: 3 }])
  wrapper = mount(EpisodeRow, {
    props: {
      anime: { id: '18897', title: 'Nisekoi', cover: 'https://image.tmdb.org/p.jpg', cover_rev: 3 },
      ep: { num: 2, season: 1, episode_key: 's01e002', ep_type: 'episode', in_local: true, has_thumb: true },
      batch: { hasSelection: false, batchDone: false, hasBatch: false },
    },
    global: { stubs: { Icon: true, Spinner: true, EpisodeMediaInfo: true } },
  })
  return still
}

describe('EpisodeRow · miniatura de episodio', () => {
  it('usa el still de TMDB si falla el fotograma local', async () => {
    const still = render()
    const image = wrapper.get('.eprow__img')

    expect(image.attributes('src')).toContain('/api/anime/thumb/18897/2')
    await image.trigger('error')

    expect(wrapper.get('.eprow__img').attributes('src')).toContain('/api/img?u=')
    expect(decodeURIComponent(wrapper.get('.eprow__img').attributes('src'))).toContain(still)

    await wrapper.get('.eprow__img').trigger('error')
    expect(wrapper.get('.eprow__img').element.style.display).toBe('none')
  })

  it('expone la acción de reproducir como botón accesible', async () => {
    render()
    const play = wrapper.get('.eprow__thumb')

    expect(play.element.tagName).toBe('BUTTON')
    expect(play.attributes('aria-label')).toMatch(/reproducir.*episodio 2/i)
    await play.trigger('click')
    expect(animeStore.play).toHaveBeenCalledTimes(1)
  })
})
