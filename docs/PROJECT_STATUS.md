# MangaJaNai - Proyecto Completo

## Resumen Ejecutivo

**MangaJaNai** es una aplicación web full-stack para gestionar biblioteca de manga local con integración a MangaDex. Permite:
- Buscar manga en MangaDex
- Seguidos (biblioteca MangaDex autenticada)
- Descargar capítulos desde MangaDex
- Upscalear imágenes usando modelos IA (MangaJaNai)
- Leer manga con interfaz responsive estilo MangaDex

---

## Stack Tecnológico

### Frontend
- **Vue.js 3** - Framework reactivo (via CDN)
- **HTML/CSS** vanilla con clases `md-*` estilo MangaDex
- **JAVASCRIPT**: `/root/workspace/manga-upscaler/static/js/app.js`

### Backend
- **Flask** (Python 3) - Servidor en puerto 5001
- **Torch + spandrel** - Upscaling de imágenes
- **API MangaDex** - Autenticación OAuth + biblioteca + búsqueda

### Modelos IA (Upscaling)
- Localización: `/root/workspace/MangaJaNai/models/`
- 2x: `2x_IllustrationJaNai_V2standard_FDAT_M_unshuffle_40k.safetensors`
- 4x: `4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors`

---

## Estructura de Archivos

```
/root/workspace/manga-upscaler/
├── src/
│   ├── app.py                    # Flask main (registra blueprints)
│   └── api/
│       ├── download.py          # Descarga capítulos desde MangaDex
│       ├── library.py         # Biblioteca local (archivos en ~/MangaLibrary)
│       ├── mangadex.py       # API MangaDex (auth, follows, search, chapters)
│       ├── reader.py        # Lector de capítulos locales
│       ├── upscale.py     # Upscaling con modelos MangaJaNai
│       ├── upscale_worker.py
│       ├── search.py     # Búsqueda (deprecated?)
│       └── status.py     # Estado de descargas/upscale
├── templates/
│   └── index.html           # Template principal (Vue.js)
├── static/
│   ├── js/
│   │   ├── app.js         # Vue app completa
│   │   └── vue.js       # Vue 3 CDN
│   └── css/
│       └── styles.css    # Estilos md-*
├── manga_cli.py             # CLI para descarga
└── ... (otros archivos de manga processing)

~/MangaLibrary/           # Directorio donde se descargan los capítulos
~/MangaLibrary_Upscaled/   # Directorio output del upscale
```

---

## Endpoints API

### Biblioteca Local (`/api/library`)
| Método | Ruta | Descripción |
|--------|-----|------------|
| GET | `/api/library` | Lista todos los manga descargados |
| GET | `/api/library/chapters/<title>` | Capítulos de un manga |

### MangaDex (`/api/mangadex`)
| Método | Ruta | Descripción |
|--------|-----|------------|
| GET | `/api/mangadex/library` | Biblioteca seguida (requires auth) |
| GET | `/api/mangadex/search?q=<query>` | Buscar manga |
| GET | `/api/mangadex/chapters/<manga_id>` | Capítulos de manga |
| POST | `/api/mangadex/follow/<manga_id>` | Seguir manga |
| POST | `/api/mangadex/unfollow/<manga_id>` | Dejar de seguir |
| GET | `/api/mangadex/local_library` | Biblioteca local JSON |
| POST | `/api/mangadex/local_library/add` | Agregar a biblioteca local |

### Download (`/api/download`)
| Método | Ruta | Descripción |
|--------|-----|------------|
| POST | `/api/download/download_chapter` | Descargar capítulo (title + chapter o chapterId) |
| GET | `/api/download/status/<task_id>` | Estado de descarga |
| DELETE | `/api/download/delete_manga` | Borrar manga descargado |

### Upscale (`/api/upscale`)
| Método | Ruta | Descripción |
|--------|-----|------------|
| POST | `/api/upscale/upscale_chapter` | Upscalear capítulo |
| GET | `/api/upscale/status/<task_id>` | Estado de upscale |

