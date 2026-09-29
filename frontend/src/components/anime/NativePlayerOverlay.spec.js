// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const { animeStore, nativeSend } = vi.hoisted(() => ({
  animeStore: {
    nativePlayer: null,
    nativeVol: 72,
    nativeSkipTarget: vi.fn(() => null),
    nativeSeek: vi.fn(),
    nativePause: vi.fn(),
    closeNative: vi.fn(),
    setNativeVol: vi.fn(),
    setNativeAudio: vi.fn(),
    setNativeSub: vi.fn(),
    setNative4kTier: vi.fn(),
    setNativeSpeed: vi.fn(),
    setNativeSubScale: vi.fn(),
    setNativeSubSync: vi.fn(),
    setNativeBright: vi.fn(),
    setNativeSat: vi.fn(),
    nativeSkipOp: vi.fn(),
    toggleNativeFullscreen: vi.fn(),
    playNative: vi.fn(),
  },
  nativeSend: vi.fn(),
}))

vi.mock('@/stores/anime', () => ({ useAnimeStore: () => animeStore }))
vi.mock('@/lib/nativeBridge', () => ({ send: nativeSend }))

import NativePlayerOverlay from './NativePlayerOverlay.vue'

let wrapper

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  animeStore.nativePlayer = {
    loading: false, paused: true, pos: 10, duration: 60,
    anime: { id: 7, title: 'Nisekoi', episodes: [{ num: 1, in_local: true }, { num: 2, in_local: true }] },
    ep: { num: 1 }, playlist: null, audioTracks: [], subTracks: [], aid: 1, sid: 0,
    isLive: false, yaHorneado: false, tier: 'off', speed: 1, fullscreen: false,
    subScale: 1, subSync: 0, bright: 1, sat: 1,
  }
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

describe('NativePlayerOverlay · accesibilidad de controles', () => {
  it('expone la línea temporal como slider y admite Home/End', async () => {
    wrapper = mount(NativePlayerOverlay, { global: { stubs: { Teleport: true, Icon: true } } })
    const slider = wrapper.get('[role="slider"]')

    expect(slider.attributes('aria-label')).toBe('Posición del episodio')
    expect(slider.attributes('aria-valuenow')).toBe('10')
    await slider.trigger('keydown', { key: 'Home' })
    expect(animeStore.nativeSeek).toHaveBeenCalledWith(0)
    await slider.trigger('keydown', { key: 'End' })
    expect(animeStore.nativeSeek).toHaveBeenLastCalledWith(60)
  })

  it('da un nombre accesible al control de volumen', () => {
    wrapper = mount(NativePlayerOverlay, { global: { stubs: { Teleport: true, Icon: true } } })
    expect(wrapper.get('input[type="range"]').attributes('aria-label')).toBe('Volumen')
  })
})
