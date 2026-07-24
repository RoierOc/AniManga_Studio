'use strict';
/**
 * SkyNovels — proveedor NATIVO (API oficial), no un plugin scraper de LNReader.
 *
 * De los 16 plugins ES de LNReader hoy responden 2; SkyNovels es la fuente ES que el usuario usa
 * de verdad, y publica una API JSON limpia (`api.skynovels.net`) — más fiable que raspar HTML y,
 * de paso, permite NAVEGAR el catálogo entero (476 novelas) con rating/estado para descubrir.
 *
 * Implementa la MISMA interfaz que un plugin LNReader (`searchNovels/popularNovels/parseNovel/
 * parseChapter`) para que fluya por el sidecar sin tocar server.cjs, y usa el MISMO formato de
 * `path` que el plugin scraper (`novelas/{id}/{slug}/` y `…/{chapterId}/{chp_name}`), así las
 * entradas de biblioteca ya guardadas siguen funcionando. Añade `browse()` para el catálogo.
 *
 * Endpoints de la API (deducidos del propio plugin skynovels de LNReader):
 *   GET api/novels?page=N            → { novels:[…], total, page, limit }   (sin búsqueda server)
 *   GET api/novel/{id}/reading?&q    → { novel:[{ …volumes[].chapters[] }] }
 *   GET api/novel-chapter/{id}       → { chapter:[{ chp_content }] }
 *   img: api/get-image/{image}/novels/false
 */
const API = 'https://api.skynovels.net/api/';
const UA = 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Mobile';

async function _get(path) {
  const r = await fetch(API + path, { headers: { 'User-Agent': UA, 'Cache-Control': 'no-cache' } });
  if (!r.ok) throw new Error(`skynovels ${path} → HTTP ${r.status}`);
  return r.json();
}

function _cover(image) {
  return image ? `${API}get-image/${image}/novels/false` : '';
}
function _novelPath(n) {
  return `novelas/${n.id}/${n.nvl_name}/`;
}
function _slim(n) {
  return {
    name: n.nvl_title || n.nvl_name || '',
    path: _novelPath(n),
    cover: _cover(n.image),
    // extras que el navegador de descubrimiento aprovecha (search/popular los ignoran)
    rating: Number(n.nvl_rating) || 0,
    status: n.nvl_status || '',
    chapters: Number(n.nvl_chapters) || 0,
    views: Number(n.nvl_views_count) || 0,
  };
}

// El catálogo entero es pequeño (476 en 24 páginas) y la API NO tiene búsqueda de servidor: se
// cachea en memoria y se filtra/ordena localmente. TTL corto — las novedades importan poco aquí.
let _cache = null;
let _cacheAt = 0;
const _CACHE_TTL = 30 * 60 * 1000;

async function _catalog() {
  if (_cache && Date.now() - _cacheAt < _CACHE_TTL) return _cache;
  const first = await _get('novels?page=1');
  const limit = first.limit || 20;
  const total = first.total || (first.novels || []).length;
  const pages = Math.max(1, Math.ceil(total / limit));
  const rest = await Promise.all(
    Array.from({ length: pages - 1 }, (_, i) => _get(`novels?page=${i + 2}`).catch(() => ({ novels: [] }))),
  );
  const all = [first, ...rest].flatMap((d) => d.novels || []);
  _cache = all.map(_slim);
  _cacheAt = Date.now();
  return _cache;
}

function _norm(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}

const provider = {
  id: 'skynovels',
  name: 'SkyNovels',
  site: 'https://www.skynovels.net',
  lang: 'Español',
  version: '1.0.0-native',

  // Búsqueda real sobre TODO el catálogo (el plugin scraper sólo miraba la página 1).
  async searchNovels(query, _page = 1) {
    const q = _norm(query);
    if (!q) return [];
    const cat = await _catalog();
    return cat.filter((n) => _norm(n.name).includes(q)).slice(0, 30);
  },

  // "Populares" = catálogo ordenado por vistas. `page` pagina localmente (20 por tanda).
  async popularNovels(page = 1, _opts) {
    const cat = [...(await _catalog())].sort((a, b) => b.views - a.views);
    const per = 20;
    return cat.slice((page - 1) * per, page * per);
  },

  async parseNovel(path) {
    const id = String(path).split('/')[1];
    const data = await _get(`novel/${id}/reading?&q`);
    const n = (data.novel || [])[0] || {};
    const base = `novelas/${id}/${n.nvl_name}/`;
    const chapters = [];
    for (const vol of (n.volumes || [])) {
      for (const c of (vol.chapters || [])) {
        chapters.push({
          name: c.chp_index_title || c.chp_name || `Capítulo ${c.chp_number}`,
          // MISMO esquema que el plugin scraper: …/{chapterId}/{chp_name}
          path: `${base}${c.id}/${c.chp_name}`,
          chapterNumber: Number(c.chp_number) || null,
          releaseTime: c.updatedAt || c.createdAt || '',
        });
      }
    }
    return {
      name: n.nvl_title || n.nvl_name || '',
      path,
      cover: _cover(n.image),
      summary: (n.nvl_content || '').replace(/<[^>]+>/g, '').trim(),
      author: /^(none|n\/a|-)?$/i.test((n.nvl_writer || '').trim()) ? '' : n.nvl_writer,
      status: n.nvl_status || '',
      genres: (n.genres || []).map((g) => g.genre_name || g.name).filter(Boolean).join(', '),
      chapters,
    };
  },

  // Devuelve el HTML del capítulo; server.cjs lo sanea antes de salir.
  async parseChapter(path) {
    const chapterId = String(path).split('/')[3];
    const data = await _get(`novel-chapter/${chapterId}`);
    const c = (data.chapter || [])[0] || {};
    return c.chp_content || '';
  },

  // Descubrir: catálogo con rating/estado, ordenable. No es de la interfaz LNReader — lo usa el
  // endpoint /browse del sidecar.
  async browse({ page = 1, sort = 'views' } = {}) {
    const key = { views: 'views', rating: 'rating', chapters: 'chapters', title: 'title' }[sort] || 'views';
    const cat = [...(await _catalog())].sort((a, b) =>
      key === 'title' ? a.name.localeCompare(b.name) : (b[key] || 0) - (a[key] || 0));
    const per = 24;
    return { results: cat.slice((page - 1) * per, page * per), total: cat.length, page };
  },
};

module.exports = { provider };
