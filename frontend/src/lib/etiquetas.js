/* Nombres legibles en ESPAÑOL para códigos que vienen de fuera (AniList, MangaDex).
 *
 * Sólo de PRESENTACIÓN. El valor original se conserva siempre: los géneros viajan de vuelta a
 * la API de AniList como clave de filtro (`?genre=Slice of Life`) y los códigos de idioma son
 * los de MangaDex — traducirlos en el backend rompería el filtrado y la selección de capítulos.
 * Por eso esto vive en el front y sólo se aplica al pintar.
 *
 * Lo que no esté en el mapa sale tal cual: una app en español con un género en inglés se lee
 * peor, pero un género que DESAPARECE es un bug. */

// La lista de géneros de AniList es cerrada (18 entradas), así que el mapa está completo.
const GENEROS = {
  'Action': 'Acción',
  'Adventure': 'Aventura',
  'Comedy': 'Comedia',
  'Drama': 'Drama',
  'Ecchi': 'Ecchi',
  'Fantasy': 'Fantasía',
  'Horror': 'Terror',
  'Mahou Shoujo': 'Chica mágica',
  'Mecha': 'Mecha',
  'Music': 'Música',
  'Mystery': 'Misterio',
  'Psychological': 'Psicológico',
  'Romance': 'Romance',
  'Sci-Fi': 'Ciencia ficción',
  'Slice of Life': 'Recuentos de la vida',
  'Sports': 'Deportes',
  'Supernatural': 'Sobrenatural',
  'Thriller': 'Suspense',
}

const IDIOMAS = {
  'es-la': 'Latino',
  'es': 'Español',
  'es-419': 'Latino',
  'es-es': 'Español',
  'es-mx': 'Latino',
  'en': 'Inglés',
  'ja': 'Japonés',
  'ko': 'Coreano',
  'zh': 'Chino',
  'zh-hk': 'Chino (HK)',
  'pt-br': 'Portugués (BR)',
  'pt': 'Portugués',
  'fr': 'Francés',
  'de': 'Alemán',
  'it': 'Italiano',
  'ru': 'Ruso',
  'ar': 'Árabe',
  'id': 'Indonesio',
  'th': 'Tailandés',
  'vi': 'Vietnamita',
  'pl': 'Polaco',
  'tr': 'Turco',
  'uk': 'Ucraniano',
}

export const genero = (g) => GENEROS[g] || g
export const generos = (lista, n) => (lista || []).slice(0, n ?? undefined).map(genero)
export const idioma = (l) => IDIOMAS[l] || IDIOMAS[String(l || '').toLowerCase()] || l