### Reader (`/api/reader`)
| Método | Ruta | Descripción |
|--------|-----|------------|
| POST | `/api/reader/read_chapter` | Cargar páginas de capítulo |

---

## Autenticación MangaDex

Las credenciales están hardcodeadas en `/root/workspace/manga-upscaler/src/api/mangadex.py`:

```python
CLIENT_ID = "REDACTED_ROTATE_REQUIRED"
CLIENT_SECRET = "REDACTED_ROTATE_REQUIRED"
USERNAME = "REDACTED_ROTATE_REQUIRED"
PASSWORD = "REDACTED_ROTATE_REQUIRED"
```

El token se cachea en memoria (`_token_cache`) y se refresh automáticamente.

---

## Problemas Conocidos y Estado Actual

### ✅ FUNCIONANDO
1. **UI estilo MangaDex** - Covers, grid, modal, reader
2. **Biblioteca local con covers** - Carga covers desde MangaDex (Promise.all paralelo)
3. **Seguidos MangaDex** - Carga correctamente (~40+ manga), covers visible
4. **Capítulo detail modal** - Muestra capítulos + idiomas + actions
5. **Búsqueda MangaDex** - Funciona correctamente
6. **autenticación MangaDex** - Token se obtiene y refresh

### ❌ PROBLEMAS ACTIVOS

#### 1. **Download retorna errores HTTP**
- **Síntoma**: Al hacer click en "Descargar" sale error 400/404/500
- **Causa raíz**: El endpoint `/api/download/download_chapter` requería `chapterId` obligatorio
- **Estado**: Se intentará fix - ahora busca chapterId desde MangaDex si no se provee
- **Código actual**: Líneas 55-100 en `download.py`

#### 2. **Polling de tasks no retorna estado correcto**
- **Síntoma**: El progress bar muestra starting pero no avanza
- **Causa**: El polling hace request a `/api/status/download/<taskId>` pero el taskId tiene formato `title_download_chapter` y el endpoint busca en `download_status` dict con key exacta - no encuentra nada

#### 3. **Upscale no funciona**
- **Síntoma**: Botón "Upscale" no hace nada visible
- **Causa**: El worker (`upscale_worker.py`) corre en thread separada pero no se monitoriza bien

#### 4. **isInLibrary muestra incorrecto**
- **Síntoma**: Siempre muestra "Agregar a biblioteca" aunque ya esté agregado
- **Causa**: El check computed (`isInLibrary`) busca en `localLibrary.value` o `mdLibrary.value` pero la data se carga async

#### 5. **Covers de biblioteca local no cargan**
- **Síntoma**: Muestran placeholder gris en vez de cover
- **Causa**: `loadLibrary` no está precargando las covers correctamente o la búsqueda en MangaDex falla silenciosamente

---

## Flujo de Datos

### Vista Biblioteca (local)
```
1. GET /api/library → lista folders en ~/MangaLibrary
2. Para cada manga, fetch('/api/mangadex/search?q=title') para obtener cover
3. Vue renderiza grid con covers
```

### Vista Explorar (MangaDex search)
```
1. User typing → debouncedSearch() (300ms delay)
2. GET /api/mangadex/search?q=query
3. Results renderiza grid
4. Click → openMdManga(manga) → fetch chapters
```

### Vista Seguidos (MangaDex library)
```
1. onMounted → loadMdLibrary() → GET /api/mangadex/library
2. Requires auth token → /api/mangadex/login implícito
3. Renderiza grid con mdLibrary
4. Click → openMdManga → fetch chapters
```

### Descargar capítulo
```
1. User click "Descargar"
2. downloadChapter(group) → POST /api/download/download_chapter
   - Body: { title, chapter, [chapterId] }
3. Backend:
   - Sin chapterId → busca en MangaDex API
   - GET /api/mangadex.org/at-home/server/{chapterId}
   - Descarga páginas a ~/MangaLibrary/{title}/
4. Polling start (500ms interval)
   - GET /api/download/status/{taskId}
   - Update progress bar
5. Complete → toast → reload chapters
```

