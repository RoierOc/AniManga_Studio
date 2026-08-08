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
  /* TMDB (cine y series occidentales, vía Sonarr/Radarr) usa OTRO vocabulario: comparte ocho
     nombres con AniList y añade los suyos. Sin esto, filtrar «Ciencia ficción» en manga y
     «Science Fiction» en Cine parecen dos cosas distintas — y son la misma. */
  'Science Fiction': 'Ciencia ficción',
  'Sci-Fi & Fantasy': 'Ciencia ficción y fantasía',
  'Animation': 'Animación',
  'Crime': 'Crimen',
  'Documentary': 'Documental',
  'Family': 'Familiar',
  'History': 'Histórico',
  'War': 'Bélico',
  'War & Politics': 'Bélico y política',
  'Western': 'Wéstern',
  'TV Movie': 'Película de TV',
  'Kids': 'Infantil',
  'News': 'Noticias',
  'Reality': 'Telerrealidad',
  'Soap': 'Culebrón',
  'Talk': 'Late night',
  /* Géneros ESPECÍFICOS: no son `genres` de AniList sino etiquetas suyas, curadas en
     `api/genre_tags.py`. Los 18 géneros no distinguen un GL de cualquier otro romance; la
     etiqueta «Yuri» sí. Se pintan igual que un género porque para quien filtra son lo mismo. */
  'Yuri': 'Yuri (GL)',
  "Boys' Love": 'Boys’ Love (BL)',
  'Bara': 'Bara',
  'LGBTQ+ Themes': 'Temática LGBTQ+',
  'Josei': 'Josei',
  'Seinen': 'Seinen',
  'Shounen': 'Shōnen',
  'Shoujo': 'Shōjo',
  'Isekai': 'Isekai',
  'School': 'Escolar',
  'Historical': 'Histórico',
  'Military': 'Militar',
  'Magic': 'Magia',
  'Vampire': 'Vampiros',
  'Time Manipulation': 'Viajes en el tiempo',
  'Post-Apocalyptic': 'Postapocalíptico',
  'Survival': 'Supervivencia',
  'Revenge': 'Venganza',
  'Tragedy': 'Tragedia',
  'Iyashikei': 'Iyashikei',
  'Cooking': 'Cocina',
  'Idol': 'Ídolos',
  'Gyaru': 'Gyaru',
  'Detective': 'Detectives',
  'Super Power': 'Superpoderes',
  'Space': 'Espacio',
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
