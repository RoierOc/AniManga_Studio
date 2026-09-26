// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { api } from '@/lib/api'
import EpisodeMediaInfo from './EpisodeMediaInfo.vue'

vi.mock('@/lib/api', () => ({ api: { get: vi.fn() } }))

const ErrorStub = defineComponent({
  props: ['detail'],
  emits: ['retry'],
  template: '<div data-testid="error"><span>{{ detail }}</span><button @click="$emit(\'retry\')">Reintentar</button></div>',
})
const SpinnerStub = defineComponent({ template: '<span data-testid="spinner" />' })
let wrapper

function mountInfo() {
  wrapper = mount(EpisodeMediaInfo, {
    props: { animeId: '42', episodeKey: 's02e001', episodeTitle: 'La llegada' },
    attachTo: document.body,
    global: { stubs: { ErrorState: ErrorStub, Spinner: SpinnerStub, Icon: true } },
  })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('ficha técnica del episodio', () => {
  it('muestra carga y luego datos del medio resuelto por identidad', async () => {
    let finish
    api.get.mockReturnValue(new Promise(resolve => { finish = resolve }))
    mountInfo()

    expect(document.body.querySelector('[data-state="loading"]')).not.toBeNull()
    expect(api.get).toHaveBeenCalledWith('/api/anime/media_info/42/s02e001')

    finish({
      video: { codec: 'hevc', width: 1920, height: 1080 },
      duration_seconds: 60,
      size_bytes: 1024,
      audio_tracks: [{ codec: 'opus', language: 'jpn', title: 'Japonés', channels: 2 }],
      subtitle_tracks: [],
    })
    await flushPromises()

    expect(document.body.querySelector('[data-state="ready"]')).not.toBeNull()
    expect(document.body.textContent).toContain('1920 × 1080')
    expect(document.body.textContent).toContain('Japonés')
    expect(document.body.textContent).toContain('Sin pistas de subtítulos')
  })

  it('distingue un fallo de consulta y permite reintentar', async () => {
    api.get.mockRejectedValueOnce(new Error('No se pudo leer el vídeo'))
      .mockResolvedValueOnce({
        video: { codec: 'h264', width: 1280, height: 720 }, duration_seconds: 1,
        size_bytes: 1, audio_tracks: [], subtitle_tracks: [],
      })
    mountInfo()
    await flushPromises()

    expect(document.body.querySelector('[data-state="error"]')).not.toBeNull()
    expect(document.body.textContent).toContain('No se pudo leer el vídeo')
    document.body.querySelector('[data-testid="error"] button').click()
    await flushPromises()
    expect(document.body.querySelector('[data-state="ready"]')).not.toBeNull()
  })
})
