// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { rememberedRef, viewMemoryKey, viewPositions } from './viewMemory'
import { useUiStore } from '@/stores/ui'

it('conserva filtros al desmontar sin compartirlos entre vistas', () => {
  const search = rememberedRef('test:library:search', '')
  search.value = 'Nisekoi'
  expect(rememberedRef('test:library:search', '').value).toBe('Nisekoi')
  expect(rememberedRef('test:downloads:search', '').value).toBe('')
})

it('separa pestañas, fichas y biblioteca para recordar posiciones independientes', () => {
  const library = { view: 'anime', sub: 'library' }
  expect(viewMemoryKey(library)).not.toBe(viewMemoryKey({ ...library, animeDetail: 18897 }))
  expect(viewMemoryKey(library)).not.toBe(viewMemoryKey({ ...library, sub: 'downloads' }))
  expect(viewMemoryKey({ view: 'library', tabs: { lib: 'local', exp: 'sources' } }))
    .toBe(viewMemoryKey({ view: 'library', tabs: { lib: 'local', exp: 'mangadex' } }))
})

it('regresar a una sección mediante navegación directa recupera su scroll', () => {
  setActivePinia(createPinia())
  const ui = useUiStore()
  ui.currentView = 'library'
  ui.initNav()
  const scroll = vi.spyOn(window, 'scrollY', 'get').mockReturnValue(420)
  ui._stampScroll()
  ui.currentView = 'activity'
  const restore = vi.spyOn(ui, '_restoreScroll').mockImplementation(() => {})
  ui.pushNav()
  scroll.mockReturnValue(0)
  ui.currentView = 'library'
  ui.pushNav()
  expect(restore).toHaveBeenLastCalledWith(420)
  scroll.mockRestore()
})

it('restaura el foco de la tarjeta sin mover el scroll y cancela destinos abandonados', () => {
  setActivePinia(createPinia())
  const ui = useUiStore()
  ui.currentView = 'library'
  const card = document.createElement('button')
  card.dataset.gridKey = 'saved-card'
  document.body.append(card)
  viewPositions.set(viewMemoryKey(ui.snapshot()), { scroll: 0, card: 'saved-card' })
  let tick
  const frame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(fn => { tick = fn; return 1 })
  const focus = vi.spyOn(card, 'focus')
  const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  ui._restoreScroll(0)
  tick()
  expect(focus).toHaveBeenCalledWith({ preventScroll: true })
  scroll.mockClear()
  ui._restoreScroll(420)
  ui.currentView = 'activity'
  tick()
  expect(scroll).not.toHaveBeenCalled()
  card.remove()
  frame.mockRestore()
  scroll.mockRestore()
})
