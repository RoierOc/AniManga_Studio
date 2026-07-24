'use strict';
/**
 * Carga plugins de LNReader (bundles CJS prebuild) y los ejecuta en Node.
 *
 * Los plugins se distribuyen como bundles JS ya compilados en la rama `plugins/v3.0.0` del repo
 * `lnreader-plugins`, indexados por `.dist/plugins.min.json` (así los consume la propia app). No
 * hay que compilar su TypeScript: se descarga el bundle y se ejecuta con un `require` que mapea
 * `@libs/*` a nuestros shims (ver libs.cjs) y deja pasar `cheerio`/`dayjs`.
 *
 * Cachés: el índice y los bundles en disco (rara vez cambian); los plugins ya evaluados, en
 * memoria por id. Un plugin expone { id, name, site, version, popularNovels, searchNovels,
 * parseNovel, parseChapter }.
 */
const fs = require('fs');
const path = require('path');
const { LIBS } = require('./libs.cjs');
const { provider: skynovels } = require('./skynovels.cjs');

// Proveedores NATIVOS (API oficial) que SUPLANTAN al plugin scraper del mismo id: más fiables y
// con navegación de catálogo. Ver skynovels.cjs. `getPlugin`/`listPlugins` los prefieren.
const NATIVE = { [skynovels.id]: skynovels };

const REPO = process.env.LNREADER_REPO
  || 'https://raw.githubusercontent.com/lnreader/lnreader-plugins/plugins/v3.0.0';
const INDEX_URL = `${REPO}/.dist/plugins.min.json`;
const CACHE_DIR = process.env.NOVELS_CACHE || path.join(__dirname, '.cache');
const INDEX_TTL = 24 * 3600 * 1000;      // el índice cambia poco
const BUNDLE_TTL = 7 * 24 * 3600 * 1000; // un bundle de plugin, aún menos

fs.mkdirSync(CACHE_DIR, { recursive: true });

function _cachePath(name) { return path.join(CACHE_DIR, name); }
function _fresh(p, ttl) { try { return Date.now() - fs.statSync(p).mtimeMs < ttl; } catch { return false; } }

async function _cachedText(url, cacheName, ttl) {
  const p = _cachePath(cacheName);
  if (_fresh(p, ttl)) return fs.readFileSync(p, 'utf8');
  const res = await fetch(url);
  if (!res.ok) {
    if (fs.existsSync(p)) return fs.readFileSync(p, 'utf8'); // sirve lo viejo antes que fallar
    throw new Error(`fetch ${url} → HTTP ${res.status}`);
  }
  const text = await res.text();
  fs.writeFileSync(p, text);
  return text;
}

let _index = null;
async function getIndex() {
  if (_index) return _index;
  _index = JSON.parse(await _cachedText(INDEX_URL, 'index.json', INDEX_TTL));
  return _index;
}

async function listPlugins(lang) {
  const idx = await getIndex();
  const slim = idx.map((p) => ({ id: p.id, name: p.name, lang: p.lang, site: p.site, version: p.version, iconUrl: p.iconUrl }));
  // Los nativos suplantan la entrada del índice del mismo id (o la añaden si no está).
  for (const p of Object.values(NATIVE)) {
    const row = { id: p.id, name: p.name, lang: p.lang, site: p.site, version: p.version, iconUrl: p.iconUrl || '', native: true };
    const i = slim.findIndex((x) => x.id === p.id);
    if (i >= 0) slim[i] = row; else slim.push(row);
  }
  return lang ? slim.filter((p) => (p.lang || '').toLowerCase() === lang.toLowerCase()) : slim;
}

const _loaded = new Map(); // id → plugin object

function _evalBundle(src) {
  // LÍMITE DE CONFIANZA (deliberado): esto EJECUTA el código del plugin. Es el mismo modelo que
  // Suwayomi/Tachiyomi (extensiones de la comunidad) y que la propia app LNReader. `src` NO es
  // entrada de usuario interpolada: es el BUNDLE completo del plugin, descargado del repo OFICIAL
  // `lnreader/lnreader-plugins` FIJADO al tag `plugins/v3.0.0` (ver REPO) y cacheado por id. No se
  // concatena nada del usuario dentro. El sidecar corre en localhost, aislado del backend Python.
  const module = { exports: {} };
  const require2 = (id) => (id in LIBS ? LIBS[id] : require(id));
  // eslint-disable-next-line no-new-func
  new Function('require', 'module', 'exports', src)(require2, module, module.exports);
  return module.exports.default || module.exports;
}

async function getPlugin(id) {
  if (NATIVE[id]) return NATIVE[id];   // el nativo gana al bundle scraper del mismo id
  if (_loaded.has(id)) return _loaded.get(id);
  const idx = await getIndex();
  const meta = idx.find((p) => p.id === id);
  if (!meta) throw new Error(`plugin desconocido: ${id}`);
  const safe = String(id).replace(/[^a-z0-9_-]/gi, '_');
  const src = await _cachedText(meta.url, `plugin_${safe}.js`, BUNDLE_TTL);
  const plugin = _evalBundle(src);
  _loaded.set(id, plugin);
  return plugin;
}

module.exports = { getIndex, listPlugins, getPlugin, REPO };
