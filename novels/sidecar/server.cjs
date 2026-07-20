'use strict';
/**
 * Sidecar de NOVELAS — "Suwayomi para novelas".
 *
 * Servicio HTTP local que corre los plugins de LNReader (ver loader.cjs) y expone buscar / listar
 * populares / metadatos+capítulos de una novela / texto de un capítulo. El backend Flask lo proxea
 * (blueprint `novels.py`), igual que `sources.py` proxea Suwayomi. Solo localhost.
 *
 * Endpoints (JSON):
 *   GET  /health
 *   GET  /plugins?lang=English
 *   GET  /search?pluginId=&q=&page=      → [{ name, path, cover }]
 *   GET  /popular?pluginId=&page=        → [{ name, path, cover }]
 *   POST /novel   { pluginId, path }     → { name, cover, summary, author, status, genres, chapters:[{name,path,chapterNumber,releaseTime}] }
 *   POST /chapter { pluginId, path }     → { html, text }
 */
const http = require('http');
const { load: cheerioLoad } = require('cheerio');
const sanitizeHtml = require('sanitize-html');
const { listPlugins, getPlugin } = require('./loader.cjs');
const { setCloudflareSolver } = require('./libs.cjs');

// Cloudflare: si el proyecto tiene FlareSolverr en marcha (env FLARESOLVERR_URL, el mismo
// que usan las fuentes de manga), los plugins lo usan como reintento ante 403/503.
const FS_URL = process.env.FLARESOLVERR_URL || '';
if (FS_URL) {
  setCloudflareSolver(async (url) => {
    const r = await fetch(`${FS_URL.replace(/\/$/, '')}/v1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: 'request.get', url, maxTimeout: 60000 }),
    });
    if (!r.ok) return null;
    const j = await r.json();
    const html = j && j.solution && j.solution.response;
    if (!html) return null;
    // Se devuelve una Response sintética: los plugins solo llaman .text()/.json().
    return new Response(html, { status: 200, headers: { 'Content-Type': 'text/html' } });
  });
  console.log(`[novels] FlareSolverr activo → ${FS_URL}`);
}

const PORT = Number(process.env.NOVELS_PORT || 4568);
const HOST = '127.0.0.1';

function send(res, code, body) {
  const data = JSON.stringify(body);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(data) });
  res.end(data);
}
function readJson(req) {
  return new Promise((resolve) => {
    let b = ''; req.on('data', (c) => { b += c; }); req.on('end', () => { try { resolve(JSON.parse(b || '{}')); } catch { resolve({}); } });
  });
}
// El HTML del capítulo acaba en un `v-html` del lector, así que se limpia AQUÍ, antes de salir
// del sidecar. Se usa `sanitize-html` con LISTA BLANCA en vez de un filtro propio: una lista
// NEGRA hecha a mano se esquiva (data:/vbscript:, <base href>, <svg>, entidades como
// `java&#9;script:`…), y el texto de una novela solo necesita este puñado de etiquetas.
const SANITIZE_OPTS = {
  allowedTags: ['p', 'br', 'hr', 'em', 'i', 'strong', 'b', 'u', 's', 'blockquote', 'span', 'div',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a', 'img', 'sup', 'sub', 'small', 'ruby', 'rt', 'rp'],
  allowedAttributes: { a: ['href', 'title'], img: ['src', 'alt', 'title'] },
  allowedSchemes: ['http', 'https', 'mailto'],   // fuera javascript:, data:, vbscript:
  disallowedTagsMode: 'discard',
};
function sanitize(html) {
  try { return sanitizeHtml(html || '', SANITIZE_OPTS); }
  catch { return ''; }
}

// Texto plano legible de un capítulo (para conteo/preview); el HTML se conserva para el lector.
function htmlToText(html) {
  try { const $ = cheerioLoad(html || ''); $('script,style').remove(); return $.text().replace(/\n{3,}/g, '\n\n').trim(); }
  catch { return ''; }
}

const server = http.createServer(async (req, res) => {
  try {
    const u = new URL(req.url, `http://${HOST}:${PORT}`);
    const q = u.searchParams;

    if (u.pathname === '/health') return send(res, 200, { ok: true, service: 'novels', port: PORT });

    if (u.pathname === '/plugins') return send(res, 200, await listPlugins(q.get('lang') || ''));

    if (u.pathname === '/search') {
      const plugin = await getPlugin(q.get('pluginId'));
      const items = await plugin.searchNovels(q.get('q') || '', Number(q.get('page') || 1));
      return send(res, 200, items || []);
    }

    // "Buscar para leer": busca el MISMO título en varios plugins a la vez y devuelve las
    // versiones encontradas (patrón "Ver versiones" del manga). En paralelo con timeout por
    // plugin: un sitio caído o lento no puede retrasar al resto ni tumbar la búsqueda.
    if (u.pathname === '/find' && req.method === 'POST') {
      const { q, pluginIds = [], timeoutMs = 12000 } = await readJson(req);
      if (!q) return send(res, 400, { error: 'falta q' });
      const one = async (id) => {
        const plugin = await getPlugin(id);
        const items = await plugin.searchNovels(q, 1);
        return (items || []).slice(0, 5).map((it) => ({ ...it, pluginId: id }));
      };
      const settled = await Promise.all(pluginIds.map((id) =>
        Promise.race([
          one(id),
          new Promise((r) => setTimeout(() => r({ _timeout: true }), timeoutMs)),
        ]).then(
          (v) => (v && v._timeout ? { id, status: 'timeout', items: [] } : { id, status: 'ok', items: v }),
          (e) => ({ id, status: 'error', error: String(e && e.message || e), items: [] }),
        )));
      return send(res, 200, {
        results: settled.flatMap((s) => s.items),
        sources: settled.map(({ id, status, error }) => ({ id, status, error })),
      });
    }

    if (u.pathname === '/popular') {
      const plugin = await getPlugin(q.get('pluginId'));
      const items = await plugin.popularNovels(Number(q.get('page') || 1), { showLatestNovels: false, filters: undefined });
      return send(res, 200, items || []);
    }

    if (u.pathname === '/novel' && req.method === 'POST') {
      const { pluginId, path } = await readJson(req);
      const plugin = await getPlugin(pluginId);
      const novel = await plugin.parseNovel(path);
      return send(res, 200, novel || {});
    }

    if (u.pathname === '/chapter' && req.method === 'POST') {
      const { pluginId, path } = await readJson(req);
      const plugin = await getPlugin(pluginId);
      const html = await plugin.parseChapter(path);
      return send(res, 200, { html: sanitize(html), text: htmlToText(html) });
    }

    return send(res, 404, { error: 'not found' });
  } catch (e) {
    return send(res, 500, { error: String(e && e.message || e) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[novels] sidecar escuchando en http://${HOST}:${PORT}`);
});
