import { expect, it } from 'vitest'
import { hideEpisodeSpoilers } from './animeSpoilers'

it('protege solo episodios sin ver y permite revelarlos voluntariamente', () => {
  expect(hideEpisodeSpoilers(true, { watched: false }, false)).toBe(true)
  expect(hideEpisodeSpoilers(true, { watched: true }, false)).toBe(false)
  expect(hideEpisodeSpoilers(true, { watched: false }, true)).toBe(false)
  expect(hideEpisodeSpoilers(false, { watched: false }, false)).toBe(false)
})
