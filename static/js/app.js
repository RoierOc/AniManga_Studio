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
            const byKey = new Map();
            const output = [];

            for (const manga of library.value || []) {
                if (!manga) continue;
                const title = (manga.name || manga.title || '').trim();
                if (!title) continue;
                const key = canonicalTitle(title) || String(manga.id || title).toLowerCase();
                const card = {
                    key: `local:${manga.id || title}`,
                    title,
                    cover: manga.cachedCover || manga.cover || null,
                    chapter_count: manga.chapter_count || manga.page_count || 0,
                    upscaled: Number(manga.upscaled || 0),
                    downloaded: true,
                    localManga: manga,
                    mdManga: null
                };
                byKey.set(key, card);
                output.push(card);
            }

            for (const manga of localLibrary.value || []) {
                if (!manga) continue;
                const title = (manga.title || manga.name || '').trim();
                if (!title) continue;
                const key = canonicalTitle(title) || String(manga.id || title).toLowerCase();
                const mdManga = {
                    id: manga.id,
                    title,
                    cover: manga.cover || null,
                    status: manga.status || 'Guardado'
                };

                const existing = byKey.get(key);
                if (existing) {
                    if (!existing.cover && mdManga.cover) existing.cover = mdManga.cover;
                    if (!existing.mdManga && mdManga.id) existing.mdManga = mdManga;
                    continue;
                }

                const card = {
                    key: `saved:${manga.id || title}`,
                    title,
                    cover: mdManga.cover,
                    chapter_count: 0,
                    upscaled: 0,
                    downloaded: false,
                    localManga: null,
                    mdManga
                };
                byKey.set(key, card);
                output.push(card);
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
        
        // Load covers from MangaDex
        const loadCoverForManga = async (manga) => {
            if (manga.cachedCover) return manga.cachedCover;
            try {
                const searchRes = await fetch('/api/mangadex/search?q=' + encodeURIComponent(manga.name));
                const searchData = await searchRes.json();
                if (searchData?.[0]?.cover) {
                    manga.cachedCover = searchData[0].cover;
                    const idx = library.value.findIndex(m => m.id === manga.id);
                    // Force reactivity update
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
        const openManga = async (manga) => {
            currentManga.value = manga;
            currentMdManga.value = null;
            currentTitle.value = manga.name || manga.title;
            chapters.value = [];
            mdChapters.value = [];
            chapterStatus.value = { downloaded: {}, upscaled: {} };
            showModal.value = true;
            loadChapters(manga.name || manga.title);

            const cover = await loadCoverForManga(manga);
            currentCover.value = cover || manga.cover;

            // Also load MangaDex chapters so we can show all available chapters
            const titleHint = manga.name || manga.title;
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
                openManga(item.localManga);
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
        };
        
        // Add to local library
        // Add to local library
        const addToLibrary = async (manga) => {
            // Vue click handlers can pass DOM event when called as @click="addToLibrary"
            if (manga && typeof manga === 'object' && (typeof manga.preventDefault === 'function' || 'isTrusted' in manga)) {
                manga = null;
            }

            if (!manga) manga = currentMdManga.value || currentManga.value;

            // If the current manga doesn't have MangaDex id (local entry), resolve it by title.
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

                    // Keep modal context consistent once resolved.
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
            
            // Check if already in library  
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
                    // Also update the mdLibrary to reflect it's now in library
                    const idx = mdLibrary.value.findIndex(m => m.id === manga.id);
                    if (idx >= 0) {
                        mdLibrary.value[idx].inLibrary = true;
                    }
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
                } else if (data.task_id && data.task_id !== taskId && activeTasks.value[taskId]) {
                    const currentTask = activeTasks.value[taskId];
                    const nextTasks = { ...activeTasks.value };
                    delete nextTasks[taskId];
                    nextTasks[data.task_id] = { ...currentTask, taskId: data.task_id };
                    activeTasks.value = nextTasks;
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
        
        const readChapter = (ch) => {
            const title = currentTitle.value;
            if (!title) return;
            currentChapter.value = ch;
            fetch('/api/reader/read_chapter', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, chapter: ch })
            }).then(r => r.json()).then(d => {
                pages.value = d.pages || [];
                currentPage.value = 0;
            });
            showReader.value = true;
        };
        
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
                                // Reload chapters to update status
                                if (currentTitle.value) loadChapters(currentTitle.value);
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

        // Init
        onMounted(() => {
            loadLibrary();
            loadLocalLibrary();
            loadMdLibrary();
            pollTasks();
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
            downloadChapter, upscaleChapter, readChapter, closeReader,
            nextPage, prevPage, toggleZoom, goToPage, onPageLoad, debouncedSearch,
            isInLibrary, taskQueueExpanded, taskQueueList, deleteChapter, downloadedLang,
            handleCoverError: (e) => { e.target.style.display = 'none'; if (e.target.nextElementSibling) e.target.nextElementSibling.style.display = 'flex'; }
        };
    }
});

app.mount('#app');