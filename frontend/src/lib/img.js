/* Routes external cover/banner art (TMDB, AniList, MangaDex) through the
 * backend's disk cache (/api/img) instead of hitting the CDN — and the
 * browser's own disk cache — every time. Local/relative URLs, and any host
 * not in this allowlist (e.g. arbitrary Suwayomi extension covers), pass
 * through untouched — the backend only ever fetches/caches these same
 * hosts, so keeping the check here too avoids ever sending an unrelated
 * URL to our own /api/img endpoint. */
const ALLOWED_HOSTS = new Set(['image.tmdb.org', 's4.anilist.co', 'uploads.mangadex.org'])

export function imgProxy(url) {
  if (!url || !/^https?:\/\//.test(url)) return url
  try {
    if (!ALLOWED_HOSTS.has(new URL(url).hostname.toLowerCase())) return url
  } catch {
    return url
  }
  return `/api/img?u=${encodeURIComponent(url)}`
}