### Upscale capítulo
```
1. User click "Upscale"
2. upscaleChapter(ch) → POST /api/upscale/upscale_chapter
3. Backend:
   - Copia de ~/MangaLibrary/{title}/  a ~/MangaLibrary_Upscaled/
   - Procesa imagen por imagen con modelo 2x
4. Polling similar al download
```

### Reader
```
1. User click "Leer"
2. readChapter(ch) → POST /api/reader/read_chapter
3. Backend:
   - Busca en ~/MangaLibrary_Upscaled/ o ~/MangaLibrary/
   - Lista imágenes ch0001_*.jpg/png
   - Retorna array de filenames
4. Vue renderiza slider con pages
```

---

## Detalles Técnicos Importantess

### Formato de archivos descargados
Los capítulos se guardan como:
```
~/MangaLibrary/{title}/ch0042_001.jpg
~/MangaLibrary/{title}/ch0042_002.jpg
...
```
- Prefijo `ch` + capítulo formateado a 4 dígitos + `_` + page number a 3 dígitos

### Nomenclatura task IDs
- Download: `{title}_download_{chapter}` (ej: `One_Piece_download_42`)
- Upscale: `{title}_ch{chapter}` (ej: `One_Piece_ch42`)

### Capítulo grouping
Los capítulos de MangaDex pueden tener múltiples variants (diferentes grupos de traducción). El frontend agrupa por `chapter` number y muestra selector de idioma.

### CORS
El servidor Flask corre en modo development sin CORS configurado.

---

## Variables de Entorno y Configuración

```python
MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")
LIBRARY_FILE = os.path.expanduser("~/MangaLibrary/local_library.json")
MODEL_PATH_2X = '/root/workspace/MangaJaNai/models/2x_...safetensors'
MODEL_PATH_4X = '/root/workspace/MangaJaNai/models/4x_...safetensors'
```

---

## CSS Classes (UI MangaDex-style)

El archivo `/root/workspace/manga-upscaler/static/css/styles.css` define:
- `.md-header`, `.md-header-inner`
- `.md-logo`, `.md-nav`, `.md-nav-link`
- `.md-search`, `.md-main`
- `.md-section`, `.md-section-header`, `.md-section-title`
- `.md-grid`, `.md-card`, `.md-cover-wrapper`, `.md-cover-placeholder`
- `.md-card-title`, `.md-card-meta`
- `.md-modal`, `.md-modal-overlay`, `.md-modal-close`
- `.md-modal-header`, `.md-modal-cover`, `.md-modal-info`
- `.md-chapters`, `.md-chapters-list`, `.md-chapter-row`
- `.md-lang-filter`, `.md-lang-btn`
- `.md-btn-primary`, `.md-btn-sm`, `.md-btn-added`
- `.md-reader`, `.md-reader-content`, `.md-reader-nav`
- `.md-badge`, `.md-status`
- `.md-progress-bar`, `.md-progress-fill`
- `.md-toast`, `.md-empty`, `.md-loading`, `.md-spinner`

---

## Cómo Ejecutar

```bash
cd /root/workspace/manga-upscaler
source .venv/bin/activate  # opcional
python src/app.py
# Server corre en http://localhost:5001
```

---

## Próximos Pasos Recomendados

### Alta Prioridad
1. [ ] Fix download endpoint - verificar que el fix de buscar chapterId funciona
2. [ ] Fix polling - el task ID no encuentra el status correctamente
3. [ ] Fix isInLibrary - hacer el computed reactive correctamente

### Media Prioridad
4. [ ] Fix covers de biblioteca local - asegurar que se precargan
5. [ ] Testing completo de flujo download → read
6. [ ] Testing completo de upscale

### Baja Prioridad
7. [ ] Agregar more languages soportados
8. [ ] Cache de covers client-side (localStorage)
9. [ ] Notificaciones push para downloads completados

---

## Referencias Rápidas

- MangaDex API: https://api.mangadex.org/docs/
- spandrel (model loading): https://github.com/Lykon/DreamSimulacra
- Vue 3 CDN: https://unpkg.com/vue@3/dist/vue.global.prod.js