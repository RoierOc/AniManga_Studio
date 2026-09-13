/* `tareaActiva` decide DOS cosas a la vez: si se pinta el progreso y si se pintan los botones
 * (`v-if` / `v-else`). Si un estado terminal se colase como "activa", la fila del capítulo se
 * quedaría con una barra parada para siempre; si uno vivo se leyera como terminada, el progreso
 * no aparecería — que es justo el fallo que veníamos a arreglar. */
import { describe, it, expect } from 'vitest'
import { tareaActiva } from './manga'

const T = {
  viva: { status: 'downloading', progress: 3, total: 10 },
  arrancando: { status: 'starting' },
  hecha: { status: 'complete' },
  rota: { status: 'error' },
  ausente: { status: 'not_found' },
}

describe('tareaActiva', () => {
  it('una descarga en marcha está activa, y devuelve la tarea entera', () => {
    expect(tareaActiva(T, 'viva')).toBe(T.viva)
    expect(tareaActiva(T, 'arrancando')).toBe(T.arrancando)
  })

  it('terminada, fallida o inexistente NO están activas', () => {
    for (const k of ['hecha', 'rota', 'ausente']) expect(tareaActiva(T, k)).toBeNull()
  })

  it('sin id o sin mapa no revienta: simplemente no hay tarea', () => {
    expect(tareaActiva(T, '')).toBeNull()
    expect(tareaActiva(T, undefined)).toBeNull()
    expect(tareaActiva(null, 'viva')).toBeNull()
    expect(tareaActiva(T, 'no-existe')).toBeNull()
  })
})
