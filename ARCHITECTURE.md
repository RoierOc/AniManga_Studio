# Architecture - MangaJaNai

> Current as of 2026-06-10. This describes the active Flask + Vite app in `workspace/manga-upscaler`.

## System Overview

```text
Browser
  |
  |  /, /assets, /api, /uploads, /sw.js
  v
Flask/Waitress :5101
  |
  +-- Vite SPA: frontend/dist at /
  +-- Legacy SPA: templates/index.html + static/js/app.js at /legacy
  +-- Flask blueprints: manga, anime, downloads, upscaling, export, integrations
  |
  +-- Local storage: MangaLibrary, MangaLibrary_Upscaled
  +-- GPU worker: PyTorch/spandrel CUDA queue
  +-- External local services: Suwayomi, qBittorrent, Ollama, manga-image-translator
```

The app is designed to run locally on WSL2. Production/local serving uses one Python process; Node is only used to build or run the Vite dev server.

## Frontend Architecture

The active frontend is `frontend/src`.

Entry:

- `main.js` creates Vue app and installs Pinia.
- `App.vue` mounts shell, view component, global modals, task queue, shortcuts, toasts.

Navigation:

- No Vue Router.
- `ui.currentView` selects a view component.
- `ui.js` manages browser history/session state.

Main view map:

```js
{
  library: LibraryView,
  mangadex: MangaDexView,
  followed: FollowedView,
  sources: SourcesView,
  local: LocalView,
  anime: AnimeStudio,
}
```

Domain stores:

- `ui` - navigation, layout, toasts, history.
- `manga` - local manga, modal, reader, upscaling, exports, Drive/WebDAV, MangaDex tomo tools.
- `mangadex` - MangaDex catalog/auth/followed/local library, AniList scores/top.
- `sources` - Suwayomi search/popular/detail/download state.
- `anime` - anime library/search/torrents/qBittorrent/playback/subtitles/scan paths/history.
- `cbz` - local CBZ/CBR volumes and pages.

Shared frontend contracts:

- `lib/api.js` wraps `fetch`. It throws on non-2xx and returns JSON/text.
- `lib/sse.js` owns one `EventSource('/api/status/stream')`.
- `lib/manga.js` mirrors backend task IDs and page URL construction.

## Backend Architecture

`src/app.py` is the Flask composition root.

It:

- Resolves/creates `MANGA_DIR` and `UPSCALED_DIR`.
- Registers all API blueprints.
- Starts Suwayomi opportunistically.
- Serves the Vite SPA from `frontend/dist`.
- Serves `/legacy`.
- Serves upload routes for original/upscaled/auto page images.
- Runs Waitress on `127.0.0.1:5101` when launched directly.

Blueprint boundaries:

| Blueprint | Prefix | Role |
|---|---|---|
| `library.py` | `/api/library` | Local manga folders, metadata, covers, chapter health, corrupt scan, compare variants |
| `reader.py` | `/api/reader` | Page list generation for manga and compare modes |
| `mangadex.py` | `/api/mangadex` | MangaDex auth/search/popular/chapter/read/follow/local-library/tomo data |
| `sources.py` | `/api/sources` | Suwayomi GraphQL proxy, multi-source search/popular, extension install, save to library |
| `download.py` | `/api/download` | MangaDex/source/compare downloads, cancellation, status |
| `upscale.py` | `/api/upscale` | AI upscaling, GPU worker, models, mode, repair, cancel |
| `export.py` | `/api/export` | CBZ/CBR tomo generation, preview, color-page detection, async export |
| `status.py` | `/api/status` | Aggregate status and SSE stream |
| `anime.py` | `/api/anime` | AniList/Nyaa/qBittorrent/MPV/anime library/history/scan paths |
| `subtitle.py` | `/api/subtitle` | Subtitle extraction/translation/injection/reinjection/cancel/status |
| `anilist.py` | `/api/anilist` | Manga AniList scores/top/genres |
| `cbz.py` | `/api/cbz` | Local CBZ/CBR browsing and page serving |
| `drive.py` | `/api/drive` | Google Drive auth/status/upload tomo |
| `webdav.py` | `/api/webdav` | WebDAV/mobile URLs and exported file handling |
| `search.py` | `/api/search` | Local/global search helper |

## Data Model And Storage

Primary storage roots:

```text
/Manga_Upscaler_project/MangaLibrary/
/Manga_Upscaler_project/MangaLibrary_Upscaled/
```

Manga title folder:

