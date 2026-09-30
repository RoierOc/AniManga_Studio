import { expect, it } from 'vitest'
import { groupAnimeDownloads } from './animeDownloads'

it('agrupa por serie y temporada sin perder hashes ni sumar semillas como descargas', () => {
  const rows = [
    { anime: { id: 'a', title: 'Serie' }, r: { season: 1 }, t: { hash: '1', progress: 50, dlspeed: 100 } },
    { anime: { id: 'a', title: 'Serie' }, r: { season: 1 }, t: { hash: '2', progress: 100, dlspeed: 0 } },
    { anime: { id: 'a', title: 'Serie' }, r: { season: 2 }, t: { hash: '3', progress: 20, dlspeed: 50 } },
  ]
  const groups = groupAnimeDownloads(rows)
  expect(groups).toHaveLength(2)
  expect(groups[0]).toMatchObject({ speed: 100, active: 1 })
  expect(groups.flatMap(g => g.rows).map(f => f.t.hash)).toEqual(['1', '2', '3'])
})
