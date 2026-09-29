/* Arrastrar la barra del reproductor del todo a la izquierda cerraba el episodio.
 *
 * mpv interpreta un seek ABSOLUTO NEGATIVO como «desde el final». Al soltar en el borde
 * izquierdo, `seekTo` calculaba una fracción ligeramente negativa y salía `pos = -0.16`.
 * MEDIDO en el log de mpv:
 *     Run command: seek, args=[target="-0.160215", flags="absolute"]
 *     queuing seek to 1471.327785   →   EOF reached.
 * Es decir: saltaba al final del episodio, se disparaba el fin de reproducción, se cerraba el
 * reproductor y aparecía «¿ver siguiente capítulo?».
 *
 * El mismo agujero lo tenía el teclado: un salto de −10 s en el segundo 2 da negativo igual.
 * Por eso la guarda vive en `nativeSeek`, por donde pasan TODOS los saltos.
 */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const enviados = []
vi.mock('@/lib/api', () => ({
  api: { get: vi.fn().mockResolvedValue({}), post: vi.fn().mockResolvedValue({}), del: vi.fn().mockResolvedValue({}) },
}))
vi.mock('@/lib/sse', () => ({ onSSE: vi.fn(), onStatus: vi.fn() }))
vi.mock('@/lib/nativeBridge', () => ({
  isNative: () => true,
  send: (tipo, datos) => enviados.push({ tipo, ...datos }),
  onMessage: vi.fn(),
}))
if (typeof globalThis.localStorage === 'undefined') {
  const m = new Map()
  globalThis.localStorage = {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear(),
  }
}

import { useAnimeStore } from './anime'

function store(duration = 1471.5) {
  const s = useAnimeStore()
  s.nativePlayer = { duration, pos: 100, tier: 'off' }
  return s
}
const ultimoSeek = () => [...enviados].reverse().find(e => e.tipo === 'seek')

describe('nativeSeek acota la posición', () => {
  beforeEach(() => { setActivePinia(createPinia()); enviados.length = 0 })

  it('el borde izquierdo NUNCA manda un negativo (el bug)', () => {
    store().nativeSeek(-0.160215)
    expect(ultimoSeek().pos).toBe(0)
  })

  it('un salto atrás desde el principio tampoco', () => {
    const s = store()
    s.nativePlayer.pos = 2
    s.nativeSeek(2 - 10)
    expect(ultimoSeek().pos).toBe(0)
  })

  it('no se puede pasar del final', () => {
    store(1471.5).nativeSeek(5000)
    expect(ultimoSeek().pos).toBeLessThanOrEqual(1471)
  })

  it('una posición normal pasa intacta', () => {
    store().nativeSeek(600)
    expect(ultimoSeek().pos).toBe(600)
  })

  it('sin duración conocida sólo se corta por abajo', () => {
    store(0).nativeSeek(-5)
    expect(ultimoSeek().pos).toBe(0)
    enviados.length = 0
    store(0).nativeSeek(900)
    expect(ultimoSeek().pos).toBe(900)
  })

  it('Saltar OP no reaprende de una búsqueda posterior', () => {
    const key = 'anime-skipop:serie-prueba'
    localStorage.removeItem(key)
    const s = store()
    s.nativePlayer.anime = { id: 'serie-prueba' }
    s.nativePlayer.pos = 100

    s.nativeSkipOp()
    expect(ultimoSeek().pos).toBe(182)
    s.nativeSeek(162) // seek ordinario tras el salto, no debe recalibrarlo

    expect(localStorage.getItem(key)).toBeNull()
  })
})
