import { expect, it } from 'vitest'
import { animeWatchProgress } from './anime'

it('separa vistos de disponibles y excluye especiales y batches', () => {
  expect(animeWatchProgress({ total_episodes: 12, downloaded_count: 12, episodes: [
    { num: 0, watched: true }, { num: 1, watched: true },
    { num: 2, watched: false }, { num: 1, ep_type: 'special', watched: true },
  ] })).toEqual({ watched: 1, total: 12, available: 12, fraction: 1 / 12 })
  expect(animeWatchProgress({ episodes: [] }).fraction).toBe(0)
})
