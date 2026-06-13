# Project Handoff — Manga Upscaler Professional

> **Date:** 2026-05-30
> **Branch:** `working-frontend`
> **Primary goal:** Refactor frontend from Vue 3 CDN monolith to Vite + Vue SFC + Pinia while keeping backend and all functionality intact.

---

## 1. Project Purpose

**Manga Upscaler Professional** is a 100% local web application for downloading, reading, upscaling (AI 4K), and exporting manga. It integrates with Suwayomi (Tachiyomi/Mihon extensions proxy), MangaDex, qBittorrent (anime), Google Drive, WebDAV, and Manga-image-translator. It also includes a full Anime Studio (AniList search, Nyaa torrents, qBittorrent integration, local library with MPV player, Ollama/Gemini subtitles).

The user runs it on WSL2. The app is designed for heavy local use; uploads/backups to Drive/WebDAV are optional.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Vue 3 SPA (Vite) — frontend/dist/ served by Flask at /       │
│ Legacy UI at /legacy (static/js/app.js + templates/index.html)│
├──────────────────────────────────────────────────────────────┤
│ Flask (Waitress WSGI) — src/app.py :5101                     │
│ 15 Blueprints: library, search, download, upscale, reader,    │
│   status, mangadex, export, sources, drive, webdav, cbz,      │
│   anilist, anime, subtitle                                    │
├──────────────────────────────────────────────────────────────┤
│ Suwayomi :4567 (Java 21) — Tachiyomi/Mihon extensions proxy  │
│ qBittorrent :8080 (Windows app)                               │
│ Manga-image-translator :5003/5006                              │
├──────────────────────────────────────────────────────────────┤
│ Storage:                                                     │
│   /Manga_Upscaler_project/MangaLibrary/          (MANGA_DIR)  │
│   /Manga_Upscaler_project/MangaLibrary_Upscaled/ (UPSCALED)  │
│   /Manga_Upscaler_project/MangaJaNai/models/     (AI models) │
└──────────────────────────────────────────────────────────────┘
```

**Deployment:** `bash start_server.sh` → builds Vite if dist/ missing → starts Flask/Waitress on :5101
**Development:** `cd frontend && pnpm dev` → Vite HMR on :5173, proxies /api, /uploads, /sw.js to :5101

---

## 3. Frontend Structure

### 3.1 Design System — "Midnight Atelier"

- **Palette:** void `#070a12`, surface `#111726`, ink `#eaf0fb`, azure primary `#4d8dff`, cyan `#46e0d8` (live/active only), coral (warnings), gold (partial upscale), jade (success), violet (MangaDex). **NO ORANGE anywhere.**
- **Fonts:** Clash Display (titles/display), General Sans (body) via Fontshare, Noto Sans JP (manga), JetBrains Mono (monospace/HUD)
- **Tokens:** `frontend/src/styles/tokens.css` — all spacing, z-index, colors, easings

### 3.2 Component Tree

```
App.vue
├── Sidebar.vue           — Nav (Manga/Anime sections), collapsible
├── TopBar.vue            — Search, view title
├── Router (view-based)   — Selected by ui.currentView
│   ├── LibraryView.vue   — Grid of manga cards + filters/search
│   ├── MangaDexView.vue  — MD search, popular, followed, AniList top
│   ├── SourcesView.vue   — Suwayomi multi-source search + popular
│   ├── FollowedView.vue  — MD followed manga
│   ├── LocalView.vue     — Local CBZ/CBR files
│   └── PlaceholderView.vue
├── Modals (teleported to body):
│   ├── MangaModal.vue    — Library manga: chapters, upscale, export, manage
│   ├── MdDetailModal.vue — MangaDex detail: chapters, download, language filter
│   ├── SourceDetailModal.vue — Suwayomi source detail: chapters, add-to-library
│   ├── Reader.vue        — Full manga reader (paged/webtoon, zoom, compare, RTL/LTR)
│   ├── ShortcutsModal.vue
│   ├── anime/* modals
│   └── Anime Preview tooltip
├── TaskQueue.vue         — Global task queue (downloads, upscale, exports)
└── Toaster.vue           — Toast notifications
```

### 3.3 Pinia Stores

