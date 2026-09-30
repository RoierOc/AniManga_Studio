// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setImageRevisions } from '@/lib/img'

const { animeStore, upscaleStore } = vi.hoisted(() => ({
  animeStore: {
    epMeta: {}, epInfo: {}, epInfoOpen: null, subTasks: {}, subFetching: null,
    subKey: () => '', loadSkip: vi.fn(), play: vi.fn(), openTorrents: vi.fn(),
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
  vi.clearAllMocks()
  setImageRevisions([])
})

describe('EpisodeCard · imagen local no disponible', () => {
  it('expone la acción principal del episodio como botón con nombre accesible', async () => {
    wrapper = mount(EpisodeCard, {
      props: {
        anime: { id: '18897', title: 'Nisekoi' },
        ep: { num: 2, ep_type: 'episode', in_local: true },
        batch: { hasSelection: false, batchDone: false, hasBatch: false },
      },
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    const action = wrapper.get('button.ep__thumb')
    expect(action.attributes('aria-label')).toBe('Reproducir Nisekoi, episodio 2')
    await action.trigger('click')
    expect(animeStore.play).toHaveBeenCalledOnce()
  })

  it('anuncia y abre la búsqueda de torrents para un episodio ausente', async () => {
    wrapper = mount(EpisodeCard, {
      props: {
        anime: { id: '18897', title: 'Nisekoi' },
        ep: { num: 4, ep_type: 'episode', in_local: false },
        batch: { hasSelection: false, batchDone: false, hasBatch: false },
      },
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    const action = wrapper.get('button.ep__thumb')
    expect(action.attributes('aria-label')).toBe('Buscar torrent para Nisekoi, episodio 4')
    await action.trigger('click')
    expect(animeStore.openTorrents).toHaveBeenCalledOnce()
  })

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

  it('mantiene el still visible al volver a montar tras fallar el fotograma local', async () => {
    const id = 'nisekoi-return-card'
    const still = 'https://image.tmdb.org/t/p/original/nisekoi-return-e2.jpg'
    const props = {
      anime: { id, title: 'Nisekoi', cover_rev: 3 },
      ep: { num: 2, season: 1, episode_key: 's01e002', ep_type: 'episode', in_local: true },
      batch: { hasSelection: false, batchDone: false, hasBatch: false },
    }
    animeStore.epMeta = { [id]: { s01e002: { still } } }
    wrapper = mount(EpisodeCard, {
      props,
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    await wrapper.get('.ep__img').trigger('error')
    expect(decodeURIComponent(wrapper.get('.ep__img').attributes('src'))).toContain(still)
    await wrapper.get('.ep__img').trigger('load')
    wrapper.unmount()
    wrapper = mount(EpisodeCard, {
      props,
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    const returned = wrapper.get('.ep__img')
    expect(decodeURIComponent(returned.attributes('src'))).toContain(still)
    expect(returned.classes()).toContain('is-loaded')
  })

  it('conserva visible el still de una serie añadida desde AniList al volver a la vista', async () => {
    const id = 'anilist-return-card'
    const still = 'https://image.tmdb.org/t/p/original/anilist-return-e1.jpg'
    const props = {
      anime: { id, title: 'Serie añadida', cover_rev: 1 },
      ep: { num: 1, ep_type: 'episode', in_local: false },
      batch: { hasSelection: false, batchDone: false, hasBatch: false },
    }
    animeStore.epMeta = { [id]: { 1: { still } } }
    wrapper = mount(EpisodeCard, {
      props,
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })
    await wrapper.get('.ep__img').trigger('load')
    wrapper.unmount()
    wrapper = mount(EpisodeCard, {
      props,
      global: { stubs: { ContextMenu: true, Icon: true, Spinner: true, EpisodeMediaInfo: true } },
    })

    expect(wrapper.get('.ep__img').classes()).toContain('is-loaded')
  })
})
