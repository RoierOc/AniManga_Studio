/* Thin fetch wrapper around the existing Flask API.
   In dev, Vite proxies /api → http://127.0.0.1:5101 (see vite.config.js). */

/* El backend contesta los fallos como `{"error": "por qué"}` (roots, anime, download…).
 * Hasta 2026-08-03 ese texto se guardaba en `err.body` y se DESCARTABA: el mensaje del Error era
 * sólo "400 BAD REQUEST — /api/roots/add", que es justo lo que el usuario acabó pegando en el chat
 * porque la app no le decía nada. El motivo real era accionable ("esa carpeta ya está en la
 * biblioteca"). Todos los stores toastean `e?.message`, así que arreglarlo AQUÍ lo arregla en toda
 * la app en vez de en cada sitio. El código sigue en `err.status` y el cuerpo crudo en `err.body`.
 */
function httpError(res, text, path) {
  let detail = ''
  try { detail = (JSON.parse(text) || {}).error || '' } catch (_) { /* no era JSON: nos queda el código */ }
  const err = new Error(detail || `${res.status} ${res.statusText} — ${path}`)
  err.status = res.status
  err.body = text
  return err
}

async function request(path, { method = 'GET', body, signal, headers } = {}) {
  const opts = { method, signal, headers: { ...headers } }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (!res.ok) {
    throw httpError(res, await res.text().catch(() => ''), path)
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
      throw httpError(res, await res.text().catch(() => ''), p)
    }
    const ct = res.headers.get('content-type') || ''
    return ct.includes('application/json') ? res.json() : res.text()
  },
}