```text
<Title>/
  ch0001_001.webp
  ch0001_002.webp
  cover.jpg
  .source_meta.json
  _compare/
```

Upscaled title folder:

```text
<Title>/
  ch0001_001.jpg
  ch0001_002.jpg
```

Important: upscaled folders may also contain `.webp`/`.png` copies used by partial tracking. Do not clean them blindly.

Chapter naming:

- Integer chapter: `ch0007_001.jpg`
- Decimal chapter: `ch0007.5_001.jpg`
- One-shot sentinel: `ch0000_001.jpg`

`normalize_chapter()` and `_chapter_file_prefix()` are the central chapter-number contracts.

## Realtime And Task Status

Long-running work is thread-based.

Download flow:

```text
Frontend action
  -> POST /api/download/...
  -> backend creates task_id
  -> background thread downloads pages
  -> download_status updated
  -> /api/status/stream emits aggregate state
  -> frontend updates rings/task queue
```

Upscale flow:

```text
Frontend action
  -> POST /api/upscale/upscale_chapter or upscale_manga
  -> backend creates task_id
  -> chapter worker threads submit tiles to one GPU queue
  -> single GPU worker batches tiles
  -> status dict/file updated
  -> SSE/poll updates frontend
```

Status persistence:

- Downloads persist to `.download_status.json` under `MANGA_DIR`.
- Upscales persist to `.upscale_status.json` under `UPSCALED_DIR`.
- In-flight tasks are marked `interrupted` on app restart.

SSE payload:

```json
{
  "downloads": {"task_id": {"status": "...", "progress": 1, "total": 10}},
  "upscale": {"task_id": {"status": "...", "progress": 1, "total": 10}},
  "exports": {"task_id": {"status": "..."}},
  "events": [{"seq": 1, "type": "watched"}]
}
```

## GPU Worker Design

`upscale.py` owns one CUDA worker thread. This is intentional.

Reasons:

- Avoid multiple CUDA contexts.
- Batch tiles from multiple chapter threads into `GPU_BATCH_SIZE=8`.
- Keep VRAM bounded with `torch.cuda.set_per_process_memory_fraction`.
- Allow cancellation and status reporting at chapter level.

Current defaults:

```text
TILE_SIZE=256
TILE_OVERLAP=16
GPU_BATCH_SIZE=8
VRAM_LIMIT_PCT=74
GPU_THROTTLE_MS=0
torch.compile(m.forward, mode='default')
```

Eco mode is supposed to use:

```text
batch throttle = 80 ms
tile throttle = 8 ms
```

But current frontend calls use `{ eco: true/false }`, while backend routes read `mode`. This is a live contract bug.

## Integrations

MangaDex:

- Public catalog/search/popular/chapter APIs.
- OAuth/session token cached in memory.
- Local library JSON under `MANGA_DIR/local_library.json`.
- Known limitation: unavailable web chapters are not exposed via public API.

Suwayomi:

- GraphQL on `127.0.0.1:4567`.
- App proxies source list/search/popular/chapter pages.
- Source downloads normalize into the same local manga folder format.

AniList:

- Manga score badges/top lists through `anilist.py`.
- Anime search/seasonal/tags/recommendations/stacks through `anime.py`.

qBittorrent:

- Windows app at `localhost:8080`.
- Used for anime torrent download state and actions.

MPV:

- Launched from backend for anime playback.
- Backend tracks position/watched events and pushes SSE.

Subtitles:

- Extraction/translation/injection in `subtitle.py`.
- Injection must use mkvmerge/mkvpropedit, not ffmpeg copy.

Google Drive/WebDAV:

- Optional tomo export destinations.
- Drive auth through env/config.
- WebDAV exposes mobile-friendly access links.

Manga-image-translator:

- External service tree exists.
- Current active Flask app does not contain `src/api/translate.py`; integration state must be re-verified before frontend work.

## Source Of Truth Rules

When docs conflict:

1. Current source code wins.
2. Memory files explain why decisions were made.
3. `documentación técnica` is useful but contains stale sections.
4. `docs/PROJECT_STATUS.md` is historical and mostly legacy.

When changing behavior:

1. Preserve legacy functional parity unless the user explicitly asks to change it.
2. Keep visual improvements separate from logic changes.
3. Test critical titles from bug history: Amayo no Tsuki, Shimanami Tasogare, Pink Candy Kiss, Nijisanji one-shot/adult content cases.
4. Do not weaken GPU limits without asking.