| Store | File | Lines | Purpose |
|---|---|---|---|
| `ui` | `stores/ui.js` | 72 | Current view, sidebar state, toasts, screen size |
| `manga` | `stores/manga.js` | 674 | Library modal, chapters, reader, upscale, SSE tracking, **download progress** |
| `mangadex` | `stores/mangadex.js` | 224 | MD search, popular, detail, chapters, local library, AniList scores |
| `sources` | `stores/sources.js` | 239 | Suwayomi sources list, multi-source search (SSE), popular by source, detail modal |
| `anime` | `stores/anime.js` | 715 | Anime search, library, torrents, downloads, seasonal, history, MPV |
| `cbz` | `stores/cbz.js` | 50 | Local CBZ/CBR file management |

### 3.4 Key Libraries

- **`lib/api.js`** — Thin fetch wrapper. `api.get(path)`, `api.post(path, body)`. In dev, Vite proxies to Flask. In prod, same-origin.
- **`lib/sse.js`** — Single shared EventSource to `/api/status/stream`. `onStatus(fn)` for aggregate downloads/upscale/exports data every 500ms. `onSSE(type, fn)` for event bus.
- **`lib/manga.js`** — `taskId(title, chapter, type)` → `sanitizedTitle_type_ch{chapter}` for matching SSE task IDs. Also `pageUrl()`, `formatChapter()`, `sanitizeTitleId()`.

---

## 4. Backend Structure

### 4.1 Blueprints (src/api/)

| File | Routes | Purpose |
|---|---|---|
| `library.py` | `/api/library` | Local manga library: list, chapters, covers, metadata, offline covers, chapter health, scan comparison variants |
| `reader.py` | `/api/reader` | Read chapters: `read_chapter`, `read_compare`, `random` |
| `mangadex.py` | `/api/mangadex` | MD search, popular, chapters, details, OAuth, follow, local_library CRUD |
| `sources.py` | `/api/sources` | Suwayomi proxy: list sources, search (single + SSE stream), popular (SSE stream), manga detail, chapters, save_to_library |
| `download.py` | `/api/download` | Download chapters (MD + source), download tasks with SSE progress |
| `upscale.py` | `/api/upscale` | AI 4K upscaling: chapter, range, all, models, eco mode, cancel, repair |
| `export.py` | `/api/export` | CBZ/CBR tomo export |
| `status.py` | `/api/status` | SSE stream aggregating downloads + upscales + exports every 500ms |
| `search.py` | `/api/search` | Global title search across library |
| `anilist.py` | `/api/anilist` | AniList API proxy: top manga, scores |
| `anime.py` | `/api/anime` | Anime search (AniList + Nyaa), torrents, library, downloads, history |
| `subtitle.py` | `/api/subtitle` | Subtitle tracks, translate (Ollama/Gemini), inject into MKV |
| `cbz.py` | `/api/cbz` | Local CBZ/CBR file browser |
| `drive.py` | `/api/drive` | Google Drive connect/disconnect/upload |
| `webdav.py` | `/api/webdav` | WebDAV mobile URL |

### 4.2 Runtime Config (`src/api/runtime.py`)

- `MANGA_DIR` / `UPSCALED_DIR` from env vars, defaults to `/Manga_Upscaler_project/MangaLibrary[_Upscaled]`
- `push_sse_event(type, **payload)` — event bus for real-time notifications
- `build_task_id(title, chapter, type)` → `sanitized_title_type_ch{normalizedChapter}`
- `normalize_chapter(ch)` — decimal normalization (e.g., "ch004.5" → "4.5", "one_shot" → "one_shot")

### 4.3 Task ID Format

Backend and frontend MUST produce the same task IDs for SSE matching:
```
{lowercase_alphanumeric_title}_{type}_ch{normalizedChapter}
```
Examples:
- `Amayo_no_Tsuki_download_ch7`
- `Umi_ga_Hashiru_End_Roll_upscale_ch3`

---

## 5. Data Directories

```
/Manga_Upscaler_project/
├── MangaLibrary/                    # Original downloaded manga
│   ├── <Manga Title>/
│   │   ├── ch0001_001.webp         # Downloaded pages (webp/png/jpg)
│   │   ├── ch0001_002.webp
│   │   ├── cover.jpg               # Cover image (optional)
│   │   ├── .source_meta.json       # Source tracking (if from Suwayomi)
│   │   └── _compare/               # Scanlation comparison variants
│   ├── local_library.json          # MangaDex local library tracking
│   └── .cover_cache.json           # Cover URL cache
├── MangaLibrary_Upscaled/          # 4K upscaled pages (always .jpg)
│   └── <Manga Title>/
│       └── ch0001_001.jpg          # Upscaled + original copies during processing
└── workspace/manga-upscaler/       # Application code
    ├── src/                        # Flask backend
    ├── frontend/                   # Vite SPA
    │   ├── src/
    │   │   ├── components/
    │   │   ├── stores/
    │   │   ├── views/
    │   │   ├── lib/
    │   │   ├── styles/
    │   │   └── assets/
    │   └── dist/                   # Built SPA (served by Flask)
    ├── templates/                  # Legacy templates (served at /legacy)
    ├── static/                     # Legacy static (js/app.js, css/styles.css, sw.js)
    └── start_server.sh             # Main launcher
```

