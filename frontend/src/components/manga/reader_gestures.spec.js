// @vitest-environment happy-dom
/* Los gestos del lector, que es donde chocan entre sí.
 *
 * Aquí vive lo más delicado de la app: un mismo ratón tiene que distinguir "pasar página" de
 * "arrastrar para mover", "ampliar" de "deshacer", y varios paneles compiten por Escape. Los 81
 * tests de store no llegan a nada de esto porque no hay DOM.
 *
 * Y esta clase de fallo YA se coló en producción: durante meses **un clic pasaba DOS páginas**,
 * porque el `mouseup` del contenedor y el `click` de la imagen navegaban los dos (`@click.stop`
 * detiene el click, no el mouseup, que ya había ocurrido). No lo encontró ningún test: lo
 * encontré leyendo el archivo por otra cosa. Estos casos son exactamente esa trampa.
 *
 * Correr:  cd frontend && pnpm test
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn(), sseState: { value: 'open' } }))
vi.mock('@/lib/manga', async (orig) => ({ ...(await orig()), pageUrl: (p) => `/${p}` }))

/* happy-dom sirve `document` pero no expone `localStorage` global; los stores lo leen al
 * construirse. Mismo apaño que en `stores/reader_spread.spec.js`. */
if (!globalThis.localStorage) {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k),
    clear: () => m.clear(),
  }
}

import Reader from './Reader.vue'
import { useMangaStore } from '@/stores/manga'

function abrirLector(paginas = 8, dir = 'ltr') {
  const store = useMangaStore()
  store.dir = dir
  store.reader = { kind: 'cbz', title: 'Prueba', chapter: null, cover: '' }
  store.pages = Array.from({ length: paginas }, (_, i) => `p${i}.jpg`)
  store.readerLoading = false
  store.mode = 'paged'
  store.spread = false
  store.compareMode = false
  store.page = 2
  store.zoom = 1
  const wrapper = mount(Reader, { attachTo: document.body })
  return { store, wrapper, paged: () => document.querySelector('.rd__paged') }
}

function raton(el, tipo, { x = 500, y = 400 } = {}) {
  el.dispatchEvent(new window.MouseEvent(tipo, { clientX: x, clientY: y, bubbles: true, cancelable: true }))
}

describe('gestos del lector', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  it('un clic en la mitad derecha pasa UNA página, no dos', async () => {
    const { store, paged } = abrirLector()
    const zona = paged()
    // el clic real llega a la imagen y burbujea: el contenedor ve mouseup Y click
    const img = document.querySelector('.rd__img') || zona
    raton(img, 'mousedown', { x: 900 })
    raton(img, 'mouseup', { x: 900 })
    img.dispatchEvent(new window.MouseEvent('click', { clientX: 900, clientY: 400, bubbles: true }))
    expect(store.page).toBe(3)
  })

  it('un clic en la mitad izquierda retrocede UNA página', async () => {
    const { store } = abrirLector()
    const img = document.querySelector('.rd__img')
    raton(img, 'mousedown', { x: 40 })
    raton(img, 'mouseup', { x: 40 })
    img.dispatchEvent(new window.MouseEvent('click', { clientX: 40, clientY: 400, bubbles: true }))
    expect(store.page).toBe(1)
  })

  it('en RTL las mitades se invierten (derecha = anterior)', async () => {
    const { store } = abrirLector(8, 'rtl')
    const img = document.querySelector('.rd__img')
    raton(img, 'mousedown', { x: 900 })
    raton(img, 'mouseup', { x: 900 })
    img.dispatchEvent(new window.MouseEvent('click', { clientX: 900, clientY: 400, bubbles: true }))
    expect(store.page).toBe(1)
  })

  it('arrastrar con zoom NO pasa de página', async () => {
    const { store, paged } = abrirLector()
    store.zoom = 2
    const zona = paged()
    raton(zona, 'mousedown', { x: 900 })
    raton(zona, 'mousemove', { x: 700 })
    raton(zona, 'mouseup', { x: 700 })
    expect(store.page).toBe(2)
  })

  it('la tecla M abre y cierra el mosaico', async () => {
    const { wrapper } = abrirLector()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'm' }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.rd__grid')).toBeTruthy()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'm' }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.rd__grid')).toBeFalsy()
  })

  it('con el mosaico abierto, Escape lo cierra ANTES que el lector', async () => {
    const { store, wrapper } = abrirLector()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'm' }))
    await wrapper.vm.$nextTick()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.rd__grid')).toBeFalsy()
    expect(store.reader).not.toBeNull()          // el lector sigue abierto
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(store.reader).toBeNull()
  })

  it('elegir una página en el mosaico salta a ella y lo cierra', async () => {
    const { store, wrapper } = abrirLector()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'm' }))
    await wrapper.vm.$nextTick()
    document.querySelectorAll('.rd__gcell')[6].dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(store.page).toBe(6)
    expect(document.querySelector('.rd__grid')).toBeFalsy()
  })

  it('el mosaico marca la actual y atenúa las ya leídas', async () => {
    const { wrapper } = abrirLector()
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'm' }))
    await wrapper.vm.$nextTick()
    const celdas = [...document.querySelectorAll('.rd__gcell')]
    expect(celdas.filter(c => c.classList.contains('is-current'))).toHaveLength(1)
    expect(celdas.filter(c => c.classList.contains('is-read'))).toHaveLength(2)   // 0 y 1
  })
})
