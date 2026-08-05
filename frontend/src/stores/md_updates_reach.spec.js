/* El aviso de «capítulo nuevo» aparecía y se borraba solo.
 *
 * Reportado en Amayo no Tsuki: sale el 50.2 y a los pocos milisegundos el aviso desaparece.
 *
 * Causa: `have` (el ALCANCE que el frontend manda al backend) se calculaba incluyendo
 * `mdChapters`, que es el catálogo REMOTO de MangaDex — justo aquello contra lo que se compara.
 * El backend marca nuevo lo que cumple `chn > reach`, con `reach = max(descargado, have)`, así que
 * en cuanto la lista de MangaDex terminaba de cargar con el 50.2 dentro, `have` pasaba a 50.2 y
 * `50.2 > 50.2` daba falso. El aviso se anulaba a sí mismo. El coalescing de 350 ms es lo que hacía
 * que se viera aparecer primero y desaparecer después.
 *
 * Matiz que el arreglo respeta: con una versión de MangaDex FIJADA, `mdChapters` contiene la lista
 * de TU fuente activa y entonces sí cuenta como alcance.
 */
import { describe, expect, it } from 'vitest'

// Réplica de la decisión de `fetchMdUpdates`. Aislada a propósito: montar el store entero pediría
// simular media docena de peticiones, y lo que falla es este cálculo.
function calcularHave({ chapters = [], sourceChapters = [], mdChapters = [], effectiveSource = null }) {
  const _num = (x) => { const n = parseFloat(x); return Number.isFinite(n) ? n : -1 }
  const mdEsMiFuente = effectiveSource?.kind === 'mangadex'
  return Math.max(-1,
    ...chapters.map(c => _num(c.chapter)),
    ...sourceChapters.map(c => _num(c.chapterNorm ?? c.chapterNumber)),
    ...(mdEsMiFuente ? mdChapters.map(c => _num(c.chapter)) : []))
}

const LOCAL = [{ chapter: '49' }, { chapter: '50' }]
const MD_CON_NUEVO = [{ chapter: '50' }, { chapter: '50.2' }]

describe('alcance (`have`) para el aviso de capítulos nuevos', () => {
  it('el catálogo REMOTO de MangaDex no cuenta como alcance (el bug)', () => {
    const have = calcularHave({ chapters: LOCAL, mdChapters: MD_CON_NUEVO })
    expect(have).toBe(50)          // no 50.2 → el 50.2 sigue siendo nuevo
  })

  it('con una versión de MangaDex FIJADA sí cuenta', () => {
    const have = calcularHave({
      chapters: LOCAL, mdChapters: MD_CON_NUEVO, effectiveSource: { kind: 'mangadex' },
    })
    expect(have).toBe(50.2)
  })

  it('la fuente activa (no MangaDex) sigue contando', () => {
    const have = calcularHave({
      chapters: LOCAL,
      sourceChapters: [{ chapterNumber: '250' }],
      mdChapters: MD_CON_NUEVO,
      effectiveSource: { kind: 'source' },
    })
    expect(have).toBe(250)         // tener 1-250 vía tu fuente NO debe marcar el 50.2 como nuevo
  })

  it('sin nada, alcance -1 (no inventa)', () => {
    expect(calcularHave({})).toBe(-1)
  })
})
