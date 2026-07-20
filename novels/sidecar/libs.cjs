'use strict';
/**
 * Shims `@libs/*` que los plugins de LNReader esperan del host.
 *
 * Cada bundle de plugin es CommonJS y hace `require("@libs/fetch")`, `require("cheerio")`, etc.
 * (ver un bundle en plugins/v3.0.0/.js/...). Aquí reimplementamos los `@libs/*` mínimos —
 * son triviales: `fetchApi` es un `fetch` (nativo en Node ≥18) con cabeceras/User-Agent, y el
 * resto son constantes/enums. `cheerio`/`dayjs`/`htmlparser2` se resuelven como paquetes npm.
 *
 * Gancho Cloudflare: `fetchApi` acepta un resolutor externo (FlareSolverr, que el proyecto ya
 * tiene) vía `setCloudflareSolver` — algunos sitios de novelas están tras Cloudflare igual que
 * las fuentes de manga. Sin resolutor, se hace el fetch directo (muchos sitios van directos).
 */
const cheerio = require('cheerio');
const dayjs = require('dayjs');
const htmlparser2 = require('htmlparser2');

// User-Agent de navegador móvil, como hace LNReader (algunos sitios discriminan por UA).
const DEFAULT_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
  'Accept': '*/*',
  'Accept-Language': 'en-US,en;q=0.9',
};

let _cfSolver = null; // (url, init) => Promise<Response|null>  — opcional (FlareSolverr)
function setCloudflareSolver(fn) { _cfSolver = fn; }

function _mergeInit(init = {}) {
  const headers = { ...DEFAULT_HEADERS, ...(init.headers || {}) };
  return { ...init, headers };
}

async function fetchApi(url, init) {
  const merged = _mergeInit(init);
  const res = await fetch(url, merged);
  // Página de reto de Cloudflare (403/503 con marca) → reintenta vía el resolutor si lo hay.
  if (_cfSolver && (res.status === 403 || res.status === 503)) {
    const solved = await _cfSolver(url, merged).catch(() => null);
    if (solved) return solved;
  }
  return res;
}
async function fetchText(url, init) { return (await fetchApi(url, init)).text(); }
async function fetchFile(url, init) {
  try {
    const r = await fetchApi(url, init);
    if (!r.ok) return '';
    return Buffer.from(await r.arrayBuffer()).toString('base64');
  } catch { return ''; }
}

// Almacenamiento que algunos plugins usan (tokens de sesión, etc.). En memoria por proceso.
function makeStore() {
  const m = new Map();
  return {
    get: (k) => m.get(k),
    set: (k, v) => { m.set(k, v); },
    delete: (k) => m.delete(k),
    getAllKeys: () => [...m.keys()],
    clearAll: () => m.clear(),
  };
}

const NovelStatus = {
  Unknown: 'Unknown', Ongoing: 'Ongoing', Completed: 'Completed',
  Licensed: 'Licensed', PublishingFinished: 'Publishing Finished',
  Cancelled: 'Cancelled', OnHiatus: 'On Hiatus',
};

const FilterTypes = {
  TextInput: 'Text', Picker: 'Picker', CheckboxGroup: 'Checkbox',
  Switch: 'Switch', ExcludableCheckboxGroup: 'XCheckbox',
};

const LIBS = {
  '@libs/fetch': { fetchApi, fetchText, fetchFile, fetchProto: fetchApi },
  '@libs/storage': { storage: makeStore(), localStorage: makeStore(), sessionStorage: makeStore() },
  '@libs/novelStatus': { NovelStatus },
  '@libs/defaultCover': { defaultCover: 'https://placehold.co/400x600/1a1f2e/6b7280?text=Novela' },
  '@libs/filterInputs': { FilterTypes, FilterInputs: FilterTypes },
  '@libs/isAbsoluteUrl': { isUrlAbsolute: (u) => /^https?:\/\//i.test(u || '') },
  'cheerio': cheerio,
  'dayjs': dayjs,
  'htmlparser2': htmlparser2,
  'urlencode': { encode: encodeURIComponent, decode: decodeURIComponent },
};

module.exports = { LIBS, setCloudflareSolver, fetchApi, cheerio };