---

## 6. Current Bugs & Issues

### 6.1 Download Progress Indicator — NOT WORKING

**Description:** When a user clicks "Descargar" on a chapter in MangaModal (source or MangaDex), the button should change to a circular progress indicator with real-time percentage. Currently, the API returns 200 and the download works (the task appears in SSE data), but the frontend UI does not update.

**What's implemented:**
- `manga.js` store has `dlTasks` state: `{ chapterKey: taskId }` — maps chapter number to SSE download task ID
- When download starts, `dlTasks[chapterKey]` is set immediately (before API call) with estimated task ID
- After API returns, `dlTasks[chapterKey]` is updated with real task ID from response
- SSE handler in `onStatus` checks `dlTasks` entries against `this.downloads[taskId]` and cleans up completed tasks, then calls `_refreshChapters()`
- `MangaModal.vue` template accesses `store.dlTasks[String(c.chapter)]` and `store.downloads[taskId]` directly for reactive tracking

**What works:**
- Backend SSE properly pushes download data with correct task IDs
- API returns correct `task_id` in response
- Task ID format matches between frontend `taskId()` and backend `build_task_id()`
- SSE handler successfully cleans up completed tasks from `dlTasks`

**Root cause hypothesis:** Vue reactivity not tracking changes inside function-call expressions in templates. The template directly accesses `store.downloads[store.dlTasks[String(c.chapter)]]` but this nested bracket access may not trigger re-renders when `downloads` changes. Previous attempt with a getter returning a function (`dlForChapter(c)`) had the same issue.

**Potential fix:** Use a computed property that returns a plain object/array of `{chapter, task}` pairs. Vue reliably tracks changes to plain data structures accessed in templates. Consider replacing `dlTasks` with a flat reactive array and using `v-for` / `find` patterns.

### 6.2 Upscale Progress — Same Pattern

Same reactivity issue applies to upscale progress. The `chapterTask` getter works for the chapter actions area (checking running upscales), but it uses a different pattern (getter returning function + checking `this.upscale`). This DOES work in the template. The download case should follow the same pattern more closely.

---

## 7. Decisions Already Made

| Decision | Rationale |
|---|---|
| 100% local, no cloud | User explicit: everything runs locally, Drive/WebDAV optional |
| `pnpm build` for production | Zero Node processes in production; Flask serves static dist/ |
| Backend UNTOUCHED (except app.py) | Refactor frontend only; backend is stable and feature-complete |
| Legacy at /legacy | Fallback while Vite is being built; both coexist |
| Midnight Atelier design | Blue palette, no orange (user eliminated it before) |
| Pinia over Vuex | Modern, TypeScript-friendly, simpler |
| SSE over WebSockets | Backend already uses SSE; lighter weight |
| Vue SFC + `<script setup>` | Standard, clean, no Options API overhead |
| No tanstack-query / swrv | Lightweight api.js wrapper is sufficient; no data-fetching library overhead |
| Fontshare for fonts | Self-hosted-like CDN, free, no Google Fonts privacy issues |
| mkvmerge for subtitle injection | ffmpeg strips MKV cue index; mkvmerge preserves it for MPV |
| GPU throttle 80ms, VRAM 50% | Leaves resources for MPV to run in parallel with upscaling |
| No torch.compile / cudnn.benchmark | Causes conflicts with the specific models used |

---

## 8. Rejected Solutions

| Attempt | Why rejected |
|---|---|
| `dlForChapter(c)` Pinia getter returning function | Getter caches the function, not the result. Function-call reactive reads not tracked by template |
| `chapterDownloadTask(c)` with SSE `progress`/`total` fields | Fields existed but not reactive through getter chain |
| Spinner-only (no ring) | User explicitly wants circular progress ring with percentage |
| Ring without percentage text | User wants visible progress percentage |
| `_dlChapters` with `finally` cleanup | Cleanup happens instantly (API returns before download completes) |
| SSE-based `task_id` extraction with substring | Task ID prefix extraction was correct but cleanup never fired due to reactivity issue |
| `api.post` not capturing `task_id` | API returns `task_id` in response; needs saving to track via SSE |

