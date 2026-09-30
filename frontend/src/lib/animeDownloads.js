export function groupAnimeDownloads(rows) {
  const groups = new Map()
  for (const row of rows) {
    const title = row.anime?.title || row.r.title
    const key = JSON.stringify([row.anime?.id || title.toLowerCase(), row.r.season ?? null])
    if (!groups.has(key)) groups.set(key, { key, title, season: row.r.season,
      cover: row.cover || '', rows: [], speed: 0, active: 0 })
    const group = groups.get(key)
    group.rows.push(row)
    group.speed += Number(row.t.dlspeed) || 0
    if (row.t.progress < 100) group.active++
  }
  return [...groups.values()]
}
