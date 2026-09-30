const dateKey = date => `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`

export function scheduleDays(entries, now) {
  const today = new Date(now)
  return Array.from({ length: 8 }, (_, offset) => {
    const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset)
    const key = dateKey(date)
    return { key, isToday: offset === 0,
      label: date.toLocaleDateString('es', { weekday: 'long', day: 'numeric', month: 'short' }),
      items: entries.filter(e => dateKey(new Date(e.at * 1000)) === key).sort((a, b) => a.at - b.at) }
  })
}