---

## 9. Open Investigations

1. **Download progress reactivity** — Need to find a pattern where Vue reliably tracks `downloads[taskId].progress` changes. Consider replacing `dlTasks` object with a flat `reactive(new Map())` or using a computed that returns a plain array of active download statuses.

2. **SSE reconnection** — When the SSE connection drops and reconnects, state recovers from the aggregate payload. However, the `manga.init()` is called once. If the page is open for hours, SSE may silently reconnect but the `statusBound` flag prevents re-binding.

3. **Task ID edge cases** — Titles with special characters (e.g., `é`, `ñ`, Japanese characters) get different sanitization from `sanitizeTitleId` (frontend) vs `sanitize_title_for_id` (backend). Both replace non-ASCII with underscores, but there may be mismatches for edge cases.

---

## 10. Features Not Yet Implemented (from legacy)

Based on `project_frontend_v2_coverage.md`, the following are marked as DONE:
- Manga library grid, modal, chapters, upscale, reader
- MangaDex search, popular, detail, follow, AniList scores
- Sources/Suwayomi search (SSE) + popular per source
- Local CBZ/CBR
- Anime Studio (search, torrents, library, seasonal, downloads, history, subtitles)
- Export/Tomo builder
- Sidebar, task queue, toaster
- Reader (paged/webtoon, zoom, fit, RTL/LTR, compare, scanlation compare)
- Google Drive + WebDAV
- Keyboard shortcuts
- Scan paths for anime

**NOT IMPLEMENTED:** Manga-image-translator integration from the `translate.py` blueprint.

---

## 11. Known Pitfalls

1. **UPSCALED_DIR contains both .jpg (upscaled) and .webp/.png (original copies):** During upscaling, the backend copies original files to UPSCALED_DIR then replaces them with .jpg output. Files that remain as .webp/.png indicate incomplete upscales. Deleting these files breaks the "partial" tracking in the chapter list. The `reader.py` chapter_pages function searches all 3 extensions in UPSCALED_DIR.

2. **Pinia + `Set` objects:** Pinia serializes state to JSON for devtools persistence. `new Set()` doesn't serialize — convert to `[]` and use `.includes()`/`.push()`/`.splice()` instead. The `sources.js` store originally used `new Set()` for `activeSources` which caused silent state loss.

3. **`image_count` vs `page_count`:** The backend library endpoint returns both. `image_count` counts all images. `page_count` filters to known extensions. Use `page_count` for chapter page counts.

4. **`chapter` vs `chapterNorm` vs `chapterNumber`:** Multiple representations of chapter numbers exist in the codebase. `normalize_chapter()` strips `ch` prefix and normalizes decimals. Frontend `formatChapter()` adds "Cap. " prefix for display. Always use normalized form for comparison.

5. **`ch.id` vs `ch._sourceId` vs `ch._mdChapterId`:** In `mergedChapters` (manga.js store), chapters from source have `_sourceId` (Suwayomi chapter ID), chapters from MangaDex have `_mdChapterId` (MD chapter UUID), and local chapters use `ch.id = null`. The download actions must use the correct ID field.

6. **`api.post` response parsing:** The API wrapper returns parsed JSON when `Content-Type: application/json`. All download/upscale endpoints return JSON with `task_id`. This `task_id` is required for SSE tracking.

7. **SSE 500 Error on curl:** The SSE stream endpoint (`/api/status/stream`) returns 500 on concurrent connections from curl because Waitress has limited threads. The browser's EventSource connection works fine. Do not test SSE with curl while the browser is connected.

---

## 12. Current State of MangaDex Integration

**Working:**
- Search (by title, with filters: rating, tags, language)
- Popular / Recent / Top Rated
- Followed library (with login)
- OAuth login flow
- Detail modal with chapters, language filter (flag emojis), download
- Add to library (`POST /api/mangadex/local_library/add` with `{manga: {...}}` — FIXED, was sending wrong payload)
- "En biblioteca" button state tracking via `localIds` in mangadex store
- MangaDex link button (opens mangadex.org in new tab, violet styled)
- AniList score badges
- Library view merges `local_library.json` entries with downloaded manga (shows MD-only → mdOnly cards)

**Fixed in this session:**
- `addLocal()` payload: was flat `{title, cover, ...}`, now `{manga: {id, title, cover, ...}}`
- Chapters defaulting to English: now shows all languages by default, with "Todos" option in dropdown
- MD link button in MdDetailModal (violet external-link)

