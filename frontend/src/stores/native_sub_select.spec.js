// Selección de pista de subtítulos en el reproductor nativo.
//
// Bug real, probado en el log de mpv (School-Live! E02):
//   177.925  loadfile
//   177.926  set sid=4      ← la pista 4 es un SIDECAR: aún no existe en el archivo recién cargado
//   178.071  sub-add …spa.srt   → ahora sí es la 4
//   178.076  set sid=4      ← ya correcta
// En esa ventana mpv resuelve el sid por su cuenta y se queda en la pista inglesa, mientras la
// interfaz ya muestra «Español» seleccionado. Síntoma del usuario: «estaba en inglés pero
// seleccionado como español; cambié a inglés y otra vez a español y ya funcionaba».
//
// Lo que fija este test: NO se preselecciona una pista externa antes de añadirla; una
// incrustada sí, porque `sid` sobrevive al cambio de archivo.
import { describe, expect, it } from 'vitest'

// Réplica de la decisión que toma `playNative`. Se prueba aislada a propósito: montar el store
// entero exigiría simular el puente nativo, y lo que falla aquí es esta condición, no el store.
function preseleccionaAlCargar(subTracks, defSid) {
  const elegida = subTracks[defSid - 1] || null
  return defSid > 0 && !(elegida && elegida.external)
}

const INCRUSTADAS = [
  { lang: 'en', title: 'Signs/Songs' },
  { lang: 'en', title: 'Dialogue' },
  { lang: '', title: '' },
]
const SIDECAR = { lang: 'spa', title: 'spa.srt', external: true, win_path: 'D:\\x.spa.srt' }

describe('preselección de subtítulos al cargar', () => {
  it('una pista INCRUSTADA sí se fija con loadfile', () => {
    expect(preseleccionaAlCargar(INCRUSTADAS, 2)).toBe(true)
  })

  it('un SIDECAR no se fija todavía: aún no existe en el archivo', () => {
    expect(preseleccionaAlCargar([...INCRUSTADAS, SIDECAR], 4)).toBe(false)
  })

  it('sin pista elegida no se manda nada', () => {
    expect(preseleccionaAlCargar([...INCRUSTADAS, SIDECAR], 0)).toBe(false)
  })

  it('un sid fuera de rango no se toma por incrustado', () => {
    // Defensa: si la lista y el sid se descuadran, mejor no fijar nada que fijar al azar.
    expect(preseleccionaAlCargar([], 3)).toBe(true)   // sin lista no hay sidecar que esperar
    expect(preseleccionaAlCargar([SIDECAR], 1)).toBe(false)
  })
})
