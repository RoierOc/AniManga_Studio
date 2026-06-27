/* Thin fetch wrapper around the existing Flask API.
   In dev, Vite proxies /api → http://127.0.0.1:5101 (see vite.config.js). */

async function request(path, { method = 'GET', body, signal, headers } = {}) {
  const opts = { method, signal, headers: { ...headers } }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    const err = new Error(`${res.status} ${res.statusText} — ${path}`)
    err.status = res.status
    err.body = text
    throw err
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get:  (p, o) => request(p, { ...o, method: 'GET' }),
  post: (p, body, o) => request(p, { ...o, method: 'POST', body }),
  del:  (p, o) => request(p, { ...o, method: 'DELETE' }),
  // Multipart upload (FormData) — bypasses request()'s JSON encoding so the
  // browser can set the correct multipart boundary itself.
  async upload(p, formData) {
    const res = await fetch(p, { method: 'POST', body: formData })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      const err = new Error(`${res.status} ${res.statusText} — ${p}`)
      err.status = res.status
      err.body = text
      throw err
    }
    const ct = res.headers.get('content-type') || ''
    return ct.includes('application/json') ? res.json() : res.text()
  },
}