**Working in Library MangaModal for MD manga:**
- MD chapters loaded alongside local chapters
- Language filter with flag emojis (matches MdDetailModal)
- MD chapter download button
- MangaDex link button when manga has `mdId`

---

## 13. Current State of Reader / Upscaler

**Reader (Reader.vue):**
- Paged mode: click left/right edges to navigate, arrow keys, scroll to zoom
- Webtoon mode: vertical scroll, track position
- Zoom: mouse wheel, pinch-to-zoom
- Fit modes: width, height, original
- RTL/LTR toggle
- Compare mode: drag slider to compare original vs 4K
- Scanlation compare mode: compare different scanlation groups side by side
- Chapter navigation: ghost zones at edges
- Scrubber with thumbnails
- Auto-hide bars
- Fullscreen
- Reading progress save/restore (localStorage)
- CBZ reader shares the same component

**Upscaler:**
- Per-chapter upscale: "Escalar a 4K" button in MangaModal
- Bulk upscale: "Escalar todo 4K" button
- Range upscale: from/to inputs
- Model selection (EULA, JaNai, DWTP, MangaJaNai 1200/1400)
- Eco mode toggle (throttles 80ms, leaves GPU for MPV)
- Repair partial upscales
- Cancel upscale
- Progress bar in chapter list during active upscale (this works via `chapterTask` getter)
- SSE-driven progress updates

---

## 14. Important Files

| File | Purpose |
|---|---|
| `src/app.py` | Flask entry point, blueprint registration, Vite/legacy routing |
| `src/api/runtime.py` | Shared config, SSE bus, task ID helpers |
| `src/api/reader.py` | Chapter reading (auto/upscaled/original source resolution) |
| `src/api/download.py` | Download orchestration, SSE progress |
| `src/api/upscale.py` | 4K upscaling pipeline |
| `src/api/sources.py` | Suwayomi GraphQL proxy (SSE search + popular) |
| `src/api/status.py` | SSE aggregate stream (/stream) |
| `frontend/src/lib/sse.js` | Frontend SSE singleton (shared EventSource) |
| `frontend/src/lib/api.js` | Fetch wrapper |
| `frontend/src/lib/manga.js` | Task ID helper, page URL helpers |
| `frontend/src/stores/manga.js` | **MOST IMPORTANT** — Manga library modal, chapters, reader, upscales, **download progress tracking** |
| `frontend/src/stores/mangadex.js` | MangaDex integration |
| `frontend/src/stores/sources.js` | Suwayomi source management |
| `frontend/src/components/manga/MangaModal.vue` | Main manga detail modal with chapter list |
| `frontend/src/components/manga/MdDetailModal.vue` | MangaDex detail modal |
| `frontend/src/components/manga/SourceDetailModal.vue` | Suwayomi source detail modal |
| `frontend/src/components/manga/Reader.vue` | Full manga reader |
| `frontend/src/views/LibraryView.vue` | Library grid with MD/local merge |
| `frontend/src/views/SourcesView.vue` | Multi-source search + popular |
| `frontend/src/components/layout/Sidebar.vue` | Sidebar navigation |

---

## 15. TODOs

- [ ] **Fix download progress reactivity** — Ring/percentage not updating in real-time despite correct SSE data flow
- [ ] **Fix upscale progress** — Same reactivity pattern as download for consistency
- [ ] **Manga-image-translator integration** — `translate.py` blueprint has no frontend UI yet
- [ ] **Verify task ID matching** for edge-case titles with Unicode characters
- [ ] **SSE reconnection hardening** — Ensure `manga.init()` or equivalent runs on reconnect
- [ ] **Clean up console.log statements** from `manga.js` `openDetail` and `sources.js` once debugging is complete

---

## 16. Session Summary (2026-05-30)

This session focused on three major areas:

1. **Sources/Populares** — Implemented multi-source selection, SSE-based popular loading, grouped search results by source. Added "Populares" button integrated into the search bar. Fixed Pinia `Set` → `Array` serialization bug.

2. **MangaDex fixes** — Fixed `addLocal` payload format, added MangaDex link button, fixed chapters defaulting to English-only filter, merged MD library entries into LibraryView, loaded MD chapters in MangaModal.

3. **Download progress indicator** — Multiple iterations attempting to show real-time download progress (ring/percentage) from SSE data. Backend and data flow verified correct. Reactivity pattern in Vue template not triggering re-renders when `store.downloads[taskId]` changes. This is the **primary unresolved issue**.
