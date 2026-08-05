/* El motivo del fallo tiene que llegar al usuario.
 *
 * Antes, `api.post` fallaba con "400 BAD REQUEST — /api/roots/add" y el `{"error": …}` del backend
 * se descartaba: el usuario veía un código en vez de "esa carpeta ya está en la biblioteca". Como
 * todos los stores toastean `e.message`, esto se comía el motivo en TODA la app.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { api } from '@/lib/api'

function respond(status, body, ct = 'application/json') {
  global.fetch = vi.fn(async () => ({
    ok: false, status, statusText: 'BAD REQUEST',
    headers: { get: () => ct },
    text: async () => body,
  }))
}

afterEach(() => { vi.restoreAllMocks() })

describe('api — detalle del error', () => {
  it('usa el "error" del backend como mensaje', async () => {
    respond(400, JSON.stringify({ error: 'Esa carpeta ya está en la biblioteca' }))
    await expect(api.post('/api/roots/add', {})).rejects.toThrow('Esa carpeta ya está en la biblioteca')
  })

  it('conserva el código y el cuerpo crudo', async () => {
    respond(404, JSON.stringify({ error: 'No existe esa carpeta' }))
    const e = await api.post('/api/roots/remove', {}).catch(x => x)
    expect(e.status).toBe(404)
    expect(e.body).toContain('No existe')
  })

  it('sin JSON útil, cae al código HTTP en vez de a un mensaje vacío', async () => {
    respond(500, '<html>Internal Server Error</html>', 'text/html')
    await expect(api.get('/api/roots')).rejects.toThrow(/500 BAD REQUEST — \/api\/roots/)
  })
})
