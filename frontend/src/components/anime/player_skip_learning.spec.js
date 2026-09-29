// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { animeStore } = vi.hoisted(() => ({
  animeStore: {
    player: null,
    skipTimes: {},
    playerNext: () => null,
    openPlayer: vi.fn(),
    closePlayer: vi.fn(),
    setPlayerMode: vi.fn(),
    play: vi.fn(),
    loadSkip: vi.fn(),
  },
}))

vi.mock('hls.js', () => ({ default: class Hls {} }))
vi.mock('jassub', () => ({ default: class JASSUB {} }))
vi.mock('jassub/dist/worker/worker.js?worker&url', () => ({ default: '/worker.js' }))
vi.mock('jassub/dist/wasm/jassub-worker.wasm?url', () => ({ default: '/worker.wasm' }))
vi.mock('jassub/dist/wasm/jassub-worker-modern.wasm?url', () => ({ default: '/worker-modern.wasm' }))
vi.mock('@/stores/anime', () => ({ useAnimeStore: () => animeStore }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ fullscreen: false }) }))
vi.mock('@/lib/anime4k', () => ({
  Anime4KRenderer: class {
    static supported() { return false }
    stop() {}
  },
  A4K_MODES: [],
}))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

const saved = new Map()
globalThis.localStorage = {
  getItem: key => saved.get(key) ?? null,
  setItem: (key, value) => saved.set(key, String(value)),
  removeItem: key => saved.delete(key),
  clear: () => saved.clear(),
}
Object.defineProperty(globalThis.navigator, 'sendBeacon', { configurable: true, value: () => true })
const { default: PlayerOverlay } = await import('./PlayerOverlay.vue')

describe('salto manual del reproductor web', () => {
  beforeEach(() => {
    localStorage.clear()
    animeStore.player = {
      anime: { id: 'serie-prueba', title: 'Serie de prueba', episodes: [] },
      ep: { num: 1, title: 'Episodio 1', ep_type: 'regular' },
      loading: false,
      sess: null,
    }
  })

  it('conserva el botón fijo sin guardar aprendizaje por serie', async () => {
    const wrapper = mount(PlayerOverlay, { global: { stubs: { Teleport: true, Icon: true, Spinner: true } } })

    const button = wrapper.find('.wp__skipop')
    expect(button.exists()).toBe(true)
    await button.trigger('click')
    expect(wrapper.find('.wp__seekbub').text()).toContain('88 s')
    expect(localStorage.getItem('anime-op-manual')).toBeNull()

    wrapper.unmount()
  })
})
