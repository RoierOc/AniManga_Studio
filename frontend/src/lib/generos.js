/* Filtrar TU estantería por género — las tres (manga, anime, cine).
 *
 * Hasta ahora el género sólo servía para DESCUBRIR lo que no tienes (Explorar y Temporada
 * filtran el catálogo de AniList); sobre lo que ya está en casa era decoración en la tarjeta.
 * Esto lo convierte en una forma de elegir qué leer/ver: «tengo 24 de acción, enséñamelos».
 *
 * Vive aquí y no en cada vista porque las tres estanterías traen ya el dato con la MISMA forma
 * (`item.genres`, lista de nombres) y comparten la barra (`ContentToolbar`), así que el
 * comportamiento tiene que ser uno solo. Los nombres se guardan como vienen (inglés) y se
 * traducen sólo al PINTAR: son la clave con la que AniList y TMDB hablan.
 */
import { genero } from '@/lib/etiquetas'

/* El género viaja DENTRO del mismo filtro que los estados y las etiquetas, con prefijo — el
 * patrón que ya usaban las etiquetas (`tag:`). Filtrar es UNA cosa: elegir un género suelta el
 * estado, igual que elegir una etiqueta. Dos ejes a la vez pedirían enseñar dos selecciones
 * activas y una forma de quitar cada una; con estanterías de 100-200 obras no compensa. */
export const GEN_PREFIX = 'gen:'

export const generoActivo = (filtro) =>
  String(filtro || '').startsWith(GEN_PREFIX) ? String(filtro).slice(GEN_PREFIX.length) : ''

export const filtroDeGenero = (g) => (g ? GEN_PREFIX + g : 'all')

/* Opciones para el desplegable, con CUÁNTAS obras tienes de cada género (es la mitad del valor:
 * dice de un vistazo de qué va tu biblioteca). Ordenadas por cantidad — buscas «qué veo hoy»,
 * y lo que más tienes es lo que más probable es que elijas; a igualdad, alfabético en español.
 *
 * Devuelve LISTA VACÍA con menos de dos géneros distintos: sin nada que elegir, el control no
 * ayuda y se esconde solo (biblioteca recién estrenada, o manga antes de que lleguen de AniList). */
export function opcionesGenero(items, get = (x) => x.genres) {
  const cuenta = new Map()
  for (const it of items || []) {
    for (const g of (get(it) || [])) {
      if (g) cuenta.set(g, (cuenta.get(g) || 0) + 1)
    }
  }
  if (cuenta.size < 2) return []
  const orden = [...cuenta.entries()]
    .sort((a, b) => b[1] - a[1] || genero(a[0]).localeCompare(genero(b[0]), 'es'))
  return [
    { value: '', label: 'Todos los géneros' },
    ...orden.map(([g, n]) => ({ value: g, label: genero(g), hint: String(n) })),
  ]
}

/* Coincidencia por nombre EXACTO, no por «incluye»: «Fantasy» no puede arrastrar a
 * «Sci-Fi & Fantasy», que es otro género y otro estante. */
export const conGenero = (items, g, get = (x) => x.genres) =>
  (g ? (items || []).filter(it => (get(it) || []).includes(g)) : (items || []))
