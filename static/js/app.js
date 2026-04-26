// Manga Upscaler Pro - Full MangaDex-Style Vue 3 App

const { createApp, ref, computed, onMounted } = Vue;

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
        const exportBusy = ref(false);
        const exportPreview = ref({ pages: 0, size_mb: 0, upscaled_pages: 0 });
        let exportPreviewTimer = null;

        // Cover state
        const exportQuality = ref(85);           // JPEG quality for CBZ re-encoding

        const exportCoverMode = ref('none');   // 'none' | 'chapter' | 'upload'
        const exportColorPages = ref([]);
        const exportColorPagesLoading = ref(false);
        const exportSelectedCover = ref(null); // { path, url, label }
        const exportUploadedCoverB64 = ref('');
        const exportUploadedCoverUrl = ref('');
        
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
                    cover_path: exportSelectedCover.value?.path || '',
                    cover_data: exportUploadedCoverB64.value || '',
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
                upscaled: Object.values(chapterStatus.value.upscaled).filter(Boolean).length
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
        
        const availableLangs = computed(() => {
            const langs = new Set();
            for (const ch of mdChapters.value) if (ch.language) langs.add(ch.language);
            return Array.from(langs).sort();
        });
        
        const currentPageUrl = computed(() => {
            if (!pages.value.length) return '';
            return '/uploads/' + encodeURIComponent(pages.value[currentPage.value]);
        });
        
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
                        upscaled[normalizeChapter(key)] = Boolean(d.upscaled[key]);
                    }
                    chapterStatus.value = { downloaded, upscaled };
                });
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
            chapterStatus.value = { downloaded: {}, upscaled: {} };
            currentSourceContext.value = null;
            currentModalTab.value = 'chapters';
            exportSelectedChapters.value = [];
            showModal.value = true;

            const title = manga.name || manga.title;
            // Load local downloaded chapters + check for source metadata
            const libRes = await fetch('/api/library/' + encodeURIComponent(title)).catch(() => null);
            if (libRes?.ok) {
                const d = await libRes.json().catch(() => ({}));
                chapters.value = d.chapters || [];
                const dl = {}, up = {};
                for (const c of d.chapters || []) { if (c.page_count > 0) dl[normalizeChapter(c.chapter)] = true; }
                for (const key of Object.keys(d.upscaled || {})) { up[normalizeChapter(key)] = Boolean(d.upscaled[key]); }
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
            chapterStatus.value = { downloaded: {}, upscaled: {} };
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
                                upscaled[normalizeChapter(key)] = Boolean(d.upscaled[key]);
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
        const isUpscaled = (ch) => chapterStatus.value.upscaled[normalizeChapter(ch)];
        
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
        const downloadChapter = async (group, forceLang = null) => {
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
            showToast(`Descargando capítulo ${chapter}...`, 'info');
            
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
        
        const upscaleChapter = async (ch) => {
            const title = currentTitle.value;
            if (!title) return;
            const taskId = getTaskKey(ch, 'upscale', title);
            
            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'upscale', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(ch) } };
            showToast(`Upscaling capítulo ${ch}...`, 'info');
            
            try {
                const res = await fetch('/api/upscale/upscale_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: ch })
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
        
        const readChapter = (ch, source = 'auto') => {
            const title = currentTitle.value;
            if (!title) return;
            currentChapter.value = ch;
            fetch('/api/reader/read_chapter', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, chapter: ch, source })
            }).then(r => r.json()).then(d => {
                pages.value = d.pages || [];
                currentPage.value = 0;
            });
            showReader.value = true;
        };

        const readChapterOriginal = (ch) => readChapter(ch, 'original');
        const readChapterUpscaled = (ch) => readChapter(ch, 'upscaled');
        
        // Reader
        const closeReader = () => { showReader.value = false; pages.value = []; isZoomed.value = false; };
        const nextPage = () => { if (currentPage.value < pages.value.length - 1) currentPage.value++; };
        const prevPage = () => { if (currentPage.value > 0) currentPage.value--; };
        const toggleZoom = () => { isZoomed.value = !isZoomed.value; };
        const goToPage = (num) => { const p = parseInt(num) - 1; if (p >= 0 && p < pages.value.length) currentPage.value = p; };
        const onPageLoad = () => {};
        
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
                                if (currentTitle.value) loadChapters(currentTitle.value);
                                // Refresh library so card shows real chapter count
                                if (task.type === 'download') loadLibrary();
                            }, 2000);
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
            if (e.key === 'ArrowRight' || e.key === ' ') nextPage();
            if (e.key === 'ArrowLeft') prevPage();
            if (e.key === 'Escape') closeReader();
            if (e.key === 'z') toggleZoom();
        };
        
        const taskQueueList = computed(() => {
            return Object.entries(activeTasks.value).map(([id, task]) => ({
                id,
                ...task,
                percent: task.total > 0 ? Math.min(100, Math.round(((task.progress || 0) * 100) / task.total)) : 0
            }));
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
        };

        const loadColorPages = async () => {
            if (!currentTitle.value || !exportSelectedChapters.value.length) {
                exportColorPages.value = [];
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
                if (res.ok) exportColorPages.value = await res.json();
            } catch (e) {}
            exportColorPagesLoading.value = false;
        };

        const setCoverMode = (mode) => {
            exportCoverMode.value = mode;
            exportSelectedCover.value = null;
            exportUploadedCoverB64.value = '';
            exportUploadedCoverUrl.value = '';
            if (mode === 'chapter') loadColorPages();
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
                        body: JSON.stringify({ title: currentTitle.value, chapters: exportSelectedChapters.value, quality: exportQuality.value })
                    });
                    if (res.ok) exportPreview.value = await res.json();
                } catch (e) {}
            }, 400);
        };

        const selectAllExportChapters = () => {
            exportSelectedChapters.value = groupedChapters.value
                .filter(g => isDownloaded(g.chapter) || isUpscaled(g.chapter))
                .map(g => normalizeChapter(g.chapter));
            scheduleExportPreview();
        };

        const selectUpscaledExportChapters = () => {
            exportSelectedChapters.value = groupedChapters.value
                .filter(g => isUpscaled(g.chapter))
                .map(g => normalizeChapter(g.chapter));
            scheduleExportPreview();
        };

        const downloadTomo = async () => {
            if (!exportSelectedChapters.value.length) {
                showToast('Selecciona al menos un capítulo', 'warning');
                return;
            }
            exportBusy.value = true;
            try {
                const fmt = exportFormat.value || 'cbz';
                const coverPayload = {};
                if (exportCoverMode.value === 'chapter' && exportSelectedCover.value?.path) {
                    coverPayload.cover_path = exportSelectedCover.value.path;
                } else if (exportCoverMode.value === 'upload' && exportUploadedCoverB64.value) {
                    coverPayload.cover_data = exportUploadedCoverB64.value;
                }

                const res = await fetch('/api/export/cbz', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: currentTitle.value,
                        chapters: exportSelectedChapters.value,
                        volume_name: exportVolumeName.value || currentTitle.value,
                        format: fmt,
                        quality: exportQuality.value,
                        ...coverPayload,
                    })
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    showToast('Error: ' + (err.error || res.statusText), 'error');
                    return;
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                const safeName = (exportVolumeName.value || currentTitle.value || 'tomo').replace(/[^\w\-. ]/g, '_');
                a.href = url;
                a.download = safeName + '.' + fmt;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showToast('Tomo exportado', 'success');
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            } finally {
                exportBusy.value = false;
            }
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
            chapterStatus.value = { downloaded: {}, upscaled: {} };
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
                        for (const key of Object.keys(d.upscaled || {})) { up[normalizeChapter(key)] = Boolean(d.upscaled[key]); }
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
            checkSuwayomi();
            checkDrive();
            document.addEventListener('keydown', handleKeydown);
        });
        return {
            currentView, library, localLibrary, mdLibrary, searchQuery, searchResults,
            chapters, mdChapters, chapterStatus, showModal, showReader,
            currentManga, currentMdManga, currentTitle, currentCover, currentCoverUrl,
            currentChapter, currentPage, pages, isZoomed, selectedLang, toast,
            stats, groupedChapters, filteredChapters, availableLangs, currentPageUrl, combinedLibrary,
            getLangName, getLangFlag, getUniqueLangs, openManga, openMdManga, openLibraryItem, closeModal, addToLibrary, removeLibraryItem,
            isDownloaded, isUpscaled, getTask, getActiveTask, getProgressPercent,
            downloadChapter, upscaleChapter, readChapter, readChapterOriginal, readChapterUpscaled, closeReader,
            nextPage, prevPage, toggleZoom, goToPage, onPageLoad, debouncedSearch,
            isInLibrary, taskQueueExpanded, taskQueueList, deleteChapter, downloadedLang, normalizeChapter,
            currentModalTab, openTomoTab,
            exportVolumeName, exportFormat, exportQuality, exportSelectedChapters, exportBusy, exportPreview,
            selectAllExportChapters, selectUpscaledExportChapters,
            downloadTomo, scheduleExportPreview,
            exportCoverMode, exportColorPages, exportColorPagesLoading,
            exportSelectedCover, exportUploadedCoverUrl,
            setCoverMode, onCoverUpload,
            handleCoverError: (e) => { e.target.style.display = 'none'; if (e.target.nextElementSibling) e.target.nextElementSibling.style.display = 'flex'; },
            sources, activeSource, sourceQuery, sourceResults, sourcesLoading, suwayomiOnline, sourceHasNextPage, currentSourceContext,
            globalQuery, globalResults, globalSearchLoading, searchMode,
            checkSuwayomi, loadSources, searchSources, searchAllSources, openSourceManga,
            activeTasksByTitle, canonicalTitle,
            driveConfigured, driveConnected, driveEmail, driveUploading, driveUploadResult,
            connectDrive, disconnectDrive, uploadToDrive,
        };
    }
});

app.mount('#app');