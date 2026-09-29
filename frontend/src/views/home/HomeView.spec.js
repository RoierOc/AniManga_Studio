// @vitest-environment happy-dom
import { reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/stores/home', () => ({ useHomeStore: vi.fn(), _hace: () => '' }))
vi.mock('@/stores/ui', () => ({ useUiStore: vi.fn() }))
vi.mock('@/stores/anime', () => ({ useAnimeStore: vi.fn() }))

import HomeView from './HomeView.vue'
import { useHomeStore } from '@/stores/home'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'

let wrapper
let originalMatchMedia
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  if (originalMatchMedia) window.matchMedia = originalMatchMedia
  else delete window.matchMedia
  originalMatchMedia = null
  vi.useRealTimers()
})

function render(mode = 'anime', hero = null) {
  const home = reactive({
    heroCurrent: hero, heroItems: hero ? [hero] : [], heroIndex: 0,
    continuarReciente: [], emitidoHoy: [], capitulosNuevos: [], dejadoAMedias: [], recaps: {},
    novidadesError: '', loadError: '', isEmpty: !hero, loading: false,
    init: vi.fn(), nextHero: vi.fn(), resume: vi.fn(), openDetail: vi.fn(),
    cargarNovedades: vi.fn(), pedirRecap: vi.fn(),
  })
  const ui = reactive({ mode, tabs: { media: 'library' }, goto: vi.fn(), setModeHome: vi.fn() })
  const anime = reactive({ sub: 'library' })
  useHomeStore.mockReturnValue(home)
  useUiStore.mockReturnValue(ui)
  useAnimeStore.mockReturnValue(anime)
  wrapper = mount(HomeView, {
    global: { stubs: {
      Icon: true, ContinueRail: true, ContextMenu: true, ErrorState: true, Skeleton: true,
      EmptyState: { props: ['title', 'hint'], template: '<div class="empty-state"><p class="empty-state__title">{{ title }}</p><p>{{ hint }}</p><slot name="action" /></div>' },
    } },
  })
  return { home, ui, anime }
}

describe('Portada · navegación y carrusel accesibles', () => {
  it('ofrece destinos de Cine coherentes con el modo activo', async () => {
    const { ui } = render('cine')
    await wrapper.get('button').trigger('click')

    expect(ui.tabs.media).toBe('library')
    expect(ui.goto).toHaveBeenCalledWith('media')
  })

  it('ofrece explorar manga desde el estado vacío del modo anime', async () => {
    const { ui } = render('anime')
    await wrapper.findAll('button')[1].trigger('click')

    expect(ui.goto).toHaveBeenCalledWith('explore')
  })

  it('pausa la rotación mientras el foco de teclado permanece en el hero', async () => {
    vi.useFakeTimers()
    const { home } = render('anime', { key: 'hero-1', title: 'Serie', kind: 'anime', label: 'E1' })
    await wrapper.get('.hero').trigger('focusin')

    vi.advanceTimersByTime(9000)

    expect(home.nextHero).not.toHaveBeenCalled()
  })

  it('respeta la preferencia del sistema de reducir movimiento', () => {
    vi.useFakeTimers()
    originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const { home } = render('anime', { key: 'hero-1', title: 'Serie', kind: 'anime', label: 'E1' })

    vi.advanceTimersByTime(9000)

    expect(home.nextHero).not.toHaveBeenCalled()
  })
})
