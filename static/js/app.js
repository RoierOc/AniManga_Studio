// Manga Upscaler Pro - Full MangaDex-Style Vue 3 App

const { createApp, ref, computed, watch, onMounted } = Vue;

// Initialize
const app = createApp({
    setup() {
        // Views
        const currentView = ref('library');
        
        // Data
        const library = ref([]);
        const localLibrary = ref([]);
        const mdLibrary = ref([]);
        const searchQuery = ref('');
        const searchResults = ref([]);
        const chapters = ref([]);
        const mdChapters = ref([]);
        const chapterStatus = ref({ downloaded: {}, upscaled: {} });
        
        // Modal
        const showModal = ref(false);
        const showReader = ref(false);
        const currentManga = ref(null);
        const currentMdManga = ref(null);
        const currentTitle = ref('');
        const currentCover = ref(null);
        
        // Reader
        const currentChapter = ref('');
        const currentPage = ref(0);
        const pages = ref([]);
        const isZoomed = ref(false);
        const readerMode = ref(localStorage.getItem('reader-mode') || 'paged');
        const fitMode = ref(localStorage.getItem('reader-fit') || 'width');
        const readingDir = ref(localStorage.getItem('reader-dir') || 'rtl');
        const readerZoom = ref(1.0);
        const panY = ref(0);
        const panX = ref(0);
        const readerBarsHidden = ref(false);
        let readerBarsTimer = null;
        // drag-to-pan state (not reactive — not needed in template)
        let dragActive = false;
        let dragOriginX = 0, dragOriginY = 0;
        let dragStartPanX = 0, dragStartPanY = 0;
        let dragHasMoved = false;

        // Compare mode (before/after upscale slider)
        const compareMode = ref(false);
        const compareX = ref(50);   // divider position 0-100 %
        const readerSource = ref(''); // 'original' | 'upscaled'
        let compareDragging = false;
        
        // Filters
        const selectedLang = ref('');
        
        // Tasks & Toast
        const activeTasks = ref({});
        const downloadedLang = ref({});  // tracks which language was downloaded per chapter key
        const taskQueueExpanded = ref(true);
        const toast = ref({ show: false, message: '', type: 'info' });

        // Modal tab state
        const currentModalTab = ref('chapters');

        // Export / Tomo state
        const exportVolumeName = ref('');
        const exportFormat = ref('cbz');
        const exportSelectedChapters = ref([]);
        const exportChapterSet = computed(() => new Set(exportSelectedChapters.value.map(c => normalizeChapter(c))));
        const exportBusy = ref(false);
        const exportPreview = ref({ pages: 0, size_mb: 0, upscaled_pages: 0, original_pages: 0 });
        const chapterHealth = ref([]);   // [{chapter, status, missing_upscaled, download_gaps}]
        let exportPreviewTimer = null;

        // Cover state
        const exportQuality = ref(92);           // JPEG quality for CBZ re-encoding (MozJPEG)
        const exportDownscaleHalf = ref(true);   // resize to ½ on export (tablet-friendly)

        const exportCoverMode = ref('none');   // 'none' | 'chapter' | 'upload' | 'mdex'
        const exportColorPages = ref([]);
        const exportColorPagesLoading = ref(false);
        const exportSelectedCover = ref(null); // { path, url, label }
        const exportUploadedCoverB64 = ref('');
        const exportUploadedCoverUrl = ref('');
        const exportExcludedPages = ref([]);   // filenames to exclude from CBZ/CBR

        // MangaDex volumes + covers
        const mdexVolumes = ref([]);           // [{ volume, label, chapters, count }]
        const mdexVolumesLoading = ref(false);
        const mdexCovers = ref([]);            // [{ id, volume, url512, url256, ... }]
        const mdexCoversLoading = ref(false);
        const mdexSelectedCover = ref(null);   // { url512, url256, volume }
        const mdexCoverLoadingId = ref(null);  // cover id being fetched as b64
        const mdexSearchQuery = ref('');
        const mdexSearchResults = ref([]);
        const mdexSearchLoading = ref(false);

        // Export task tracking
        const exportTasks = ref({});           // task_id → {status, title, progress, total, ...}
        
        // Google Drive
        const driveConfigured = ref(false);
        const driveConnected = ref(false);
        const driveEmail = ref('');
        const driveUploading = ref(false);
        const driveUploadResult = ref(null); // { name, link } after successful upload

        const checkDrive = async () => {
            try {
                const r = await fetch('/api/drive/status');
                const d = await r.json();
                driveConfigured.value = d.configured;
                driveConnected.value = d.connected;
                driveEmail.value = d.email || '';
            } catch (_) {}
        };

        const connectDrive = async () => {
            try {
                const r = await fetch('/api/drive/auth');
                const d = await r.json();
                if (d.error) { showToast(d.error, 'error'); return; }
                window.open(d.auth_url, '_blank', 'width=500,height=650');
                // Poll for connection
                let tries = 0;
                const poll = setInterval(async () => {
                    await checkDrive();
                    if (driveConnected.value || ++tries > 30) clearInterval(poll);
                }, 2000);
            } catch (e) { showToast('Error conectando Drive', 'error'); }
        };

        const disconnectDrive = async () => {
            await fetch('/api/drive/disconnect', { method: 'POST' });
            driveConnected.value = false;
            driveEmail.value = '';
        };

        const uploadToDrive = async () => {
            if (!driveConnected.value) { connectDrive(); return; }
            driveUploading.value = true;
            driveUploadResult.value = null;
            try {
                const payload = {
                    title: currentTitle.value,
                    chapters: exportSelectedChapters.value,
                    volume_name: exportVolumeName.value || currentTitle.value,
                    format: exportFormat.value,
                    quality: exportQuality.value,
                    downscale_half: exportDownscaleHalf.value,
                    cover_path: exportSelectedCover.value?.path || '',
                    cover_data: exportUploadedCoverB64.value || '',
                    exclude_pages: exportExcludedPages.value,
                };
                const r = await fetch('/api/drive/upload_tomo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const d = await r.json();
                if (d.error) { showToast('Error subiendo: ' + d.error, 'error'); return; }
                driveUploadResult.value = { name: d.name, link: d.link };
                showToast(`"${d.name}" subido a Google Drive`, 'success');
            } catch (e) {
                showToast('Error subiendo a Drive', 'error');
            } finally {
                driveUploading.value = false;
            }
        };

        // Sources / Suwayomi
        const sources = ref([]);
        const activeSource = ref(null);
        const sourceQuery = ref('');
        const sourceResults = ref([]);
        const sourcesLoading = ref(false);
        const suwayomiOnline = ref(false);
        const sourceHasNextPage = ref(false);
        const currentSourceContext = ref(null);
        const globalQuery = ref('');
        const globalResults = ref([]);   // [{source:{id,name,lang}, results:[...]}]
        const globalSearchLoading = ref(false);
        const searchMode = ref('global'); // 'global' | 'source'

        // Search debounce
        let searchTimeout = null;

        const normalizeChapter = (value) => {
            if (value === null || value === undefined) return '';
            let raw = String(value).trim().toLowerCase();
            if (!raw) return '';
            if (raw.startsWith('ch')) raw = raw.slice(2);
            const num = Number(raw);
            if (!Number.isNaN(num) && Number.isFinite(num)) {
                if (Number.isInteger(num)) return String(num);
                return String(num).replace(/\.0+$/, '');
            }
            const stripped = raw.replace(/^0+/, '');
            return stripped || '0';
        };

        const sanitizeTitleForId = (value) => {
            const text = (value || '').trim();
            const safe = text.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
            return safe || 'manga';
        };

        const canonicalTitle = (value) => (value || '').toString().trim().toLowerCase().replace(/[^a-z0-9]+/g, '');

        const buildTaskId = (title, chapter, type) => {
            return `${sanitizeTitleForId(title)}_${type}_ch${normalizeChapter(chapter)}`;
        };
        
        // Computed
        const stats = computed(() => {
            const all = chapters.value.length > 0 ? chapters.value : mdChapters.value;
            return {
                total: all.length,
                downloaded: Object.values(chapterStatus.value.downloaded).filter(Boolean).length,
                upscaled: Object.values(chapterStatus.value.upscaled).filter(v => v === true).length
            };
        });
        
        const currentCoverUrl = computed(() => {
            return currentCover.value || currentMdManga.value?.cover || currentManga.value?.cover || null;
        });

        const combinedLibrary = computed(() => {
            const output = [];

            // Local downloaded manga — split MangaDex vs Mihon by source_meta
            for (const manga of library.value || []) {
                if (!manga) continue;
                const title = (manga.name || manga.title || '').trim();
                if (!title) continue;
                const isMihon = !!(manga.source_meta?.sourceId);
                const card = {
                    key: isMihon ? `mihon:${title}` : `local:${manga.id || title}`,
                    title,
                    source: isMihon ? 'mihon' : 'local',
                    source_meta: manga.source_meta || null,
                    cover: manga.cachedCover || manga.cover || null,
                    chapter_count: manga.chapter_count || manga.page_count || 0,
                    upscaled: Number(manga.upscaled || 0),
                    downloaded: true,
                    localManga: manga,
                    mdManga: null,
                };
                output.push(card);
            }

            // MangaDex followed (not yet downloaded) — never merge with Mihon entries
            const localKeys = new Set(output.map(c => canonicalTitle(c.title) + ':' + c.source));
            for (const manga of localLibrary.value || []) {
                if (!manga) continue;
                const title = (manga.title || manga.name || '').trim();
                if (!title) continue;
                const ck = canonicalTitle(title) + ':local';
                // Merge cover into existing local entry if titles match and it's not Mihon
                const existing = output.find(c => c.source !== 'mihon' && canonicalTitle(c.title) === canonicalTitle(title));
                if (existing) {
                    if (!existing.cover && manga.cover) existing.cover = manga.cover;
                    if (!existing.mdManga && manga.id) existing.mdManga = { id: manga.id, title, cover: manga.cover, status: manga.status };
                    continue;
                }
                if (localKeys.has(ck)) continue;
                output.push({
                    key: `saved:${manga.id || title}`,
                    title,
                    source: 'mangadex',
                    cover: manga.cover || null,
                    chapter_count: 0,
                    upscaled: 0,
                    downloaded: false,
                    localManga: null,
                    mdManga: { id: manga.id, title, cover: manga.cover, status: manga.status || 'Guardado' },
                });
            }

            output.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
            return output;
        });
        
        const groupedChapters = computed(() => {
            const src = mdChapters.value.length > 0 ? mdChapters.value : chapters.value;
            if (!src?.length) return [];
            const groups = {};
            for (const ch of src) {
                if (!ch) continue;
                const key = ch.chapter || '0';
                if (!groups[key]) groups[key] = { chapter: ch.chapter, variants: [] };
                groups[key].variants.push(ch);
            }
            return Object.values(groups).sort((a, b) => (parseFloat(b.chapter) || 0) - (parseFloat(a.chapter) || 0));
        });
        
        const filteredChapters = computed(() => {
            if (!selectedLang.value) return groupedChapters.value;
            return groupedChapters.value.filter(g => g.variants.some(v => v.language === selectedLang.value));
        });

        // ── Reader computed ─────────────────────────────────────────────────────
        const readerChapterList = computed(() =>
            [...filteredChapters.value].sort((a,b) => (parseFloat(a.chapter)||0) - (parseFloat(b.chapter)||0))
        );
        const readerChapterIndex = computed(() =>
            readerChapterList.value.findIndex(g => normalizeChapter(g.chapter) === normalizeChapter(currentChapter.value))
        );
        const canGoPrevChapter = computed(() => readerChapterIndex.value > 0);
        const canGoNextChapter = computed(() => readerChapterIndex.value < readerChapterList.value.length - 1);

        // ── Reading progress (localStorage) ─────────────────────────────────────
        const PROGRESS_KEY = 'manga-progress-v1';
        const _progressStore = ref((() => { try { return JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}'); } catch { return {}; } })());
        const getProgressStore = () => _progressStore.value;
        const _writeProgressStore = (s) => {
            _progressStore.value = s;
            localStorage.setItem(PROGRESS_KEY, JSON.stringify(s));
        };
        const saveReadingProgress = (title, chapter, page) => {
            const s = JSON.parse(JSON.stringify(getProgressStore())); const k = canonicalTitle(title);
            if (!s[k]) s[k] = { read: {} };
            s[k].lastChapter = String(chapter); s[k].lastPage = page;
            _writeProgressStore(s);
        };
        const markChapterRead = (title, chapter) => {
            const s = JSON.parse(JSON.stringify(getProgressStore())); const k = canonicalTitle(title);
            if (!s[k]) s[k] = { read: {} };
            s[k].read[normalizeChapter(chapter)] = true;
            _writeProgressStore(s);
        };
        const isChapterRead = (chapter) => {
            if (!currentTitle.value) return false;
            return !!(getProgressStore()[canonicalTitle(currentTitle.value)]?.read?.[normalizeChapter(chapter)]);
        };
        const getLastReadProgress = (title) => title ? (getProgressStore()[canonicalTitle(title)] || null) : null;
        const toggleChapterRead = (chapter) => {
            const s = JSON.parse(JSON.stringify(getProgressStore())); const k = canonicalTitle(currentTitle.value);
            if (!s[k]) s[k] = { read: {} };
            if (!s[k].read) s[k].read = {};
            const norm = normalizeChapter(chapter);
            if (s[k].read[norm]) delete s[k].read[norm];
            else s[k].read[norm] = true;
            _writeProgressStore(s);
        };

        const seriesReadCounts = computed(() => {
            const s = getProgressStore();
            const out = {};
            for (const [k, data] of Object.entries(s)) {
                out[k] = Object.keys(data.read || {}).length;
            }
            return out;
        });

        const availableLangs = computed(() => {
            const langs = new Set();
            for (const ch of mdChapters.value) if (ch.language) langs.add(ch.language);
            return Array.from(langs).sort();
        });
        
        const currentPageUrl = computed(() => {
            if (!pages.value.length) return '';
            return '/uploads/' + encodeURIComponent(pages.value[currentPage.value]);
        });

        // Compare mode — explicit original / upscaled URLs for the same page path
        const currentPageOrigUrl = computed(() => {
            if (!pages.value.length) return '';
            return '/uploads/original/' + encodeURIComponent(pages.value[currentPage.value]);
        });
        const currentPageUpUrl = computed(() => {
            if (!pages.value.length) return '';
            return '/uploads/upscaled/' + encodeURIComponent(pages.value[currentPage.value]);
        });
        const canCompare = computed(() =>
            readerSource.value === 'upscaled' && readerMode.value === 'paged'
        );
        
        // Load library WITH covers (PARALLEL for speed)
        const loadLibrary = async () => {
            try {
                const res = await fetch('/api/library');
                const data = await res.json();
                library.value = data || [];
                
                // Load ALL covers in PARALLEL (fast!)
                if (data.length > 0) {
                    await Promise.all(data.map(m => loadCoverForManga(m)));
                }
            } catch (e) { 
                library.value = []; 
            }
        };
        const langFlags = {
            'en': '🇬🇧', 'es': '🇪🇸', 'es-la': '🇲🇽', 'ja': '🇯🇵', 'ko': '🇰🇷', 'zh': '🇨🇳',
            'id': '🇮🇩', 'ru': '🇷🇺', 'pt-br': '🇧🇷', 'fr': '🇫🇷', 'de': '🇩🇪',
            'it': '🇮🇹', 'pl': '🇵🇱', 'tr': '🇹🇷', 'vi': '🇻🇳', 'th': '🇹🇭',
            'uk': '🇺🇦', 'cs': '🇨🇿', 'sv': '🇸🇪', 'hu': '🇭🇺', 'ro': '🇷🇴'
        };
        const langNames = {
            'en': 'English', 'es': 'Español', 'es-la': 'Español (Latino)', 'ja': '日本語', 'ko': '한국어', 'zh': '中文',
            'id': 'Bahasa Indonesia', 'ru': 'Русский', 'pt-br': 'Português (Brasil)', 'fr': 'Français', 'de': 'Deutsch',
            'it': 'Italiano', 'pl': 'Polski', 'tr': 'Türkçe', 'vi': 'Tiếng Việt', 'th': 'ไทย',
            'uk': 'Українська', 'cs': 'Čeština', 'sv': 'Svenska', 'hu': 'Magyar', 'ro': 'Română'
        };
        const getLangName = (code) => langNames[code] || code;
        const getLangFlag = (code) => langFlags[code] || '🌐';

        const getUniqueLangs = (variants) => {
            const seen = new Set();
            return (variants || []).filter(v => {
                if (!v.language || seen.has(v.language)) return false;
                seen.add(v.language);
                return true;
            });
        };
        
        // Load covers from MangaDex — skip Mihon manga (they come from a different source)
        const loadCoverForManga = async (manga) => {
            if (manga.cachedCover) return manga.cachedCover;
            // For Suwayomi/Mihon manga use the stored thumbnail URL (no MangaDex lookup)
            if (manga.source_meta?.sourceId) {
                // Use stored thumbnail if available
                if (manga.source_meta.thumbnailUrl || manga.cover) {
                    const thumb = manga.source_meta.thumbnailUrl || manga.cover;
                    manga.cachedCover = thumb;
                    return thumb;
                }
                // No stored thumbnail — fetch from Suwayomi and cache it
                if (manga.source_meta.mangaId) {
                    try {
                        const r = await fetch(`/api/sources/manga/${manga.source_meta.mangaId}`);
                        if (r.ok) {
                            const d = await r.json();
                            if (d.thumbnailUrl) {
                                manga.cachedCover = d.thumbnailUrl;
                                // Persist thumbnail into source_meta for next time
                                fetch('/api/sources/save_to_library', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        title: manga.name || manga.title,
                                        sourceId: manga.source_meta.sourceId,
                                        mangaId: manga.source_meta.mangaId,
                                        thumbnailUrl: d.thumbnailUrl,
                                        onlyIfExists: true,
                                    }),
                                }).catch(() => {});
                                const idx = library.value.findIndex(m => m.id === manga.id);
                                if (idx >= 0) {
                                    library.value = [...library.value];
                                    library.value[idx].cachedCover = d.thumbnailUrl;
                                }
                                return d.thumbnailUrl;
                            }
                        }
                    } catch (e) {}
                }
                return null;
            }
            try {
                const searchRes = await fetch('/api/mangadex/search?q=' + encodeURIComponent(manga.name));
                const searchData = await searchRes.json();
                if (searchData?.[0]?.cover) {
                    manga.cachedCover = searchData[0].cover;
                    const idx = library.value.findIndex(m => m.id === manga.id);
                    if (idx >= 0) {
                        library.value = [...library.value];
                        library.value[idx].cachedCover = searchData[0].cover;
                    }
                }
            } catch (e) {
                console.log('Error loading cover for', manga.name, e.message);
            }
            return manga.cachedCover;
        };
        
        const loadLocalLibrary = () => fetch('/api/mangadex/local_library').then(r => r.json()).then(d => localLibrary.value = d || []);
        const loadMdLibrary = () => fetch('/api/mangadex/library').then(r => r.json()).then(d => mdLibrary.value = d || []);
        
        const loadChapters = (title) => {
            fetch('/api/library/' + encodeURIComponent(title))
                .then(r => r.json()).catch(() => ({ chapters: [], upscaled: {} }))
                .then(d => {
                    chapters.value = d.chapters || [];
                    const downloaded = {};
                    for (const c of d.chapters || []) {
                        if (c.page_count > 0) downloaded[normalizeChapter(c.chapter)] = true;
                    }
                    const upscaled = {};
                    for (const key of Object.keys(d.upscaled || {})) {
                        upscaled[normalizeChapter(key)] = d.upscaled[key];
                    }
                    chapterStatus.value = { downloaded, upscaled };
                });
            loadChapterHealth(title);
        };
        
        const loadChapterHealth = (title) => {
            fetch('/api/library/chapter_health/' + encodeURIComponent(title))
                .then(r => r.json()).catch(() => [])
                .then(d => { if (Array.isArray(d)) chapterHealth.value = d; });
        };

        const getChapterHealth = (ch) => {
            const norm = normalizeChapter(ch);
            return chapterHealth.value.find(h => normalizeChapter(h.chapter) === norm) || null;
        };

        const repairChapter = async (ch, mode = 'full') => {
            const title = currentTitle.value;
            if (!title) return;
            const taskId = getTaskKey(ch, 'upscale', title);
            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'upscale', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(ch), mode, repair: true } };
            showToast(`Reparando cap. ${ch}...`, 'info');
            try {
                const res = await fetch('/api/upscale/repair_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: ch, mode })
                });
                const data = await res.json();
                if (data.status === 'nothing_to_repair') {
                    showToast(`Cap. ${ch} ya está completo`, 'info');
                    const t = { ...activeTasks.value }; delete t[taskId]; activeTasks.value = t;
                    loadChapterHealth(title);
                } else if (data.error) {
                    showToast('Error: ' + data.error, 'error');
                    activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
                } else if (data.task_id && data.task_id !== taskId && activeTasks.value[taskId]) {
                    const next = { ...activeTasks.value };
                    const cur = next[taskId]; delete next[taskId];
                    next[data.task_id] = { ...cur, taskId: data.task_id };
                    activeTasks.value = next;
                }
            } catch(e) {
                showToast('Error: ' + e.message, 'error');
                activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
            }
        };

        const loadMdChapters = (mangaId) => {
            fetch('/api/mangadex/chapters/' + mangaId).then(r => r.json()).catch(() => [])
                .then(d => { if (Array.isArray(d)) mdChapters.value = d; });
        };
        
        // Search
        const debouncedSearch = () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                if (searchQuery.value.length < 2) { searchResults.value = []; return; }
                fetch('/api/mangadex/search?q=' + encodeURIComponent(searchQuery.value))
                    .then(r => r.json()).then(d => searchResults.value = d || []);
            }, 300);
        };
        
        // Open local manga
        // options.skipMangaDexLookup — when true, never fall through to MangaDex search
        // options.forcedSourceMeta   — source_meta from the library card (may be newer than API response)
        // options.pairedMdManga      — MangaDex entry paired with this local manga (for the link button only)
        const openManga = async (manga, { skipMangaDexLookup = false, forcedSourceMeta = null, pairedMdManga = null } = {}) => {
            currentManga.value = manga;
            currentMdManga.value = null;
            currentTitle.value = manga.name || manga.title;
            chapters.value = [];
            mdChapters.value = [];
            chapterStatus.value = { downloaded: {}, upscaled: {} }; chapterHealth.value = [];
            currentSourceContext.value = null;
            currentModalTab.value = 'chapters';
            exportSelectedChapters.value = [];
            exportExcludedPages.value = [];
            showModal.value = true;

            const title = manga.name || manga.title;
            // Load local downloaded chapters + check for source metadata
            const libRes = await fetch('/api/library/' + encodeURIComponent(title)).catch(() => null);
            if (libRes?.ok) {
                const d = await libRes.json().catch(() => ({}));
                chapters.value = d.chapters || [];
                const dl = {}, up = {};
                for (const c of d.chapters || []) { if (c.page_count > 0) dl[normalizeChapter(c.chapter)] = true; }
                for (const key of Object.keys(d.upscaled || {})) { up[normalizeChapter(key)] = d.upscaled[key]; }
                chapterStatus.value = { downloaded: dl, upscaled: up };

                // Use forcedSourceMeta from the card (covers old downloads without .source_meta.json)
                const sourceMeta = forcedSourceMeta || d.source_meta;

                // If manga was downloaded from a Suwayomi source, load the full chapter list from there
                if (sourceMeta?.sourceId && sourceMeta?.mangaId) {
                    currentSourceContext.value = { sourceId: sourceMeta.sourceId, mangaId: sourceMeta.mangaId };
                    // Load cover and chapters in parallel
                    const [, chRes] = await Promise.all([
                        loadCoverForManga(manga).then(c => { currentCover.value = c || manga.cachedCover || manga.cover || null; }),
                        fetch(`/api/sources/manga/${sourceMeta.mangaId}/chapters`).catch(() => null),
                    ]);
                    try {
                        if (chRes?.ok) {
                            const raw = await chRes.json();
                            if (Array.isArray(raw) && !raw.error) {
                                mdChapters.value = raw.map(ch => ({
                                    id: 'suw_' + ch.id,
                                    suwayomiId: ch.id,
                                    chapter: String(ch.chapterNumber ?? '0').replace(/\.0$/, ''),
                                    title: ch.name || '',
                                    language: 'und',
                                    scanlator: ch.scanlator || '',
                                    pageCount: ch.pageCount || 0,
                                }));
                            }
                        }
                    } catch (e) {}
                    return;
                }
            }

            // Stop here — show only local chapters. For non-Mihon local downloads
            // that have a paired MangaDex entry, still set currentMdManga so the
            // MangaDex link button is available without loading the chapter list.
            if (skipMangaDexLookup) {
                const cover = await loadCoverForManga(manga);
                currentCover.value = cover || manga.cover;
                if (!forcedSourceMeta?.sourceId && pairedMdManga?.id) {
                    currentMdManga.value = pairedMdManga;
                    if (!currentCover.value && pairedMdManga.cover) currentCover.value = pairedMdManga.cover;
                    loadMdChapters(pairedMdManga.id);
                }
                return;
            }

            const cover = await loadCoverForManga(manga);
            currentCover.value = cover || manga.cover;

            // No source meta and MangaDex lookup allowed — try MangaDex for the full chapter list
            const titleHint = title;
            try {
                const res = await fetch('/api/mangadex/search?q=' + encodeURIComponent(titleHint));
                const results = await res.json();
                if (Array.isArray(results) && results.length > 0) {
                    const exact = results.find(r => canonicalTitle(r.title) === canonicalTitle(titleHint));
                    const mdEntry = exact || results[0];
                    currentMdManga.value = { ...mdEntry, title: mdEntry.title || titleHint };
                    if (!currentCover.value && mdEntry.cover) currentCover.value = mdEntry.cover;
                    loadMdChapters(mdEntry.id);
                }
            } catch (e) {}
        };
        
        // Open MangaDex manga
        const openMdManga = (manga) => {
            currentMdManga.value = manga;
            currentManga.value = null;
            currentTitle.value = manga.title;
            currentCover.value = manga.cover;
            chapters.value = [];
            mdChapters.value = [];
            chapterStatus.value = { downloaded: {}, upscaled: {} }; chapterHealth.value = [];
            currentModalTab.value = 'chapters';
            exportSelectedChapters.value = [];
            showModal.value = true;
            loadMdChapters(manga.id);
            // Also check local chapters
            if (manga.title) {
                fetch('/api/library/' + encodeURIComponent(manga.title))
                    .then(r => r.json()).catch(() => ({}))
                    .then(d => {
                        if (d.chapters?.length) {
                            const dl = {};
                            for (const c of d.chapters) {
                                if (c.page_count > 0) dl[normalizeChapter(c.chapter)] = true;
                            }
                            const upscaled = {};
                            for (const key of Object.keys(d.upscaled || {})) {
                                upscaled[normalizeChapter(key)] = d.upscaled[key];
                            }
                            chapterStatus.value = { ...chapterStatus.value, downloaded: dl, upscaled };
                        }
                    });
            }
        };

        const openLibraryItem = (item) => {
            if (!item) return;
            if (item.localManga) {
                // Any locally-downloaded manga shows its local chapters; never fall through
                // to MangaDex search. MangaDex metadata (cover, link) can still be shown
                // via item.mdManga without loading an unrelated chapter list.
                openManga(item.localManga, {
                    skipMangaDexLookup: true,
                    forcedSourceMeta: item.source_meta || null,
                    // Pass mdManga so the modal can show a MangaDex link if available
                    pairedMdManga: item.mdManga || null,
                });
                return;
            }
            if (item.mdManga) {
                openMdManga(item.mdManga);
            }
        };
        
        const closeModal = () => {
            showModal.value = false;
            selectedLang.value = '';
            currentManga.value = null;
            currentMdManga.value = null;
            currentSourceContext.value = null;
        };
        
        // Add to local library
        // Add to local library
        const addToLibrary = async (manga) => {
            // Vue click handlers can pass DOM event when called as @click="addToLibrary"
            if (manga && typeof manga === 'object' && (typeof manga.preventDefault === 'function' || 'isTrusted' in manga)) {
                manga = null;
            }

            if (!manga) manga = currentMdManga.value || currentManga.value;

            // ── Mihon / Suwayomi source ───────────────────────────────────────────
            // If the modal was opened from a Mihon source, save as a Mihon library entry
            // (write .source_meta.json) instead of going through MangaDex.
            if (currentSourceContext.value?.sourceId && currentSourceContext.value?.mangaId) {
                const title = (manga?.title || manga?.name || currentTitle.value || '').trim();
                if (!title) { showToast('No hay manga seleccionado', 'error'); return; }

                // Already in library as Mihon entry?
                const norm = canonicalTitle(title);
                if (library.value.some(m => canonicalTitle(m.name || m.title || '') === norm && m.source_meta?.sourceId)) {
                    showToast('Ya está en biblioteca', 'info');
                    return;
                }

                showToast('Añadiendo a biblioteca...', 'info');
                try {
                    const thumbnailUrl = currentCover.value || null;
                    const res = await fetch('/api/sources/save_to_library', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            title,
                            sourceId: currentSourceContext.value.sourceId,
                            mangaId: currentSourceContext.value.mangaId,
                            thumbnailUrl,
                        }),
                    });
                    const payload = await res.json();
                    if (!res.ok) { showToast('Error: ' + (payload?.error || 'Unknown'), 'error'); return; }

                    showToast('Añadido a biblioteca (Mihon)', 'success');
                    // Add to in-memory library immediately so the card shows up right away
                    if (!library.value.some(m => canonicalTitle(m.name || m.title || '') === norm)) {
                        library.value = [...library.value, {
                            id: title, name: title, title,
                            chapter_count: 0, upscaled: 0,
                            cover: thumbnailUrl,
                            source_meta: {
                                sourceId: String(currentSourceContext.value.sourceId),
                                mangaId: currentSourceContext.value.mangaId,
                                title,
                                thumbnailUrl,
                            },
                            cachedCover: thumbnailUrl,
                        }];
                    }
                    loadLibrary();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
                return;
            }

            // ── MangaDex flow ─────────────────────────────────────────────────────
            // If the manga doesn't have a MangaDex id, try to resolve it by title.
            if (!manga?.id) {
                const titleHint = (manga?.title || manga?.name || currentTitle.value || '').trim();
                if (!titleHint) {
                    showToast('No hay manga seleccionado para agregar', 'error');
                    return;
                }

                showToast('Buscando manga en MangaDex...', 'info');
                try {
                    const searchRes = await fetch('/api/mangadex/search?q=' + encodeURIComponent(titleHint));
                    const candidates = await searchRes.json();
                    if (!Array.isArray(candidates) || candidates.length === 0) {
                        showToast('No se encontró este manga en MangaDex', 'error');
                        return;
                    }

                    const target = canonicalTitle(titleHint);
                    const exact = candidates.find(c => canonicalTitle(c?.title) === target);
                    const partial = candidates.find(c => canonicalTitle(c?.title).includes(target) || target.includes(canonicalTitle(c?.title)));
                    manga = exact || partial || candidates[0];

                    currentMdManga.value = manga;
                    if (!currentCover.value && manga?.cover) currentCover.value = manga.cover;
                } catch (e) {
                    showToast('Error buscando manga: ' + e.message, 'error');
                    return;
                }
            }

            const mangaId = String(manga.id || '');
            if (!mangaId) {
                showToast('No se pudo resolver ID de manga', 'error');
                return;
            }

            const alreadyIn = localLibrary.value.some(m => String(m?.id || m?.mangaId || '') === mangaId);
            if (alreadyIn) {
                showToast('Ya está en biblioteca', 'info');
                return;
            }

            showToast('Añadiendo...', 'info');
            try {
                const res = await fetch('/api/mangadex/local_library/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ manga: manga })
                });
                const payload = await res.json();
                if (res.ok || res.status === 200) {
                    if (payload?.already_exists) {
                        showToast('Ya estaba en biblioteca', 'info');
                    } else {
                        showToast('Añadido a biblioteca', 'success');
                        localLibrary.value = [...localLibrary.value, manga];
                    }
                    loadLocalLibrary();
                    const idx = mdLibrary.value.findIndex(m => m.id === manga.id);
                    if (idx >= 0) mdLibrary.value[idx].inLibrary = true;
                } else {
                    showToast('Error: ' + (payload?.error || 'Unknown'), 'error');
                }
            } catch(e) {
                showToast('Error: ' + e.message, 'error');
            }
        };

        const removeLibraryItem = async (item) => {
            if (!item) return;

            const title = (item.title || item.localManga?.name || '').trim();
            if (!title) {
                showToast('No se pudo identificar el manga a borrar', 'error');
                return;
            }

            const confirmed = window.confirm(`Borrar "${title}" de tu biblioteca?`);
            if (!confirmed) return;

            const errors = [];
            let removedAnything = false;

            // Remove downloaded files (original + upscaled) when present.
            if (item.downloaded) {
                try {
                    const res = await fetch('/api/download/delete_manga', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title })
                    });
                    if (res.ok) {
                        removedAnything = true;
                    } else {
                        const payload = await res.json().catch(() => ({}));
                        errors.push(payload?.error || `Error borrando archivos (${res.status})`);
                    }
                } catch (e) {
                    errors.push('Error borrando archivos: ' + e.message);
                }
            }

            // Remove saved MangaDex entry from local library JSON when present.
            let mangaId = item?.mdManga?.id || '';
            if (!mangaId) {
                const byTitle = localLibrary.value.find(m => canonicalTitle(m?.title || m?.name) === canonicalTitle(title));
                mangaId = byTitle?.id || '';
            }
            if (mangaId) {
                try {
                    const res = await fetch('/api/mangadex/local_library/remove/' + encodeURIComponent(mangaId), {
                        method: 'DELETE'
                    });
                    if (res.ok) {
                        removedAnything = true;
                    } else {
                        const payload = await res.json().catch(() => ({}));
                        errors.push(payload?.error || `Error quitando de biblioteca (${res.status})`);
                    }
                } catch (e) {
                    errors.push('Error quitando de biblioteca: ' + e.message);
                }
            }

            await Promise.all([loadLibrary(), loadLocalLibrary(), loadMdLibrary()]);

            if (currentTitle.value && canonicalTitle(currentTitle.value) === canonicalTitle(title)) {
                closeModal();
            }

            if (errors.length) {
                showToast(errors[0], 'error');
                return;
            }

            showToast(removedAnything ? 'Manga borrado de biblioteca' : 'No había nada para borrar', removedAnything ? 'success' : 'info');
        };
        
        // Chapter status
        const isDownloaded = (ch) => chapterStatus.value.downloaded[normalizeChapter(ch)];
        const isUpscaled = (ch) => chapterStatus.value.upscaled[normalizeChapter(ch)] === true;
        const isPartialUpscaled = (ch) => chapterStatus.value.upscaled[normalizeChapter(ch)] === 'partial';
        
        // Task helpers
        const getTaskKey = (ch, type, titleOverride) => {
            const title = titleOverride || currentTitle.value || currentManga.value?.title || currentMdManga.value?.title;
            return buildTaskId(title, ch, type);
        };
        
        const getTask = (ch, type) => activeTasks.value[getTaskKey(ch, type)];
        
        const getActiveTask = (title) => {
            // Find any active task for this title
            for (const key in activeTasks.value) {
                const task = activeTasks.value[key];
                if (key.startsWith(title) && task?.status !== 'complete' && task?.status !== 'error') {
                    return task;
                }
            }
            return null;
        };
        
        const getProgressPercent = (task) => {
            if (!task || !task.total) return 0;
            return Math.round((task.progress / task.total) * 100);
        };
        
        // Actions
        const downloadChapter = async (group, forceLang = null, { silent = false } = {}) => {
            if (currentSourceContext.value) { await _downloadFromSource(group); return; }
            const title = currentTitle.value;
            if (!title) return;

            const variants = Array.isArray(group?.variants) ? group.variants : [];
            const langToUse = forceLang || selectedLang.value || null;
            const selectedVariant = langToUse
                ? variants.find(v => v.language === langToUse) || variants[0]
                : null;
            const ch = selectedVariant || variants[0] || (Array.isArray(group) ? group[0] : group);
            const resolvedLang = ch?.language || langToUse || null;
            const chapter = group?.chapter ?? ch?.chapter;
            const chapterId = ch?.id || null; // MangaDex ID may not exist for local chapters
            const mangaId = currentMdManga.value?.id || null;
            if (chapter === null || chapter === undefined) {
                showToast('No se pudo determinar el capítulo', 'error');
                return;
            }
            const taskId = getTaskKey(chapter, 'download', title);

            // Track the language being downloaded for this chapter
            if (resolvedLang) {
                const chKey = normalizeChapter(chapter);
                downloadedLang.value = { ...downloadedLang.value, [chKey]: resolvedLang };
            }
            
            // Immediate UI update
            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'download', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(chapter) } };
            if (!silent) showToast(`Descargando capítulo ${chapter}...`, 'info');
            
            try {
                // If we have chapterId, use it. Otherwise use title+chapter only
                const body = chapterId || mangaId
                    ? JSON.stringify({ chapterId, mangaId, title, chapter })
                    : JSON.stringify({ title, chapter });

                const res = await fetch('/api/download/download_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body
                });
                const data = await res.json();
                if (data.error) {
                    showToast('Error: ' + data.error, 'error');
                    activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
                } else {
                    // Add manga to library immediately if not already present
                    const norm = canonicalTitle(title);
                    if (!library.value.some(m => canonicalTitle(m.name || m.title || '') === norm)) {
                        library.value = [...library.value, {
                            id: title, name: title, title,
                            chapter_count: 0, upscaled: 0,
                            cachedCover: currentCover.value || null,
                        }];
                    }
                    if (data.task_id && data.task_id !== taskId && activeTasks.value[taskId]) {
                        const currentTask = activeTasks.value[taskId];
                        const nextTasks = { ...activeTasks.value };
                        delete nextTasks[taskId];
                        nextTasks[data.task_id] = { ...currentTask, taskId: data.task_id };
                        activeTasks.value = nextTasks;
                    }
                }
            } catch(e) {
                showToast('Error: ' + e.message, 'error');
                activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
            }
        };
        
        const upscaleChapter = async (ch, mode = 'full', { silent = false } = {}) => {
            const title = currentTitle.value;
            if (!title) return;
            const taskId = getTaskKey(ch, 'upscale', title);

            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'upscale', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(ch), mode } };
            if (!silent) showToast(mode === 'eco' ? `Upscale eco cap. ${ch}...` : `Upscaling capítulo ${ch}...`, 'info');

            try {
                const res = await fetch('/api/upscale/upscale_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: ch, mode })
                });
                const data = await res.json();
                if (data.error) {
                    showToast('Error: ' + data.error, 'error');
                    activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
                } else if ((data.task_id || data.upscale_id) && (data.task_id || data.upscale_id) !== taskId && activeTasks.value[taskId]) {
                    const resolvedTaskId = data.task_id || data.upscale_id;
                    const currentTask = activeTasks.value[taskId];
                    const nextTasks = { ...activeTasks.value };
                    delete nextTasks[taskId];
                    nextTasks[resolvedTaskId] = { ...currentTask, taskId: resolvedTaskId };
                    activeTasks.value = nextTasks;
                }
            } catch(e) {
                showToast('Error: ' + e.message, 'error');
                activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
            }
        };

        const cancelUpscale = async (taskId) => {
            try {
                await fetch('/api/upscale/cancel/' + encodeURIComponent(taskId), { method: 'POST' });
                const t = { ...activeTasks.value };
                delete t[taskId];
                activeTasks.value = t;
                showToast('Upscale cancelado', 'info');
            } catch(e) {}
        };

        const cancelDownload = async (taskId) => {
            try {
                await fetch('/api/download/cancel/' + encodeURIComponent(taskId), { method: 'POST' });
                const t = { ...activeTasks.value };
                delete t[taskId];
                activeTasks.value = t;
                showToast('Descarga cancelada', 'info');
            } catch(e) {}
        };
        
        const readChapter = async (ch, source = 'auto') => {
            const title = currentTitle.value;
            if (!title) return;
            currentChapter.value = ch;
            readerZoom.value = 1.0;
            panY.value = 0;
            panX.value = 0;
            showReader.value = true;
            pages.value = [];
            try {
                const r = await fetch('/api/reader/read_chapter', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: ch, source })
                });
                const d = await r.json();
                pages.value = d.pages || [];
                readerSource.value = d.source || 'original';
                compareMode.value = false; // reset on chapter change
                const prog = getLastReadProgress(title);
                if (prog?.lastChapter === String(ch) && prog.lastPage > 0 && prog.lastPage < pages.value.length) {
                    currentPage.value = prog.lastPage;
                } else {
                    currentPage.value = 0;
                }
            } catch(e) { console.error('readChapter error', e); }
        };

        const readChapterOriginal = (ch) => readChapter(ch, 'original');
        const readChapterUpscaled = (ch) => readChapter(ch, 'upscaled');

        // Reader controls
        const closeReader = () => {
            showReader.value = false; pages.value = [];
            readerZoom.value = 1.0; panY.value = 0; panX.value = 0; readerBarsHidden.value = false;
            dragActive = false; compareDragging = false; compareMode.value = false;
            clearTimeout(readerBarsTimer);
        };
        const nextPage = () => { if (currentPage.value < pages.value.length - 1) currentPage.value++; };
        const prevPage = () => { if (currentPage.value > 0) currentPage.value--; };
        const toggleZoom = () => {};
        const goToPage = (num) => { const p = parseInt(num) - 1; if (p >= 0 && p < pages.value.length) currentPage.value = p; };
        const onPageLoad = () => {};

        // Chapter navigation from reader
        const goNextChapter = () => {
            if (!canGoNextChapter.value) return;
            const next = readerChapterList.value[readerChapterIndex.value + 1];
            readChapter(next.chapter, isUpscaled(next.chapter) ? 'upscaled' : 'auto');
        };
        const goPrevChapter = () => {
            if (!canGoPrevChapter.value) return;
            const prev = readerChapterList.value[readerChapterIndex.value - 1];
            readChapter(prev.chapter, isUpscaled(prev.chapter) ? 'upscaled' : 'auto');
        };

        // Bar auto-hide
        const showBars = () => {
            readerBarsHidden.value = false;
            clearTimeout(readerBarsTimer);
            if (readerMode.value === 'paged') readerBarsTimer = setTimeout(() => { readerBarsHidden.value = true; }, 1200);
        };

        // Reader settings
        const cycleFitMode = () => { const modes = ['width','height','original']; const i = modes.indexOf(fitMode.value); fitMode.value = modes[(i+1)%3]; localStorage.setItem('reader-fit', fitMode.value); };
        const setReaderMode = (mode) => { readerMode.value = mode; localStorage.setItem('reader-mode', mode); };
        const toggleReaderDir = () => { readingDir.value = readingDir.value === 'rtl' ? 'ltr' : 'rtl'; localStorage.setItem('reader-dir', readingDir.value); };
        // Drag-to-pan when zoomed
        const onPanStart = (e) => {
            if (readerZoom.value <= 1.01) return;
            dragActive = true;
            dragHasMoved = false;
            dragOriginX = e.clientX;
            dragOriginY = e.clientY;
            dragStartPanX = panX.value;
            dragStartPanY = panY.value;
            e.preventDefault();
        };
        const onPanMove = (e) => {
            if (!dragActive) return;
            const dx = e.clientX - dragOriginX;
            const dy = e.clientY - dragOriginY;
            if (Math.abs(dx) > 4 || Math.abs(dy) > 4) dragHasMoved = true;
            if (dragHasMoved) {
                const maxPanYv = (readerZoom.value - 1) * window.innerHeight * 0.6;
                const maxPanXv = (readerZoom.value - 1) * window.innerWidth  * 0.6;
                panY.value = Math.max(-maxPanYv, Math.min(maxPanYv, dragStartPanY + dy));
                panX.value = Math.max(-maxPanXv, Math.min(maxPanXv, dragStartPanX + dx));
            }
        };
        const onPanEnd = (e) => {
            if (!dragActive) return;
            dragActive = false;
            if (!dragHasMoved) {
                // Treat as page-navigation click
                const x = e.clientX / window.innerWidth;
                readingDir.value === 'rtl' ? (x > 0.5 ? prevPage() : nextPage())
                                           : (x > 0.5 ? nextPage() : prevPage());
            }
        };

        // Scroll zoom (paged mode: always; webtoon: only with ctrl)
        const handleReaderWheel = (e) => {
            if (readerMode.value === 'webtoon' && !(e.ctrlKey || e.metaKey)) return;
            e.preventDefault();
            const next = Math.max(0.5, Math.min(3.0, readerZoom.value + (e.deltaY < 0 ? 0.15 : -0.15)));
            readerZoom.value = next;
            if (next <= 1.0) { panY.value = 0; panX.value = 0; }
        };

        const toggleFullscreen = () => {
            if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(()=>{});
            else document.exitFullscreen().catch(()=>{});
        };

        // ── Compare mode (before/after upscale) ─────────────────────────────
        const toggleCompare = () => {
            if (!canCompare.value) { compareMode.value = false; return; }
            compareMode.value = !compareMode.value;
            if (compareMode.value) compareX.value = 50;
        };
        const onCompareDragStart = (e) => {
            compareDragging = true;
            e.stopPropagation();
            e.preventDefault();
        };
        const onCompareDrag = (e) => {
            if (!compareDragging) return;
            const content = document.querySelector('.reader-content');
            if (!content) return;
            const rect = content.getBoundingClientRect();
            compareX.value = Math.max(3, Math.min(97, ((e.clientX - rect.left) / rect.width) * 100));
        };
        const onCompareDragEnd = () => { compareDragging = false; };

        // ── Mobile library ───────────────────────────────────────────────────
        const libraryUrlLocal = ref('http://localhost:5001/library');
        const libraryUrlPhone = ref('');
        const libraryWslIp = ref('');
        const libraryFileCount = ref(0);
        const libraryFwCmd = 'New-NetFirewallRule -DisplayName "MangaUpscaler Web" -Direction Inbound -Protocol TCP -LocalPort 5001 -Action Allow -Profile Any';
        const libraryProxyCmd = computed(() =>
            `netsh interface portproxy add v4tov4 listenport=5001 listenaddress=0.0.0.0 connectport=5001 connectaddress=${libraryWslIp.value}`
        );

        const loadLibraryInfo = async () => {
            try {
                const r = await fetch('/api/webdav/status');
                const d = await r.json();
                libraryUrlLocal.value = d.library_url_local || 'http://localhost:5001/library';
                libraryUrlPhone.value = d.library_url_phone || '';
                libraryWslIp.value = d.server_ip || '';
                libraryFileCount.value = (d.folders || []).length + (d.files || []).length;
            } catch (_) {}
        };

        const saveToLibrary = async (taskId) => {
            try {
                const r = await fetch('/api/webdav/save', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: taskId }),
                });
                const d = await r.json();
                if (d.error) { showToast('Error guardando: ' + d.error, 'error'); return; }
                showToast(`"${d.filename}" guardado en biblioteca`, 'success');
                loadLibraryInfo();
            } catch (e) { showToast('Error guardando en biblioteca', 'error'); }
        };

        const dismissExportTask = (taskId) => {
            const t = { ...exportTasks.value };
            delete t[taskId];
            exportTasks.value = t;
        };

        // Webtoon scroll → track current page
        const onWebtoonScroll = (e) => {
            const container = e.target;
            const imgs = container.querySelectorAll('.reader-webtoon-img');
            const mid = container.scrollTop + container.clientHeight / 2;
            let closest = 0, closestDist = Infinity;
            imgs.forEach((img, i) => {
                const dist = Math.abs(img.offsetTop + img.offsetHeight / 2 - mid);
                if (dist < closestDist) { closestDist = dist; closest = i; }
            });
            currentPage.value = closest;
        };
        
        const showToast = (msg, type = 'info') => {
            toast.value = { show: true, message: msg, type };
            setTimeout(() => toast.value.show = false, 3000);
        };
        
        // Poll tasks
        const pollTasks = () => {
            setInterval(async () => {
                const keys = Object.keys(activeTasks.value);
                if (!keys.length) return;
                
                for (const taskId of keys) {
                    const task = activeTasks.value[taskId];
                    if (!task || task.status === 'complete') continue;
                    
                    try {
                        const endpoint = task.type === 'download' ? '/api/status/download/' : '/api/status/upscale/';
                        const res = await fetch(endpoint + encodeURIComponent(taskId));
                        const data = await res.json();
                        
                        if (data.status === 'downloading' || data.status === 'upscaleing' || data.status === 'upscaling' || data.status === 'starting' || data.status === 'started') {
                            const progressValue = data.progress ?? data.current ?? 0;
                            const totalValue = data.total || 1;
                            activeTasks.value = {
                                ...activeTasks.value,
                                [taskId]: { ...task, status: data.status, progress: progressValue, total: totalValue, percent: data.percent, misses: 0 }
                            };
                        } else if (data.status === 'complete') {
                            activeTasks.value = { ...activeTasks.value, [taskId]: { ...task, status: 'complete', progress: task.total || 1 } };
                            showToast(task.type === 'download' ? '✅ Descarga completa' : '🔥 Upscale completo', 'success');
                            setTimeout(() => {
                                const t = { ...activeTasks.value };
                                delete t[taskId];
                                activeTasks.value = t;
                                if (currentTitle.value) {
                                    loadChapters(currentTitle.value);
                                    if (task.type === 'upscale') loadChapterHealth(currentTitle.value);
                                }
                                // Refresh library so card shows real chapter count
                                if (task.type === 'download') loadLibrary();
                            }, 2000);
                        } else if (data.status === 'cancelled') {
                            const t = { ...activeTasks.value };
                            delete t[taskId];
                            activeTasks.value = t;
                        } else if (data.status === 'error' || data.status === 'not_found') {
                            const misses = data.status === 'not_found' ? (task.misses || 0) + 1 : 0;
                            if (data.status === 'error' || misses >= 8) {
                                showToast(task.type === 'download' ? 'Error en descarga' : 'Error en upscale', 'error');
                                const t = { ...activeTasks.value };
                                delete t[taskId];
                                activeTasks.value = t;
                            } else {
                                activeTasks.value = { ...activeTasks.value, [taskId]: { ...task, misses } };
                            }
                        }
                    } catch (e) {}
                }
            }, 500);
        };
        
        // Keyboard
        const handleKeydown = (e) => {
            if (!showReader.value) return;
            const isRTL = readingDir.value === 'rtl';
            const zoomed = readerZoom.value > 1.01;

            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (zoomed) {
                    const maxPan = (readerZoom.value - 1) * window.innerHeight * 0.6;
                    panY.value = Math.max(panY.value - 120, -maxPan);
                }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (zoomed) {
                    const maxPan = (readerZoom.value - 1) * window.innerHeight * 0.6;
                    panY.value = Math.min(panY.value + 120, maxPan);
                }
                return;
            }

            if (e.key === 'ArrowRight') { e.preventDefault(); isRTL ? prevPage() : nextPage(); }
            if (e.key === 'ArrowLeft')  { e.preventDefault(); isRTL ? nextPage() : prevPage(); }
            if (e.key === ' ') { e.preventDefault(); nextPage(); }
            if (e.key === 'Escape') closeReader();
            if (e.key === 'f') cycleFitMode();
            if (e.key === 'w') setReaderMode(readerMode.value === 'paged' ? 'webtoon' : 'paged');
            if (e.key === 'd') toggleReaderDir();
            if (e.key === 'c') toggleCompare();
            if (e.key === ']') goNextChapter();
            if (e.key === '[') goPrevChapter();
        };

        // Reset pan when page changes
        watch(currentPage, () => { panY.value = 0; panX.value = 0; });

        // Auto-save progress on page change
        watch(currentPage, (page) => {
            if (!showReader.value || !currentTitle.value || !currentChapter.value) return;
            saveReadingProgress(currentTitle.value, currentChapter.value, page);
            if (pages.value.length > 0 && page >= pages.value.length - 1) {
                markChapterRead(currentTitle.value, currentChapter.value);
            }
        });
        
        const taskQueueList = computed(() => {
            return Object.entries(activeTasks.value).map(([id, task]) => ({
                id,
                ...task,
                percent: task.total > 0 ? Math.min(100, Math.round(((task.progress || 0) * 100) / task.total)) : 0
            }));
        });

        const taskQueueSummary = computed(() => {
            const list = taskQueueList.value;
            const dlTasks = list.filter(t => t.type === 'download');
            const upTasks = list.filter(t => t.type === 'upscale');
            const totalProgress = list.reduce((s, t) => s + (t.progress || 0), 0);
            const totalWork     = list.reduce((s, t) => s + (t.total || 0), 0);
            const aggPercent    = totalWork > 0 ? Math.min(100, Math.round(totalProgress * 100 / totalWork)) : 0;
            // Pages remaining across all tasks
            const pagesLeft = list.reduce((s, t) => s + Math.max(0, (t.total || 0) - (t.progress || 0)), 0);
            return { dl: dlTasks.length, up: upTasks.length, aggPercent, pagesLeft, total: list.length };
        });

        // Map canonical title → active task (used to show progress overlays on library cards)
        const activeTasksByTitle = computed(() => {
            const map = {};
            for (const task of taskQueueList.value) {
                const norm = canonicalTitle(task.displayTitle || '');
                if (norm && !map[norm]) map[norm] = task;
            }
            return map;
        });

        const isInLibrary = computed(() => {
            if (currentMdManga.value?.id) {
                return localLibrary.value.some(m => m.id === currentMdManga.value.id);
            }
            if (!currentTitle.value) return false;
            const title = (currentTitle.value || '').trim().toLowerCase();
            return localLibrary.value.some(m => ((m.title || m.name || '').trim().toLowerCase() === title));
        });
        
        const deleteChapter = async (chapter) => {
            const title = currentTitle.value;
            if (!title) return;
            const chNum = normalizeChapter(chapter);
            if (!window.confirm(`Borrar capítulo ${chNum} de "${title}"?\nSe eliminarán los archivos descargados y upscaleados.`)) return;

            // Optimistic UI update — remove immediately before server confirms
            const prevStatus = chapterStatus.value;
            const newDownloaded = { ...chapterStatus.value.downloaded };
            const newUpscaled = { ...chapterStatus.value.upscaled };
            delete newDownloaded[chNum];
            delete newUpscaled[chNum];
            chapterStatus.value = { downloaded: newDownloaded, upscaled: newUpscaled };

            try {
                const res = await fetch('/api/download/delete_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: chNum })
                });
                const data = await res.json();
                if (res.ok) {
                    showToast(`Capítulo ${chNum} eliminado`, 'success');
                    loadChapters(title);  // sync server state
                } else {
                    // Revert optimistic update on failure
                    chapterStatus.value = prevStatus;
                    showToast('Error: ' + (data.error || 'No se pudo eliminar'), 'error');
                }
            } catch (e) {
                chapterStatus.value = prevStatus;
                showToast('Error: ' + e.message, 'error');
            }
        };

        // Export / Tomo
        const openTomoTab = () => {
            if (!exportVolumeName.value) exportVolumeName.value = currentTitle.value || '';
            currentModalTab.value = 'tomo';
            if (!exportSelectedChapters.value.length) {
                exportPreview.value = { pages: 0, size_mb: 0, upscaled_pages: 0 };
            }
            exportCoverMode.value = 'none';
            exportSelectedCover.value = null;
            exportUploadedCoverB64.value = '';
            exportUploadedCoverUrl.value = '';
            exportExcludedPages.value = [];
            mdexSelectedCover.value = null;
            mdexVolumes.value = [];
            mdexCovers.value = [];
            mdexSearchQuery.value = '';
            mdexSearchResults.value = [];
            if (exportSelectedChapters.value.length > 0) loadColorPages();
            loadMdexVolumes();
        };

        const loadColorPages = async () => {
            if (!currentTitle.value || !exportSelectedChapters.value.length) {
                exportColorPages.value = [];
                exportExcludedPages.value = [];
                return;
            }
            exportColorPagesLoading.value = true;
            exportColorPages.value = [];
            try {
                const res = await fetch('/api/export/color_pages', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: currentTitle.value, chapters: exportSelectedChapters.value })
                });
                if (res.ok) {
                    exportColorPages.value = await res.json();
                    // Remove exclusions that no longer exist in the current chapter selection
                    const valid = new Set(exportColorPages.value.map(p => p.filename));
                    exportExcludedPages.value = exportExcludedPages.value.filter(f => valid.has(f));
                }
            } catch (e) {}
            exportColorPagesLoading.value = false;
        };

        const setCoverMode = (mode) => {
            exportCoverMode.value = mode;
            exportSelectedCover.value = null;
            exportUploadedCoverB64.value = '';
            exportUploadedCoverUrl.value = '';
            mdexSelectedCover.value = null;
            loadColorPages();
            if (mode === 'mdex') loadMdexCovers();
        };

        // ── MangaDex volumes ──────────────────────────────────────────────────
        // Returns a MangaDex UUID — only from currentMdManga (guaranteed UUID).
        // currentManga.id is the local folder name; source_meta.mangaId is Suwayomi's internal int — neither is a UUID.
        const _getMangaId = () => currentMdManga.value?.id || null;

        // Resolve UUID when not already known (library / Mihon manga not in followed list).
        const _resolveMdexId = async () => {
            let id = _getMangaId();
            if (id) return id;
            const title = currentTitle.value;
            if (!title) return null;
            try {
                const res = await fetch('/api/mangadex/search?q=' + encodeURIComponent(title));
                const results = await res.json();
                if (!Array.isArray(results) || !results.length) return null;
                const exact = results.find(r => canonicalTitle(r.title) === canonicalTitle(title));
                if (!exact) return null;   // only accept exact match to avoid wrong manga
                currentMdManga.value = { id: exact.id, title: exact.title, cover: exact.cover, status: exact.status };
                return exact.id;
            } catch (e) { return null; }
        };

        const loadMdexVolumes = async () => {
            const id = await _resolveMdexId();
            if (!id) return;
            mdexVolumesLoading.value = true;
            mdexVolumes.value = [];
            try {
                const [volRes, covRes] = await Promise.all([
                    fetch(`/api/mangadex/volumes/${id}`),
                    fetch(`/api/mangadex/covers/${id}`)
                ]);
                let volumes = volRes.ok ? await volRes.json() : [];
                if (!Array.isArray(volumes)) volumes = [];
                const covers = covRes.ok ? await covRes.json() : [];

                // Only scrape when the API returned zero chapter data (manga fully unlisted).
                // For partial gaps (covers > api vols), the gap interpolation handles it —
                // scraping would replace the detailed API chapter lists with coarse ranges.
                const apiVolCount = volumes.filter(v => v.chapters && v.chapters.length).length;
                if (apiVolCount === 0) {
                    try {
                        const scrapeRes = await fetch(`/api/mangadex/scrape_volumes/${id}`);
                        if (scrapeRes.ok) {
                            const scraped = await scrapeRes.json();
                            if (Array.isArray(scraped) && scraped.length > 0 && !scraped.error) {
                                volumes = scraped;
                            }
                        }
                    } catch (_) {}
                }

                // Add volumes that exist only as cover art (no chapters on MangaDex)
                const knownVols = new Set(volumes.map(v => v.volume));
                const coverVols = [...new Set(
                    (Array.isArray(covers) ? covers : [])
                        .map(c => c.volume)
                        .filter(v => v && v !== 'none')
                )];
                for (const vol of coverVols) {
                    if (!knownVols.has(vol)) {
                        volumes.push({ volume: vol, label: `Tomo ${vol}`, chapters: null, count: 0, noChapterData: true });
                    }
                }

                // Sort: numeric ascending, "none" last
                volumes.sort((a, b) => {
                    const na = parseFloat(a.volume), nb = parseFloat(b.volume);
                    if (!isNaN(na) && !isNaN(nb)) return na - nb;
                    return isNaN(na) ? 1 : -1;
                });
                mdexVolumes.value = volumes;
            } catch (e) {}
            mdexVolumesLoading.value = false;
        };

        const applyMdexVolume = (vol) => {
            exportVolumeName.value = vol.label || `Tomo ${vol.volume}`;
            exportExcludedPages.value = [];

            const localChaps = groupedChapters.value
                .map(g => ({ norm: normalizeChapter(g.chapter), num: parseFloat(g.chapter) }))
                .filter(g => !isNaN(g.num));

            let selected;
            if (vol.noChapterData || !vol.chapters || !vol.chapters.length) {
                const volNum = parseFloat(vol.volume);
                const known = mdexVolumes.value.filter(v => !v.noChapterData && v.chapters && v.chapters.length);

                if (known.length === 0) {
                    // No API chapter data for ANY volume — evenly split local chapters across cover-only volumes
                    const coverVols = mdexVolumes.value
                        .filter(v => !isNaN(parseFloat(v.volume)))
                        .sort((a, b) => parseFloat(a.volume) - parseFloat(b.volume));
                    const idx = coverVols.findIndex(v => v.volume === vol.volume);
                    const count = coverVols.length;
                    const sorted = [...localChaps].sort((a, b) => a.num - b.num);

                    // Separate "main" chapters (integers) from bonus/half chapters (e.g. 23.5, 2.1).
                    // This lets us compute a fair base size using only main chapters, then assign
                    // bonus chapters to the volume that contains their parent chapter number.
                    const mainChaps  = sorted.filter(g => Number.isInteger(g.num));
                    const bonusChaps = sorted.filter(g => !Number.isInteger(g.num));

                    const base      = Math.floor(mainChaps.length / count);
                    const remainder = mainChaps.length % count;
                    // Extra chapters go to the LAST `remainder` volumes so vol 1 isn't bloated
                    const slices = [];
                    let offset = 0;
                    for (let i = 0; i < count; i++) {
                        const extra = (i >= count - remainder) ? 1 : 0;
                        slices.push(mainChaps.slice(offset, offset + base + extra).map(g => g.norm));
                        offset += base + extra;
                    }
                    // Assign each bonus chapter to the slice that contains its integer parent
                    for (const bonus of bonusChaps) {
                        const parent = Math.floor(bonus.num);
                        const target = slices.findIndex(s =>
                            s.some(norm => parseFloat(norm) === parent)
                        );
                        (target >= 0 ? slices[target] : slices[slices.length - 1]).push(bonus.norm);
                    }
                    selected = slices[idx] || [];
                } else {
                    // Some volumes have API data — find bounding API volumes
                    const prev = [...known].sort((a,b) => parseFloat(b.volume)-parseFloat(a.volume)).find(v => parseFloat(v.volume) < volNum);
                    const next = [...known].sort((a,b) => parseFloat(a.volume)-parseFloat(b.volume)).find(v => parseFloat(v.volume) > volNum);
                    const prevMax = prev ? Math.max(...prev.chapters.map(Number).filter(n => !isNaN(n) && n >= 1)) : 0;
                    const _nextNums = next ? next.chapters.map(Number).filter(n => !isNaN(n) && n >= 1) : [];
                    const nextMin = _nextNums.length ? Math.min(..._nextNums) : Infinity;

                    // Find all cover-only volumes in this same sub-gap so we can split evenly.
                    // Without this, every cover-only vol in the gap selects the same chapters.
                    const prevDataNum = prev ? parseFloat(prev.volume) : -Infinity;
                    const nextDataNum = next ? parseFloat(next.volume) : Infinity;
                    const subGapVols = mdexVolumes.value
                        .filter(v => {
                            const vn = parseFloat(v.volume);
                            return !isNaN(vn) && vn > prevDataNum && vn < nextDataNum &&
                                (v.noChapterData || !v.chapters || !v.chapters.length);
                        })
                        .sort((a,b) => parseFloat(a.volume) - parseFloat(b.volume));

                    const gapChaps = [...localChaps]
                        .filter(g => g.num > prevMax && g.num < nextMin)
                        .sort((a,b) => a.num - b.num);

                    if (subGapVols.length <= 1) {
                        selected = gapChaps.map(g => g.norm);
                    } else {
                        const idx = subGapVols.findIndex(v => v.volume === vol.volume);
                        const chunkSize = Math.ceil(gapChaps.length / subGapVols.length);
                        selected = gapChaps.slice(idx * chunkSize, idx * chunkSize + chunkSize).map(g => g.norm);
                    }
                }
            } else {
                // Volume has API chapter data.
                // Filter out special chapters (< 1, e.g. "0.1" prologues) for boundary math.
                const nums = vol.chapters.map(Number).filter(n => !isNaN(n));
                const regular = nums.filter(n => n >= 1);
                const anchor = regular.length ? regular : nums;
                const minCh = Math.min(...anchor);
                const maxCh = Math.max(...anchor);

                // Detect whether this volume sits in a gap (cover-only volumes between it
                // and the nearest volumes that have API data on either side).
                const volNum = parseFloat(vol.volume);
                const allWithData = mdexVolumes.value.filter(
                    v => !v.noChapterData && v.chapters && v.chapters.length && v.volume !== vol.volume
                );
                const sortAsc  = (a, b) => parseFloat(a.volume) - parseFloat(b.volume);
                const sortDesc = (a, b) => parseFloat(b.volume) - parseFloat(a.volume);
                const prevWithData = [...allWithData].sort(sortDesc).find(v => parseFloat(v.volume) < volNum);
                const nextWithData = [...allWithData].sort(sortAsc).find(v => parseFloat(v.volume) > volNum);
                const prevDataNum = prevWithData ? parseFloat(prevWithData.volume) : -Infinity;
                const nextDataNum = nextWithData ? parseFloat(nextWithData.volume) : Infinity;

                const hasGap = mdexVolumes.value.some(v => {
                    const vn = parseFloat(v.volume);
                    return !isNaN(vn) &&
                        ((vn > prevDataNum && vn < volNum) || (vn > volNum && vn < nextDataNum)) &&
                        (v.noChapterData || !v.chapters || !v.chapters.length);
                });

                if (!hasGap) {
                    // Clean neighbourhood — strict API range
                    selected = localChaps.filter(g => g.num >= minCh && g.num <= maxCh).map(g => g.norm);
                } else {
                    // Isolated volume: neighbours are cover-only.
                    // Use an anchor-aligned equal-chunk split across all gap volumes so that
                    // unavailable chapters adjacent to the anchor are also included.
                    const _regularNums = v => v.chapters.map(Number).filter(n => !isNaN(n) && n >= 1);
                    const prevMax = prevWithData
                        ? Math.max(...(_regularNums(prevWithData).length ? _regularNums(prevWithData) : prevWithData.chapters.map(Number).filter(n => !isNaN(n))))
                        : 0;
                    const nextRegular = nextWithData ? _regularNums(nextWithData) : [];
                    const nextMin = nextRegular.length ? Math.min(...nextRegular) : Infinity;

                    const gapVols = mdexVolumes.value
                        .filter(v => {
                            const vn = parseFloat(v.volume);
                            return !isNaN(vn) && vn > prevDataNum && vn < nextDataNum;
                        })
                        .sort(sortAsc);

                    const gapChaps = [...localChaps]
                        .filter(g => g.num > prevMax && g.num < nextMin)
                        .sort((a, b) => a.num - b.num);

                    if (!gapChaps.length) {
                        // Fallback: strict range
                        selected = localChaps.filter(g => g.num >= minCh && g.num <= maxCh).map(g => g.norm);
                    } else {
                        // Chunk size: at least 3 to avoid single-chapter slices
                        const chunkSize = Math.max(Math.ceil(gapChaps.length / gapVols.length), 3);

                        // Find the chunk that contains the anchor (first regular chapter)
                        const anchorIdx = gapChaps.findIndex(g => g.num >= minCh);
                        if (anchorIdx < 0) {
                            // Anchor outside gap — use positional slot
                            const slot = gapVols.findIndex(v => v.volume === vol.volume);
                            const start = slot * chunkSize;
                            selected = gapChaps.slice(start, start + chunkSize).map(g => g.norm);
                        } else {
                            const chunkStart = Math.floor(anchorIdx / chunkSize) * chunkSize;
                            selected = gapChaps.slice(chunkStart, chunkStart + chunkSize).map(g => g.norm);
                        }
                    }
                }
            }

            if (!selected.length) {
                exportSelectedChapters.value = [];
                scheduleExportPreview();
                showToast(`${vol.label}: ningún capítulo en este rango`, 'warning');
                return;
            }
            exportSelectedChapters.value = selected;
            scheduleExportPreview();
            loadColorPages();
            const totalDefined = vol.chapters ? vol.chapters.length : 0;
            const missing = totalDefined > 0 && totalDefined > selected.length ? totalDefined - selected.length : 0;
            const msg = missing > 0
                ? `${selected.length}/${totalDefined} caps — ${vol.label} (faltan ${missing})`
                : `${selected.length} caps — ${vol.label}`;
            showToast(msg, missing > 0 ? 'warning' : 'success');
        };

        const searchMdexForTomo = async () => {
            const q = mdexSearchQuery.value.trim();
            if (!q) return;
            mdexSearchLoading.value = true;
            mdexSearchResults.value = [];
            try {
                const res = await fetch('/api/mangadex/search?q=' + encodeURIComponent(q));
                const results = await res.json();
                mdexSearchResults.value = Array.isArray(results) ? results.slice(0, 8) : [];
            } catch (e) {}
            mdexSearchLoading.value = false;
        };

        const selectMdexEntry = async (entry) => {
            currentMdManga.value = { id: entry.id, title: entry.title, cover: entry.cover, status: entry.status };
            mdexSearchResults.value = [];
            mdexSearchQuery.value = '';
            mdexVolumes.value = [];
            mdexCovers.value = [];
            await loadMdexVolumes();
            loadMdexCovers();
        };

        // ── MangaDex covers ───────────────────────────────────────────────────
        const loadMdexCovers = async () => {
            const id = await _resolveMdexId();
            if (!id) return;
            mdexCoversLoading.value = true;
            mdexCovers.value = [];
            try {
                const r = await fetch(`/api/mangadex/covers/${id}`);
                if (r.ok) mdexCovers.value = await r.json();
            } catch (e) {}
            mdexCoversLoading.value = false;
        };

        const selectMdexCover = async (cover) => {
            mdexSelectedCover.value = cover;
            mdexCoverLoadingId.value = cover.id;
            exportUploadedCoverB64.value = '';
            exportUploadedCoverUrl.value = cover.url;
            try {
                const r = await fetch('/api/mangadex/cover_b64', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: cover.url }),
                });
                if (r.ok) {
                    const d = await r.json();
                    exportUploadedCoverB64.value = d.b64;
                    exportUploadedCoverUrl.value = d.data_url;
                }
            } catch (e) {}
            mdexCoverLoadingId.value = null;
        };

        const toggleExcludePage = (filename) => {
            const idx = exportExcludedPages.value.indexOf(filename);
            if (idx === -1) exportExcludedPages.value.push(filename);
            else exportExcludedPages.value.splice(idx, 1);
        };

        const onCoverUpload = (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (file.size > 8 * 1024 * 1024) {
                showToast('Imagen demasiado grande (máx 8 MB)', 'warning');
                return;
            }
            const reader = new FileReader();
            reader.onload = (ev) => {
                const dataUrl = ev.target.result;
                exportUploadedCoverUrl.value = dataUrl;
                // Strip the data:image/xxx;base64, prefix — only send raw base64
                exportUploadedCoverB64.value = dataUrl.split(',')[1] || '';
            };
            reader.readAsDataURL(file);
        };

        const scheduleExportPreview = () => {
            clearTimeout(exportPreviewTimer);
            if (!exportSelectedChapters.value.length) {
                exportPreview.value = { pages: 0, size_mb: 0, upscaled_pages: 0 };
                return;
            }
            exportPreviewTimer = setTimeout(async () => {
                try {
                    const res = await fetch('/api/export/preview', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            title: currentTitle.value,
                            chapters: exportSelectedChapters.value,
                            quality: exportQuality.value,
                            exclude_pages: exportExcludedPages.value,
                        })
                    });
                    if (res.ok) exportPreview.value = await res.json();
                } catch (e) {}
                loadColorPages();
            }, 400);
        };

        // ── Bulk download / upscale ───────────────────────────────────────────────
        const bulkFrom = ref('');
        const bulkTo   = ref('');

        const _bulkRange = () => {
            const lo = parseFloat(bulkFrom.value), hi = parseFloat(bulkTo.value);
            if (isNaN(lo) || isNaN(hi)) return null;
            return { lo: Math.min(lo, hi), hi: Math.max(lo, hi) };
        };

        const bulkPreview = computed(() => {
            const r = _bulkRange();
            if (!r) return null;
            const inRange = filteredChapters.value.filter(g => {
                const n = parseFloat(g.chapter);
                return !isNaN(n) && n >= r.lo && n <= r.hi;
            });
            return {
                dl: inRange.filter(g =>
                    !isDownloaded(g.chapter) && !isUpscaled(g.chapter) && !isPartialUpscaled(g.chapter)
                    && !getTask(g.chapter, 'download')).length,
                up: inRange.filter(g =>
                    isDownloaded(g.chapter) && !isUpscaled(g.chapter) && !isPartialUpscaled(g.chapter)
                    && !getTask(g.chapter, 'upscale')).length,
            };
        });

        const bulkDownload = async () => {
            const r = _bulkRange();
            if (!r) return;
            const targets = filteredChapters.value.filter(g => {
                const n = parseFloat(g.chapter);
                return !isNaN(n) && n >= r.lo && n <= r.hi
                    && !isDownloaded(g.chapter) && !isUpscaled(g.chapter) && !isPartialUpscaled(g.chapter)
                    && !getTask(g.chapter, 'download');
            });
            if (!targets.length) { showToast('Todos los capítulos del rango ya están descargados', 'info'); return; }
            showToast(`Descargando ${targets.length} capítulo${targets.length !== 1 ? 's' : ''}...`, 'info');
            await Promise.all(targets.map(g => downloadChapter(g, selectedLang.value || null, { silent: true })));
        };

        const bulkUpscale = async (mode = 'full') => {
            const r = _bulkRange();
            if (!r) return;
            const targets = filteredChapters.value.filter(g => {
                const n = parseFloat(g.chapter);
                return !isNaN(n) && n >= r.lo && n <= r.hi
                    && (isDownloaded(g.chapter) || isPartialUpscaled(g.chapter))
                    && !isUpscaled(g.chapter) && !getTask(g.chapter, 'upscale');
            });
            if (!targets.length) { showToast('No hay capítulos descargados sin upscale en ese rango', 'info'); return; }
            showToast(`Upscaleando ${targets.length} capítulo${targets.length !== 1 ? 's' : ''}...`, 'info');
            await Promise.all(targets.map(g => upscaleChapter(g.chapter, mode, { silent: true })));
        };

        // ── Export chapter range (Tomo builder) ──────────────────────────────────
        const rangeFrom = ref('');
        const rangeTo = ref('');
        const rangeAnchor = ref(null);

        const addChapterRange = () => {
            const from = parseFloat(rangeFrom.value);
            const to   = parseFloat(rangeTo.value);
            if (isNaN(from) || isNaN(to)) return;
            const lo = Math.min(from, to), hi = Math.max(from, to);
            const toAdd = groupedChapters.value
                .filter(g => {
                    const n = parseFloat(g.chapter);
                    return !isNaN(n) && n >= lo && n <= hi &&
                           (isDownloaded(g.chapter) || isUpscaled(g.chapter) || isPartialUpscaled(g.chapter));
                })
                .map(g => normalizeChapter(g.chapter));
            if (!toAdd.length) { showToast('No hay capítulos descargados en ese rango', 'warn'); return; }
            const current = new Set(exportSelectedChapters.value);
            toAdd.forEach(c => current.add(c));
            exportSelectedChapters.value = [...current];
            scheduleExportPreview();
            loadColorPages();
        };

        const onRowClickCapture = (group, idx, event) => {
            const available = isDownloaded(group.chapter) || isUpscaled(group.chapter) || isPartialUpscaled(group.chapter);
            if (!available) return;
            if (event.shiftKey && rangeAnchor.value !== null) {
                // Intercept before the checkbox receives it
                event.preventDefault();
                event.stopPropagation();
                const lo = Math.min(rangeAnchor.value, idx);
                const hi = Math.max(rangeAnchor.value, idx);
                const inRange = groupedChapters.value
                    .slice(lo, hi + 1)
                    .filter(g => isDownloaded(g.chapter) || isUpscaled(g.chapter) || isPartialUpscaled(g.chapter))
                    .map(g => normalizeChapter(g.chapter));
                const current = new Set(exportSelectedChapters.value);
                inRange.forEach(c => current.add(c));
                exportSelectedChapters.value = [...current];
                scheduleExportPreview();
            } else {
                // Normal click: let v-model handle it, just record anchor
                rangeAnchor.value = idx;
            }
        };

        const selectAllExportChapters = () => {
            exportSelectedChapters.value = groupedChapters.value
                .filter(g => isDownloaded(g.chapter) || isUpscaled(g.chapter))
                .map(g => normalizeChapter(g.chapter));
            exportExcludedPages.value = [];
            rangeAnchor.value = null;
            scheduleExportPreview();
            loadColorPages();
        };

        const selectUpscaledExportChapters = () => {
            exportSelectedChapters.value = groupedChapters.value
                .filter(g => isUpscaled(g.chapter) || isPartialUpscaled(g.chapter))
                .map(g => normalizeChapter(g.chapter));
            exportExcludedPages.value = [];
            rangeAnchor.value = null;
            scheduleExportPreview();
            loadColorPages();
        };

        const _triggerFileDownload = (url, filename) => {
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
        };

        const downloadTomo = async () => {
            if (!exportSelectedChapters.value.length) {
                showToast('Selecciona al menos un capítulo', 'warning');
                return;
            }
            const fmt = exportFormat.value || 'cbz';
            const coverPayload = {};
            if (exportCoverMode.value === 'chapter' && exportSelectedCover.value?.path) {
                coverPayload.cover_path = exportSelectedCover.value.path;
            } else if (exportUploadedCoverB64.value) {
                coverPayload.cover_data = exportUploadedCoverB64.value;
            }
            const payload = {
                title: currentTitle.value,
                chapters: exportSelectedChapters.value,
                volume_name: exportVolumeName.value || currentTitle.value,
                format: fmt,
                quality: exportQuality.value,
                downscale_half: exportDownscaleHalf.value,
                exclude_pages: exportExcludedPages.value,
                ...coverPayload,
            };

            try {
                const res = await fetch('/api/export/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showToast('Error: ' + (err.error || res.statusText), 'error');
                    return;
                }
                const { task_id } = await res.json();
                const volName = (exportVolumeName.value || currentTitle.value || 'tomo').replace(/[^\w\-. ]/g, '_');
                exportTasks.value = {
                    ...exportTasks.value,
                    [task_id]: { task_id, status: 'queued', progress: 0, total: 0,
                                 title: currentTitle.value, filename: volName + '.' + fmt },
                };
                exportExcludedPages.value = [];
                scheduleExportPreview();
                showToast(`Exportando "${volName}" en segundo plano…`, 'info');
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            }
        };

        const pollExports = () => {
            setInterval(async () => {
                const active = Object.values(exportTasks.value)
                    .filter(t => t.status === 'queued' || t.status === 'running');
                for (const task of active) {
                    try {
                        const res = await fetch(`/api/export/status/${task.task_id}`);
                        if (!res.ok) continue;
                        const data = await res.json();
                        exportTasks.value = { ...exportTasks.value, [task.task_id]: { ...task, ...data } };
                        if (data.status === 'complete') {
                            _triggerFileDownload(`/api/export/file/${task.task_id}`, task.filename || data.filename || 'tomo.cbz');
                            showToast(`✅ "${task.title || 'Tomo'}" exportado`, 'success');
                            exportTasks.value = { ...exportTasks.value, [task.task_id]: { ...task, ...data, status: 'ready' } };
                        } else if (data.status === 'error') {
                            showToast(`Error exportando "${task.title}": ${data.error || ''}`, 'error');
                            setTimeout(() => {
                                const t = { ...exportTasks.value };
                                delete t[task.task_id];
                                exportTasks.value = t;
                            }, 6000);
                        }
                    } catch (e) {}
                }
            }, 1500);
        };

        // Sync running export tasks from server on page load (background persistence)
        const syncExportTasks = async () => {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                const serverExports = data.exports || {};
                const active = Object.values(serverExports).filter(t => t.status === 'queued' || t.status === 'running');
                if (active.length) {
                    const merged = { ...exportTasks.value };
                    for (const t of active) merged[t.task_id] = { ...t, filename: t.filename || `${t.volume_name || t.title}.cbz` };
                    exportTasks.value = merged;
                }
            } catch (e) {}
        };

        // ── Sources (Suwayomi) ──────────────────────────────────────────────────

        const checkSuwayomi = async () => {
            try {
                const res = await fetch('/api/sources/health');
                const data = await res.json();
                suwayomiOnline.value = data.online;
                if (data.online && sources.value.length === 0) await loadSources();
            } catch (e) {
                suwayomiOnline.value = false;
            }
        };

        const loadSources = async () => {
            try {
                const res = await fetch('/api/sources/list');
                if (res.ok) sources.value = await res.json();
            } catch (e) {}
        };

        const searchSources = async () => {
            if (!activeSource.value || !sourceQuery.value.trim()) return;
            sourcesLoading.value = true;
            sourceResults.value = [];
            try {
                const params = new URLSearchParams({
                    source: activeSource.value.id,
                    q: sourceQuery.value.trim(),
                    page: 1,
                });
                const res = await fetch(`/api/sources/search?${params}`);
                if (!res.ok) throw new Error(res.statusText);
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                sourceResults.value = data.results || [];
                sourceHasNextPage.value = data.hasNextPage || false;
            } catch (e) {
                showToast('Error buscando: ' + e.message, 'error');
            } finally {
                sourcesLoading.value = false;
            }
        };

        const searchAllSources = async () => {
            if (!globalQuery.value.trim()) return;
            globalSearchLoading.value = true;
            globalResults.value = [];
            try {
                const params = new URLSearchParams({ q: globalQuery.value.trim() });
                const res = await fetch(`/api/sources/search_all?${params}`);
                if (!res.ok) throw new Error(res.statusText);
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                globalResults.value = (data || []).filter(g => g.results.length > 0);
            } catch (e) {
                showToast('Error buscando: ' + e.message, 'error');
            } finally {
                globalSearchLoading.value = false;
            }
        };

        const openSourceManga = async (manga, sourceOverride) => {
            const source = sourceOverride || activeSource.value;
            const sourceId = source?.id ?? manga.sourceId;
            const mangaId = manga.id;

            currentTitle.value = manga.title;
            currentCover.value = manga.thumbnailUrl || null;
            currentMdManga.value = null; // this is a Suwayomi manga, not MangaDex
            currentManga.value = null;
            currentSourceContext.value = { sourceId, mangaId };
            mdChapters.value = [];
            chapters.value = [];
            chapterStatus.value = { downloaded: {}, upscaled: {} }; chapterHealth.value = [];
            exportSelectedChapters.value = [];
            currentModalTab.value = 'chapters';
            showModal.value = true;

            // Auto-write source_meta if this manga already has a local folder (retroactive fix)
            if (sourceId && mangaId) {
                fetch('/api/sources/save_to_library', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: manga.title,
                        sourceId,
                        mangaId,
                        thumbnailUrl: manga.thumbnailUrl || null,
                        onlyIfExists: true,
                    }),
                }).catch(() => {});
            }

            try {
                const res = await fetch(`/api/sources/manga/${mangaId}/chapters`);
                if (!res.ok) throw new Error(res.statusText);
                const raw = await res.json();
                if (raw.error) throw new Error(raw.error);
                mdChapters.value = raw.map(ch => ({
                    id: 'suw_' + ch.id,
                    suwayomiId: ch.id,
                    chapter: String(ch.chapterNumber ?? '0').replace(/\.0$/, ''),
                    title: ch.name || '',
                    language: activeSource.value?.lang || 'und',
                    scanlator: ch.scanlator || '',
                    pageCount: ch.pageCount || 0,
                }));
                // Sync local download/upscale status
                const statusRes = await fetch('/api/library/' + encodeURIComponent(manga.title)).catch(() => null);
                if (statusRes?.ok) {
                    const d = await statusRes.json().catch(() => ({}));
                    if (d.chapters?.length) {
                        const dl = {};
                        for (const c of d.chapters) { if (c.page_count > 0) dl[normalizeChapter(c.chapter)] = true; }
                        const up = {};
                        for (const key of Object.keys(d.upscaled || {})) { up[normalizeChapter(key)] = d.upscaled[key]; }
                        chapterStatus.value = { downloaded: dl, upscaled: up };
                    }
                }
            } catch (e) {
                showToast('Error cargando capítulos: ' + e.message, 'error');
            }
        };

        const _downloadFromSource = async (group) => {
            const title = currentTitle.value;
            const chapter = group?.chapter;
            if (!chapter || !title) return;
            const chapterNorm = normalizeChapter(chapter);

            const variant = group.variants?.find(v => v.suwayomiId) || group.variants?.[0];
            const suwayomiId = variant?.suwayomiId;
            if (!suwayomiId) { showToast('No se encontró el ID del capítulo en la fuente', 'error'); return; }

            const taskId = getTaskKey(chapter, 'download', title);
            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'download', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(chapter) } };
            showToast(`Descargando capítulo ${chapter} desde fuente...`, 'info');

            try {
                const pagesRes = await fetch(`/api/sources/chapter/${suwayomiId}/pages`);
                if (!pagesRes.ok) throw new Error('No se obtuvieron las páginas de la fuente');
                const pagesData = await pagesRes.json();

                const dlRes = await fetch('/api/download/download_source_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title,
                        chapter: chapterNorm,
                        pageUrls: pagesData.pages,
                        sourceId: currentSourceContext.value?.sourceId,
                        mangaId: currentSourceContext.value?.mangaId,
                    }),
                });
                const dlData = await dlRes.json();
                if (dlData.error) throw new Error(dlData.error);

                // Add manga to library immediately if not already present
                const norm = canonicalTitle(title);
                if (!library.value.some(m => canonicalTitle(m.name || m.title || '') === norm)) {
                    const thumbUrl = currentCover.value || null;
                    library.value = [...library.value, {
                        id: title, name: title, title,
                        chapter_count: 0, upscaled: 0,
                        cover: thumbUrl,
                        source_meta: currentSourceContext.value ? {
                            sourceId: String(currentSourceContext.value.sourceId),
                            mangaId: currentSourceContext.value.mangaId,
                            title,
                            thumbnailUrl: thumbUrl,
                        } : null,
                        cachedCover: thumbUrl,
                    }];
                }

                const realId = dlData.task_id || taskId;
                if (realId !== taskId && activeTasks.value[taskId]) {
                    const next = { ...activeTasks.value };
                    const cur = next[taskId];
                    delete next[taskId];
                    next[realId] = { ...cur, taskId: realId };
                    activeTasks.value = next;
                }
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
                activeTasks.value = { ...activeTasks.value, [taskId]: { ...activeTasks.value[taskId], status: 'error' } };
            }
        };

        // Init
        onMounted(() => {
            loadLibrary();
            loadLocalLibrary();
            loadMdLibrary();
            pollTasks();
            pollExports();
            syncExportTasks();
            checkSuwayomi();
            checkDrive();
            loadLibraryInfo();
            document.addEventListener('keydown', handleKeydown);
        });
        return {
            currentView, library, localLibrary, mdLibrary, searchQuery, searchResults,
            chapters, mdChapters, chapterStatus, showModal, showReader,
            currentManga, currentMdManga, currentTitle, currentCover, currentCoverUrl,
            currentChapter, currentPage, pages, isZoomed, selectedLang, toast,
            stats, groupedChapters, filteredChapters, availableLangs, currentPageUrl, combinedLibrary,
            getLangName, getLangFlag, getUniqueLangs, openManga, openMdManga, openLibraryItem, closeModal, addToLibrary, removeLibraryItem,
            isDownloaded, isUpscaled, isPartialUpscaled, getTask, getActiveTask, getProgressPercent,
            downloadChapter, upscaleChapter, cancelUpscale, cancelDownload, repairChapter, readChapter, readChapterOriginal, readChapterUpscaled, closeReader,
            chapterHealth, getChapterHealth, loadChapterHealth,
            nextPage, prevPage, toggleZoom, goToPage, onPageLoad, debouncedSearch,
            readerMode, fitMode, readingDir, readerZoom, panY, panX,
            readerBarsHidden, readerChapterList, readerChapterIndex, canGoPrevChapter, canGoNextChapter,
            isChapterRead, getLastReadProgress,
            goNextChapter, goPrevChapter, showBars, cycleFitMode, setReaderMode, toggleReaderDir,
            onPanStart, onPanMove, onPanEnd, handleReaderWheel, toggleFullscreen, onWebtoonScroll,
            isInLibrary, taskQueueExpanded, taskQueueList, taskQueueSummary, deleteChapter, downloadedLang, normalizeChapter, toggleChapterRead,
            currentModalTab, openTomoTab,
            exportVolumeName, exportFormat, exportQuality, exportDownscaleHalf, exportSelectedChapters, exportChapterSet, exportBusy, exportPreview,
            bulkFrom, bulkTo, bulkPreview, bulkDownload, bulkUpscale,
            rangeFrom, rangeTo, addChapterRange, onRowClickCapture,
            selectAllExportChapters, selectUpscaledExportChapters,
            downloadTomo, scheduleExportPreview, exportTasks,
            exportCoverMode, exportColorPages, exportColorPagesLoading,
            exportSelectedCover, exportUploadedCoverUrl,
            exportExcludedPages, toggleExcludePage,
            setCoverMode, onCoverUpload,
            mdexVolumes, mdexVolumesLoading, mdexCovers, mdexCoversLoading,
            mdexSelectedCover, mdexCoverLoadingId,
            mdexSearchQuery, mdexSearchResults, mdexSearchLoading,
            loadMdexVolumes, applyMdexVolume, loadMdexCovers, selectMdexCover,
            searchMdexForTomo, selectMdexEntry,
            handleCoverError: (e) => { e.target.style.display = 'none'; if (e.target.nextElementSibling) e.target.nextElementSibling.style.display = 'flex'; },
            sources, activeSource, sourceQuery, sourceResults, sourcesLoading, suwayomiOnline, sourceHasNextPage, currentSourceContext,
            globalQuery, globalResults, globalSearchLoading, searchMode,
            checkSuwayomi, loadSources, searchSources, searchAllSources, openSourceManga,
            activeTasksByTitle, canonicalTitle, seriesReadCounts,
            driveConfigured, driveConnected, driveEmail, driveUploading, driveUploadResult,
            connectDrive, disconnectDrive, uploadToDrive,
            compareMode, compareX, canCompare, readerSource,
            currentPageOrigUrl, currentPageUpUrl,
            toggleCompare, onCompareDragStart, onCompareDrag, onCompareDragEnd,
            libraryUrlLocal, libraryUrlPhone, libraryWslIp, libraryFileCount, libraryFwCmd, libraryProxyCmd,
            loadLibraryInfo, saveToLibrary, dismissExportTask,
        };
    }
});

app.mount('#app');