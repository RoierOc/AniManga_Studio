// @vitest-environment happy-dom
/* «Seguir viendo» apuntaba al episodio equivocado.
 *
 * Caso real reportado: te asomas un minuto al episodio 7, luego ves el 4 entero, y al salir la
 * tarjeta te ofrece el 7. La regla vieja era «el primer episodio con posición guardada y sin
 * ver», y la posición del 7 seguía ahí — no había NINGÚN dato para saber cuál de los dos era más
 * reciente: los mapas `positions`/`watched` no llevan hora y `last_watched_at` es de la serie
 * entera, no del episodio.
 *
 * La regla correcta, y la que se fija aquí: el ancla es el ÚLTIMO episodio que tocaste
 * (`last_ep`, que ahora escribe el backend en los dos caminos: visto y progreso parcial).
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAnimeStore } from './anime'

const ep = (num, extra = {}) => ({ num, ep_type: 'episode', in_local: true, watched: false, resume_pos: 0, ...extra })

function serie(extra = {}) {
  return {
    id: 's', title: 'Serie', last_watched_at: 1000,
    episodes: [ep(1, { watched: true }), ep(2, { watched: true }), ep(3, { watched: true }),
               ep(4), ep(5), ep(6), ep(7), ep(8)],
    ...extra,
  }
}

const conAnime = (a) => { const s = useAnimeStore(); s.library = [a]; return s.continueWatching[0]?.ep?.num }

beforeEach(() => {
  // El store de anime lee `localStorage` al construirse, sin guarda. Fuera del navegador no
  // existe, así que se le da uno vacío antes de instanciarlo.
  if (!globalThis.localStorage) globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  setActivePinia(createPinia())
})

describe('«Seguir viendo» se ancla en el último episodio tocado', () => {
  it('EL FALLO: asomarse al 7 y luego dejar el 4 a medias → ofrece el 4', () => {
    const a = serie({ last_ep: 4 })
    a.episodes[6].resume_pos = 60      // el minuto que viste del 7
    a.episodes[3].resume_pos = 900     // donde lo dejaste de verdad
    expect(conAnime(a)).toBe(4)
  })

  it('si terminas el 4, el siguiente es el 5 — no el 7 que tiene posición suelta', () => {
    const a = serie({ last_ep: 4 })
    a.episodes[3].watched = true
    a.episodes[6].resume_pos = 60
    expect(conAnime(a)).toBe(5)
  })

  it('lo normal sigue funcionando: un solo episodio a medias se reanuda', () => {
    const a = serie({ last_ep: 4 })
    a.episodes[3].resume_pos = 900
    expect(conAnime(a)).toBe(4)
  })

  it('sin `last_ep` (series de antes del campo) se mantiene el comportamiento anterior', () => {
    const a = serie()                  // sin last_ep
    a.episodes[3].resume_pos = 900
    expect(conAnime(a)).toBe(4)
  })

  it('el episodio anclado NO se ofrece si ya no está en disco', () => {
    // Liberar espacio borra el fichero pero no el progreso: ofrecer algo que no se puede
    // reproducir es peor que pasar al siguiente.
    const a = serie({ last_ep: 4 })
    a.episodes[3].resume_pos = 900
    a.episodes[3].in_local = false
    expect(conAnime(a)).toBe(5)
  })
})
