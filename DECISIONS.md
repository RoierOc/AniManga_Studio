# Decisions - MangaJaNai

> Architectural and product decisions inferred from memory, docs, and source code.

## Product Decisions

### Local-first app

The app is intended to run locally on the user's machine. Cloud services are optional integrations, not the core runtime.

Consequences:

- Flask serves the production frontend directly.
- Vite dev server is development-only.
- Google Drive and WebDAV are optional export destinations.
- qBittorrent, MPV, Suwayomi, Ollama, and manga-image-translator are local services.

### Preserve legacy behavior during frontend migration

The Vite rewrite is primarily an architecture/design migration. The user explicitly wants the old behavior preserved unless a change is intentional.

Consequences:

- Compare new Vite logic against `static/js/app.js` and `templates/index.html` when behavior is unclear.
- Visual improvements are allowed.
- Backend behavior should not be changed just to fit the new UI unless there is a real bug.

### Keep `/legacy`

The old Vue CDN app remains at `/legacy`.

Reason:

- Fallback while Vite migration stabilizes.
- Reference implementation for behavior parity.

## Frontend Decisions

### Vite + Vue SFC + Pinia

Chosen over the old CDN monolith.

Reasons:

- Component boundaries are clearer.
- Pinia separates domains.
- Build output is static and can be served by Flask.

### No Vue Router

Current navigation is store-driven.

Reason:

- Existing app is view-state driven.
- Browser history is handled manually in `ui.js`.

Tradeoff:

- Deep-linking is limited unless manually implemented.
- History/session logic must be maintained carefully.

### No heavy data-fetching library

The app uses `lib/api.js` instead of TanStack Query/SWR.

Reason:

- Existing data flows are imperative and stateful.
- SSE and local task state are already custom.

Tradeoff:

- Cache invalidation and retries are manual.

### Design system: Midnight Atelier

Palette is deep blue/azure/cyan. Orange was intentionally removed.

Rules:

- Use existing tokens in `frontend/src/styles/tokens.css`.
- Do not reintroduce orange as a dominant accent.
- Keep UI dense and functional; this is an operational app, not a marketing page.

## Backend Decisions

### Flask blueprints by domain

Each domain is a separate blueprint under `src/api`.

Reason:

- The app is broad; blueprint boundaries limit coupling.

Tradeoff:

- Some shared concerns, especially task IDs and status, must be centralized in `runtime.py`.

### SSE over WebSockets

The app uses Server-Sent Events for status and events.

Reasons:

- One-way server-to-client updates are enough.
- Simpler than WebSockets.
- Existing status endpoint already emits aggregated snapshots.

### Threaded long-running tasks

Downloads, exports, subtitle jobs, and upscales are launched in Python threads.

Reasons:

- Simple local app model.
- Good enough for bounded local workloads.

Tradeoffs:

- Process restarts require status recovery.
- No durable external queue.
- Module-level state requires care.

## File And Task Decisions

### Chapter filename convention is strict

Use `ch####_###.<ext>` with decimal and one-shot handling.

Reason:

- Reader, downloader, upscaler, export, and health checks depend on naming.

### `one_shot` sentinel

MangaDex `chapter: null` is represented as `one_shot`, not `0`.

Reason:

- Prevents one-shots from showing as `Cap. 0`.
- Keeps matching explicit.

### Shared task ID format

Task IDs are:

```text
{sanitized_title}_{type}_ch{normalizedChapter}
```

Reason:

- Frontend can optimistically track progress before backend returns.

Tradeoff:

- Frontend/backend sanitization must remain identical.
- Backend-returned `task_id` should replace optimistic IDs when available.

## Upscaling Decisions

### Single CUDA worker

Only one GPU worker owns the CUDA context.

Reasons:

- Avoid multiple contexts and runaway VRAM.
- Batch tiles from many chapter threads.
- Keep cancellation/status manageable.

### Current performance defaults

Keep:

- `TILE_SIZE=256`
- `TILE_OVERLAP=16`
- `GPU_BATCH_SIZE=8`
- `VRAM_LIMIT_PCT=74`
- `torch.compile(m.forward, mode='default')`
- `cudnn.benchmark=True`

Reasons:

- Larger tiles were slower.
- `max-autotune` was slower on RTX 5070.
- `reduce-overhead`/CUDA graphs caused dynamic batch errors.
- Whole-model compile broke spandrel/RRDBNet behavior.
- VRAM must stay under the user's 10 GB maximum.

### Eco mode exists to preserve MPV usability

Eco mode adds GPU throttling.

Reason:

- User wants to keep watching videos while upscaling when needed.

Important:

- Current frontend/backend mode contract appears broken (`eco` vs `mode`); fix without changing the decision.

### Do not delete mixed files in `UPSCALED_DIR`

Reason:

- `.webp`/`.png` files can participate in partial tracking and reader fallback.

## MangaDex Decisions

### Public API is primary; scrape fallback is best-effort

Reason:

- MangaDex public API is stable enough for normal chapters.
- Web unavailable chapters are not consistently exposed by public endpoints.

Tradeoff:

- Exact web tomo distribution cannot always be guaranteed.
- Estimated cover-only volumes must be presented honestly.

### Content ratings include all when needed

Adult/suggestive content can be valid user-requested content.

Reason:

- Some MangaDex chapters disappeared until `contentRating[]` included all ratings.

## Anime Decisions

### MPV is external, not embedded

Backend launches MPV and tracks state.

Reason:

- Local playback quality and codec/subtitle support.

Tradeoff:

- Runtime control is limited compared with an embedded web player.
- Skip OP is implemented as start offset/pre-play behavior, not in-player overlay control.

### qBittorrent is local Windows app

The app controls it through Web API.

Reason:

- Fits WSL2 + Windows media setup.

### mkvmerge for subtitle injection

Use mkvmerge/mkvpropedit rather than ffmpeg copy.

Reason:

- ffmpeg can omit MKV cue indexes, causing MPV subtitle seeking/visibility failures.

## Documentation Decisions

### Current source wins over old docs

Old docs in `docs/PROJECT_STATUS.md` and some `documentación técnica` sections describe historical states.

Decision:

- Use code as source of truth.
- Use memory files for rationale.
- Update docs when code and memory disagree.

## Rejected Or Avoided Approaches

- Replacing the Vite production server with a Node process.
- Reintroducing the old CDN monolith as active UI.
- Using ffmpeg copy for MKV subtitle injection.
- Using `torch.compile()` on the whole model object.
- Using `max-autotune` as the default compile mode.
- Treating MangaDex cover-only volume distribution as exact data.
- Cleaning `UPSCALED_DIR` by extension without understanding partial tracking.
- Replacing SSE with WebSockets without a concrete bidirectional requirement.

