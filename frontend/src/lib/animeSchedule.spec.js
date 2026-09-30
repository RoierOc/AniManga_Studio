import { expect, it } from 'vitest'
import { scheduleDays } from './animeSchedule'

it('separa dos lunes y conserva el orden cronológico por fecha local', () => {
  const now = new Date(2026, 8, 28, 10).getTime()
  const a = { at: new Date(2026, 8, 28, 12).getTime() / 1000 }
  const b = { at: new Date(2026, 9, 5, 12).getTime() / 1000 }
  const days = scheduleDays([b, a], now)
  expect(days).toHaveLength(8)
  expect(days[0].items).toEqual([a])
  expect(days[7].items).toEqual([b])
  expect(days[0].key).not.toBe(days[7].key)
})
