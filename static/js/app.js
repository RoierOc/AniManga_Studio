// Manga Upscaler Pro - Full MangaDex-Style Vue 3 App

const { createApp, ref, computed, watch, onMounted } = Vue;

// Initialize
const app = createApp({
    setup() {
        // ── Session persistence ───────────────────────────────────────────────
        const _SESSION_KEY = 'animanga:session';
        const _sess = (() => { try { return JSON.parse(localStorage.getItem(_SESSION_KEY) || '{}'); } catch { return {}; } })();
        const _saveSession = () => {
            const av = animeView.value;
            localStorage.setItem(_SESSION_KEY, JSON.stringify({
                view: currentView.value,
                // only restore persistent sub-views, not ephemeral ones
                animeView: ['library','history'].includes(av) ? av : 'library',
                libFilter: libFilter.value,
                libSearch: libSearch.value,
                animeLibFilter: animeLibFilter.value,
                animeLibSort: animeLibSort.value,
            }));
        };

        // Views — restored from session
        const _VALID_VIEWS = new Set(['library','mangadex','followed','sources','anime','local']);
        const currentView = ref(_VALID_VIEWS.has(_sess.view) ? _sess.view : 'library');

        // Sidebar state
        const sidebarCollapsed  = ref(localStorage.getItem('manga-sb-collapsed') === '1');
        const sidebarMobileOpen = ref(false);

        const saveSidebarState = () => {
            localStorage.setItem('manga-sb-collapsed', sidebarCollapsed.value ? '1' : '0');
        };

        // Library search & filter — restored from session
        const libSearch = ref(_sess.libSearch || '');
        const libFilter = ref(_sess.libFilter || 'all'); // 'all' | 'downloaded' | 'upscaled' | 'saved' | 'updates'

        // Manga chapter updates
        const mangaUpdates = ref([]);
        const updatesLoading = ref(false);

        // Data
        const library = ref([]);
        const localLibrary = ref([]);
        const mdLibrary = ref([]);
        const searchQuery = ref('');
        const searchResults = ref([]);
        const chapters = ref([]);
        const mdChapters = ref([]);
        const mdChaptersLoading = ref(false);
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
        const showShortcuts = ref(false);
        const mainScrolled = ref(false);

        // Modal tab state
        const currentModalTab = ref('chapters');

        // Export / Tomo state
        const exportVolumeName = ref('');
        const exportFormat = ref('cbz');
        const exportSelectedChapters = ref([]);
        const exportChapterSet = computed(() => new Set(exportSelectedChapters.value.map(c => normalizeChapter(c))));
        const exportBusy = ref(false);
        const upscaleModel = ref('eula');        // 'eula' | 'mangajanai'
        const upscaleModelLabel = ref('eula-digimanga (B&W)');
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
        const sourceFolderName = ref('');   // editable folder name when opening from source search
        const globalQuery = ref('');
        const globalResults = ref([]);   // [{source:{id,name,lang}, results:[...]}]
        const globalSearchLoading = ref(false);
        const globalLang = ref('');
        const globalSearchDone = ref(0);
        const globalSearchTotal = ref(0);
        const searchMode = ref('global'); // 'global' | 'source' | 'popular'

        const sourceLangs = computed(() => {
            const seen = new Set();
            (sources.value || []).forEach(s => s.lang && seen.add(s.lang.toLowerCase()));
            return [...seen].sort();
        });

        // MangaDex popular/trending tabs
        const mdViewTab = ref('search');   // 'search' | 'popular' | 'latest' | 'rating' | 'anilist'
        const mdPopular = ref([]);
        const mdPopularLoading = ref(false);

        // AniList integration
        const anilistScores = ref({});          // malId (string) → { score, genres, al_id, popularity }
        const anilistTop = ref([]);
        const anilistTopLoading = ref(false);
        const anilistTopHasMore = ref(false);
        const anilistTopPage = ref(1);
        const anilistGenres = ref([]);       // [{name, type, category}]
        const anilistGenreFilter = ref('');  // selected name (string)
        const anilistTopSort = ref('SCORE_DESC');
        const anilistChipSearch = ref('');   // text filter for chips

        // MangaDex genre filters
        const mdTags = ref([]);
        const mdSelectedTags = ref([]);    // array of tag IDs
        const mdContentRating = ref(['safe', 'suggestive', 'erotica', 'pornographic']);
        const mdShowTagFilter = ref(false);

        // Full manga detail (synopsis, author, tags)
        const currentMdDetail = ref(null);

        // CBZ / Local Comics
        const cbzManga = ref([]);
        const cbzLoading = ref(false);
        const cbzCurrentManga = ref(null);
        const cbzVolumes = ref([]);
        const cbzVolumesLoading = ref(false);
        const showCbzModal = ref(false);

        // Suwayomi popular
        const sourcePopular = ref([]);
        const sourcePopularLoading = ref(false);
        const offlineCoverStatus = ref({ running: false, done: 0, total: 0, errors: 0 });
        let _offlinePollTimer = null;

        // Metadata edit
        const editMetaShow = ref(false);
        const editMetaTitle = ref('');
        const editMetaCoverUrl = ref('');
        const editMetaBusy = ref(false);

        // Corrupt page scan
        const corruptScanResult = ref(null);
        const corruptScanBusy = ref(false);

        // ── Anime / Nyaa ────────────────────────────────────────────────────────
        const animeView = ref(['library','history'].includes(_sess.animeView) ? _sess.animeView : 'library'); // 'library' | 'search' | 'detail' | 'downloads'
        const animeQuery = ref('');
        const animeResults = ref([]);
        const animeLoading = ref(false);
        const animeAnilistDown = ref(false);
        const animeCurrentAnime = ref(null);
        const animeTorrents = ref([]);
        const animeTorrentsLoading = ref(false);
        const animeTorrentQuery = ref('');
        const animeTorrentCategory = ref('1_2');
        const animeLangFilter = ref('all');       // 'all' | 'esp' | 'eng' | 'other'
        const animeHideDead  = ref(true);         // hide seeders === 0 by default
        const animeQualityFilter = ref('');
        const animeGroupFilter = ref('');
        const animeEpFilter = ref('all');         // 'all' | 'episodes' | 'batch'
        const targetEpisodeNum = ref(null);       // episode number when navigating from Mi Anime
        const qbtConnected = ref(false);
        const qbtVersion = ref('');
        const qbtUrl = ref('http://localhost:8080');
        const qbtUsername = ref('admin');
        const qbtPassword = ref('adminadmin');
        const qbtConfigShow = ref(false);
        const qbtTorrents = ref([]);
        const qbtLoading = ref(false);
        let qbtPollTimer = null;
        let libPollTimer  = null;
        const qbtAddingHashes = ref(new Set());
        const qbtAddedHashes  = ref(new Set());
        const animeLibrary = ref([]);
        const animeLibraryLoading = ref(false);
        const animeLibraryDetail  = ref(null);
        const animeLibSort   = ref(_sess.animeLibSort   || 'last_added');  // 'last_added'|'last_watched'|'last_downloaded'|'title'|'progress'|'episodes'|'status'
        const animeLibFilter = ref(_sess.animeLibFilter || 'all');     // 'all' | status values
        const animeLibSearch = ref('');

        // Watch status
        const ANIME_STATUS = {
            watching:      { label: 'Viendo',      color: '#56b870' },
            completed:     { label: 'Completado',  color: '#6b8fbd' },
            plan_to_watch: { label: 'Por ver',     color: '#9b7fb8' },
            on_hold:       { label: 'En pausa',    color: '#c9a84c' },
            dropped:       { label: 'Abandonado',  color: '#d45f5f' },
        };
        const linkTorrentShow   = ref(false);
        const linkTorrentList   = ref([]);
        const linkTorrentLoading = ref(false);
        const linkTorrentSubpath = ref('');
        const clearEpsConfirm   = ref(false);

        // Watch history
        const watchHistory       = ref([]);
        const watchHistoryLoaded = ref(false);

        // MangaDex new-chapter notifications
        const mangaNewChapters   = ref([]);  // [{manga_id, title, cover, new_count, new_chapters}]
        const mangaNewCount      = computed(() => mangaNewChapters.value.length);

        // Episode auto-renamer
        const renameAnime   = ref(null);
        const renameItems   = ref([]);
        const renameBusy    = ref(false);
        const renameChanges = computed(() => renameItems.value.filter(r => r.changed).length);

        // Scanlation comparison
        const scanCompareChapter  = ref(null);   // group object currently expanded
        const scanCompareVariants = ref([]);      // downloaded comparison copies for that chapter
        const scanCompareLoading  = ref(false);
        const scanCompareMode     = ref(false);   // true when reader is in scanlation-compare mode
        const comparePages2       = ref([]);      // pages of the comparison variant in reader
        const currentPageCompare2Url = computed(() => {
            if (!scanCompareMode.value || !comparePages2.value.length) return '';
            const idx = Math.min(currentPage.value, comparePages2.value.length - 1);
            return '/uploads/original/' + comparePages2.value[idx];
        });

        // Scan paths (local anime folders)
        const scanPathsShow      = ref(false);
        const scanPaths          = ref([]);
        const scanNewPath        = ref('');
        const scanFolders        = ref([]);
        const scanFoldersLoading = ref(false);

        // File browser
        const scanBrowsing     = ref(false);
        const scanBrowsePath   = ref('');
        const scanBrowseWinPath = ref('');
        const scanBrowseParent = ref(null);
        const scanBrowseItems  = ref([]);
        const scanBrowseLoading = ref(false);

        // Seasonal
        const animeSeasonalResults  = ref([]);
        const animeSeasonalLoading  = ref(false);
        const animeSeasonSort       = ref('score');   // 'score' | 'popularity' | 'trending'
        const animeSeasonSeason     = ref('');
        const animeSeasonYear       = ref(0);
        const animeSeasonGenreFilter = ref('');
        const SEASON_ES = { WINTER: 'Invierno', SPRING: 'Primavera', SUMMER: 'Verano', FALL: 'Otoño' };
        const SEASONS   = ['WINTER', 'SPRING', 'SUMMER', 'FALL'];

        const _navStack = [];           // SPA back-button stack

        // Search debounce
        let searchTimeout = null;

        const normalizeChapter = (value) => {
            if (value === null || value === undefined) return '';
            let raw = String(value).trim().toLowerCase();
            if (!raw) return '';
            if (raw === 'one_shot') return 'one_shot';
            if (raw.startsWith('ch')) raw = raw.slice(2);
            const num = Number(raw);
            if (!Number.isNaN(num) && Number.isFinite(num)) {
                if (Number.isInteger(num)) return String(num);
                return String(num).replace(/\.0+$/, '');
            }
            const stripped = raw.replace(/^0+/, '');
            return stripped || '0';
        };

        const formatChapter = (ch) => ch === 'one_shot' ? 'One Shot' : 'Cap. ' + ch;

        const animeFormatLabel = (fmt) => {
            const map = { TV: 'TV', TV_SHORT: 'TV Short', MOVIE: 'Película', OVA: 'OVA', ONA: 'ONA', SPECIAL: 'Especial', MUSIC: 'Video Musical' };
            return map[fmt] || fmt || '';
        };

        const animeEpLabel = (anime, ep) => {
            const fmt = anime?.format || '';
            // MOVIE/MUSIC always override the raw torrent filename stored in ep.title
            if (fmt === 'MOVIE') return 'Película';
            if (fmt === 'MUSIC') return 'Video Musical';
            if (ep.title) return ep.title;
            if (fmt === 'OVA') return 'OVA ' + ep.num;
            if (fmt === 'SPECIAL') return 'Especial ' + ep.num;
            return 'Episodio ' + ep.num;
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

        const filteredAnilistChips = computed(() => {
            const q = anilistChipSearch.value.trim().toLowerCase();
            if (!q) return anilistGenres.value;
            return anilistGenres.value.filter(g => g.name.toLowerCase().includes(q));
        });

        const filteredAnimeTorrents = computed(() => {
            let list = animeTorrents.value;
            // Minimum 1 seeder filter (on by default)
            if (animeHideDead.value) list = list.filter(t => t.seeders > 0);
            // Language filter
            if (animeLangFilter.value === 'esp')
                list = list.filter(t => isSpanishOrMulti(t.title));
            else if (animeLangFilter.value === 'eng')
                list = list.filter(t => isEnglishSub(t.title));
            else if (animeLangFilter.value === 'other')
                list = list.filter(t => !isSpanishOrMulti(t.title) && !isEnglishSub(t.title));
            if (animeQualityFilter.value)   list = list.filter(t => t.quality === animeQualityFilter.value);
            if (animeGroupFilter.value)     list = list.filter(t => t.group === animeGroupFilter.value);
            if (animeEpFilter.value === 'batch')    list = list.filter(t => t.episode === 0);
            else if (animeEpFilter.value === 'episodes') list = list.filter(t => t.episode > 0);
            // Global sort: ESP first → ENG → others, then seeders desc
            return [...list].sort((a, b) => {
                const aEsp = isSpanishOrMulti(a.title), bEsp = isSpanishOrMulti(b.title);
                const aEng = isEnglishSub(a.title),     bEng = isEnglishSub(b.title);
                if (aEsp !== bEsp) return aEsp ? -1 : 1;
                if (aEng !== bEng) return aEng ? -1 : 1;
                return b.seeders - a.seeders;
            });
        });

        // Counts per language bucket (from raw results, ignoring dead filter)
        const animeLangCounts = computed(() => {
            const all  = animeTorrents.value.filter(t => animeHideDead.value ? t.seeders > 0 : true);
            return {
                all:   all.length,
                esp:   all.filter(t => isSpanishOrMulti(t.title)).length,
                eng:   all.filter(t => isEnglishSub(t.title)).length,
                other: all.filter(t => !isSpanishOrMulti(t.title) && !isEnglishSub(t.title)).length,
            };
        });

        const animeGroups = computed(() => {
            const groups = new Set(animeTorrents.value.map(t => t.group).filter(Boolean));
            return [...groups].sort();
        });

        const animeQualities = computed(() => {
            const q = new Set(animeTorrents.value.map(t => t.quality).filter(Boolean));
            return [...q].sort();
        });

        const isSpanishOrMulti = (title) =>
            /\b(esp|espa[nñ]ol|castellano|multi|lat|latino|multi.?sub|sub.?esp|dual)\b/i.test(title);

        const isEnglishSub = (title) => {
            if (isSpanishOrMulti(title)) return false;
            return /\b(eng(?:lish)?[\s._-]?(?:sub(?:bed)?|dub(?:bed)?)?|english[\s._-]?(?:sub(?:bed)?|dubbed)?|\[en\]|\[eng\])\b/i.test(title);
        };

        const animeExpandedEps = ref(new Set());
        const toggleEpGroup = (ep) => {
            const s = new Set(animeExpandedEps.value);
            if (s.has(ep)) s.delete(ep); else s.add(ep);
            animeExpandedEps.value = s;
        };
        const isEpExpanded = (ep) => animeExpandedEps.value.has(ep);

        const groupedAnimeEpisodes = computed(() => {
            let list = filteredAnimeTorrents.value.map(t => ({
                ...t,
                isSpanish: isSpanishOrMulti(t.title),
                isEnglish: isEnglishSub(t.title),
            }));
            const map = {};
            for (const t of list) {
                const key = t.episode;
                if (!map[key]) map[key] = [];
                map[key].push(t);
            }
            // Spanish/multi first → English → rest, then by seeders desc
            for (const key in map) {
                map[key].sort((a, b) => {
                    if (a.isSpanish !== b.isSpanish) return a.isSpanish ? -1 : 1;
                    if (a.isEnglish !== b.isEnglish) return a.isEnglish ? -1 : 1;
                    return b.seeders - a.seeders;
                });
            }
            // Batch (0) first, then episodes asc, unknowns (-1) last
            let groups = Object.entries(map)
                .map(([k, torrents]) => ({ episode: Number(k), torrents }))
                .sort((a, b) => {
                    if (a.episode === 0) return -1;
                    if (b.episode === 0) return 1;
                    if (a.episode === -1) return 1;
                    if (b.episode === -1) return -1;
                    return a.episode - b.episode;
                });
            // When coming from Mi Anime for a specific episode, show only that ep + batches
            if (targetEpisodeNum.value !== null) {
                groups = groups.filter(g => g.episode === 0 || g.episode === targetEpisodeNum.value);
            }
            return groups;
        });

        const updatesById = computed(() => {
            const m = {};
            for (const u of mangaUpdates.value) m[u.manga_id] = u;
            return m;
        });

        const loadMangaUpdates = async () => {
            updatesLoading.value = true;
            try {
                const r = await fetch('/api/mangadex/updates');
                if (r.ok) mangaUpdates.value = (await r.json()) || [];
            } catch (_) {}
            updatesLoading.value = false;
        };

        const refreshMangaUpdates = async () => {
            await fetch('/api/mangadex/updates/refresh', { method: 'POST' });
            await loadMangaUpdates();
        };

        const filteredLibrary = computed(() => {
            let items = combinedLibrary.value;
            const q = libSearch.value.trim().toLowerCase();
            if (q) items = items.filter(i => i.title.toLowerCase().includes(q));
            if (libFilter.value === 'downloaded') items = items.filter(i => i.downloaded);
            else if (libFilter.value === 'upscaled')  items = items.filter(i => i.upscaled > 0);
            else if (libFilter.value === 'saved')     items = items.filter(i => !i.downloaded);
            else if (libFilter.value === 'updates')   items = items.filter(i => i.mdManga && updatesById.value[i.mdManga.id]);
            return items;
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

        const LANG_PRIORITY = ['en', 'es', 'es-la', 'pt-br', 'fr', 'de', 'it', 'ko', 'zh', 'ja', 'ru', 'vi', 'th', 'id', 'tr', 'pl', 'cs', 'hu', 'ro', 'sv', 'uk'];
        const langPrio = (code) => { const i = LANG_PRIORITY.indexOf(code); return i >= 0 ? i : 99; };

        const availableLangs = computed(() => {
            const langs = new Set();
            for (const ch of mdChapters.value) if (ch.language) langs.add(ch.language);
            return Array.from(langs).sort((a, b) => langPrio(a) - langPrio(b));
        });
        
        const currentPageUrl = computed(() => {
            if (!pages.value.length) return '';
            const p = pages.value[currentPage.value];
            if (p.startsWith('/')) return p;   // CBZ or other absolute path
            return '/uploads/' + encodeURIComponent(p);
        });

        // Compare mode — explicit original / upscaled URLs for the same page path
        const currentPageOrigUrl = computed(() => {
            if (!pages.value.length) return '';
            const p = pages.value[currentPage.value];
            if (p.startsWith('/')) return p;
            return '/uploads/original/' + encodeURIComponent(p);
        });
        const currentPageUpUrl = computed(() => {
            // In scanlation compare mode use the second variant's page
            if (scanCompareMode.value && comparePages2.value.length) {
                const idx = Math.min(currentPage.value, comparePages2.value.length - 1);
                const p2 = comparePages2.value[idx];
                return p2.startsWith('/') ? p2 : '/uploads/original/' + encodeURIComponent(p2);
            }
            if (!pages.value.length) return '';
            const p = pages.value[currentPage.value];
            if (p.startsWith('/')) return p;
            return '/uploads/upscaled/' + encodeURIComponent(p);
        });
        const canCompare = computed(() =>
            readerMode.value === 'paged' &&
            (scanCompareMode.value ||
             readerSource.value === 'upscaled' ||
             isUpscaled(currentChapter.value) ||
             isPartialUpscaled(currentChapter.value))
        );
        
        // Load library WITH covers (PARALLEL for speed)
        const loadLibrary = async () => {
            try {
                const res = await fetch('/api/library');
                const data = await res.json();
                library.value = data || [];
                // Fire-and-forget: covers load in background, don't block the grid render
                if (data.length > 0) Promise.all(data.map(m => loadCoverForManga(m)));
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
            }).sort((a, b) => langPrio(a.language) - langPrio(b.language));
        };
        
        // Load covers from MangaDex — skip Mihon manga (they come from a different source)
        const loadCoverForManga = async (manga) => {
            if (manga.cachedCover) return manga.cachedCover;
            // Backend already resolved a cover (from local_library.json or disk cache) — use it
            if (manga.cover) { manga.cachedCover = manga.cover; return manga.cover; }
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
                    const coverUrl = searchData[0].cover;
                    manga.cachedCover = coverUrl;
                    const idx = library.value.findIndex(m => m.id === manga.id);
                    if (idx >= 0) {
                        library.value = [...library.value];
                        library.value[idx].cachedCover = coverUrl;
                    }
                    // Persist to disk so backend returns it directly on next load
                    fetch('/api/library/cache_cover', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: manga.name, url: coverUrl }),
                    }).catch(() => {});
                }
            } catch (e) {
                console.log('Error loading cover for', manga.name, e.message);
            }
            return manga.cachedCover;
        };
        
        const loadLocalLibrary = () => fetch('/api/mangadex/local_library').then(r => r.json()).then(d => localLibrary.value = d || []);
        const loadMdLibrary = () => fetch('/api/mangadex/library').then(r => r.json()).then(d => mdLibrary.value = d || []);

        const _pollOfflineStatus = () => {
            fetch('/api/library/offline_covers_status').then(r => r.json()).then(d => {
                offlineCoverStatus.value = d;
                if (d.running) {
                    _offlinePollTimer = setTimeout(_pollOfflineStatus, 800);
                } else {
                    _offlinePollTimer = null;
                    if (d.done > 0) loadLibrary();  // refresh covers in grid
                }
            }).catch(() => { _offlinePollTimer = null; });
        };

        const downloadCoversOffline = async () => {
            const r = await fetch('/api/library/download_covers_offline', { method: 'POST' }).catch(() => null);
            if (!r?.ok) { showToast('Error al iniciar descarga', 'error'); return; }
            const d = await r.json();
            if (!d.ok) { showToast(d.message || 'Error', 'error'); return; }
            if (d.total === 0) { showToast('Todas las portadas ya están guardadas', 'info'); return; }
            showToast(`Descargando ${d.total} portadas...`, 'info');
            offlineCoverStatus.value = { running: true, done: 0, total: d.total, errors: 0 };
            _pollOfflineStatus();
        };
        
        const openEditMeta = () => {
            editMetaTitle.value = currentTitle.value;
            editMetaCoverUrl.value = '';
            editMetaShow.value = true;
        };

        const saveEditMeta = async () => {
            editMetaBusy.value = true;
            try {
                const body = {};
                if (editMetaTitle.value !== currentTitle.value) body.new_title = editMetaTitle.value;
                if (editMetaCoverUrl.value) body.cover_url = editMetaCoverUrl.value;
                const res = await fetch(`/api/library/meta/${encodeURIComponent(currentTitle.value)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const d = await res.json();
                if (!res.ok) { showToast(d.error || 'Error', 'error'); return; }
                editMetaShow.value = false;
                if (d.new_title) {
                    currentTitle.value = d.new_title;
                    if (currentManga.value) currentManga.value = { ...currentManga.value, name: d.new_title, id: d.new_title };
                }
                await loadLibrary();
                showToast('Guardado', 'success');
            } finally {
                editMetaBusy.value = false;
            }
        };

        const scanCorruptPages = async () => {
            corruptScanBusy.value = true;
            corruptScanResult.value = null;
            try {
                const res = await fetch(`/api/library/scan_corrupt/${encodeURIComponent(currentTitle.value)}`);
                corruptScanResult.value = await res.json();
                if (corruptScanResult.value.corrupt?.length === 0) showToast('Sin páginas corruptas', 'success');
            } catch (_) {
                showToast('Error al escanear', 'error');
            } finally {
                corruptScanBusy.value = false;
            }
        };

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
            mdChaptersLoading.value = true;
            fetch('/api/mangadex/chapters/' + mangaId).then(r => r.json()).catch(() => [])
                .then(d => { if (Array.isArray(d)) mdChapters.value = d; })
                .finally(() => { mdChaptersLoading.value = false; });
        };
        
        // Search
        const _mdSearchParams = () => {
            const p = new URLSearchParams();
            if (searchQuery.value.length >= 2) p.set('q', searchQuery.value);
            mdSelectedTags.value.forEach(id => p.append('tags[]', id));
            mdContentRating.value.forEach(r => p.append('rating[]', r));
            return p;
        };

        const debouncedSearch = () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                const hasQuery = searchQuery.value.length >= 2;
                const hasTags  = mdSelectedTags.value.length > 0;
                if (!hasQuery && !hasTags) { searchResults.value = []; return; }
                fetch('/api/mangadex/search?' + _mdSearchParams())
                    .then(r => r.json()).then(d => searchResults.value = d || []);
            }, 300);
        };

        const toggleMdTag = (tagId) => {
            const idx = mdSelectedTags.value.indexOf(tagId);
            if (idx === -1) mdSelectedTags.value.push(tagId);
            else mdSelectedTags.value.splice(idx, 1);
            if (mdViewTab.value === 'search') debouncedSearch();
            else loadMdPopular(mdViewTab.value);
        };

        const toggleMdRating = (rating) => {
            const idx = mdContentRating.value.indexOf(rating);
            if (idx === -1) mdContentRating.value.push(rating);
            else if (mdContentRating.value.length > 1) mdContentRating.value.splice(idx, 1);
            if (mdViewTab.value === 'search') debouncedSearch();
            else loadMdPopular(mdViewTab.value);
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
            currentMdDetail.value = null;
            showModal.value = true;
            loadMdChapters(manga.id);
            // Load full metadata (synopsis, author, tags) in background
            fetch(`/api/mangadex/manga/${manga.id}`)
                .then(r => r.ok ? r.json() : null)
                .then(d => { if (d && !d.error) currentMdDetail.value = d; })
                .catch(() => {});
            // Also check local chapters (needed for Tomo builder when manga was
            // downloaded locally but opened from MangaDex search/followed view)
            if (manga.title) {
                fetch('/api/library/' + encodeURIComponent(manga.title))
                    .then(r => r.json()).catch(() => ({}))
                    .then(d => {
                        if (d.chapters?.length) {
                            chapters.value = d.chapters;
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
                // Skip MangaDex search only if a Suwayomi/Mihon source or a paired MD entry is already known.
                // Otherwise fall through so the chapter list auto-loads from MangaDex by title search.
                const hasSuwayomi = !!(item.source_meta?.sourceId);
                const hasMdPair   = !!(item.mdManga?.id);
                openManga(item.localManga, {
                    skipMangaDexLookup: hasSuwayomi || hasMdPair,
                    forcedSourceMeta: item.source_meta || null,
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
            sourceFolderName.value = '';
            editMetaShow.value = false;
            corruptScanResult.value = null;
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
            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'download', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: chapter === 'one_shot' ? 'One Shot' : String(chapter) } };
            if (!silent) showToast(`Descargando ${chapter === 'one_shot' ? 'One Shot' : 'capítulo ' + chapter}...`, 'info');
            
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
        
        const upscaleChapter = async (ch, mode = 'full', { silent = false, fast = false } = {}) => {
            const title = currentTitle.value;
            if (!title) return;
            const taskId = getTaskKey(ch, 'upscale', title);

            activeTasks.value = { ...activeTasks.value, [taskId]: { type: 'upscale', status: 'starting', progress: 0, total: 0, misses: 0, displayTitle: title, displayChapter: String(ch), mode, fast } };
            if (!silent) {
                const label = fast ? '2x⚡' : (mode === 'eco' ? 'eco' : '4x');
                showToast(`Upscale ${label} cap. ${ch}...`, 'info');
            }

            try {
                const res = await fetch('/api/upscale/upscale_chapter', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, chapter: ch, mode, fast_mode: fast })
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
            scanCompareMode.value = false; comparePages2.value = [];
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
        const onCompareDrag = (e) => {
            if (!compareDragging) return;
            const wrap = document.querySelector('.reader-compare-wrap');
            if (!wrap) return;
            const rect = wrap.getBoundingClientRect();
            compareX.value = Math.max(3, Math.min(97, ((e.clientX - rect.left) / rect.width) * 100));
        };
        const _onCompareDocMouseUp = () => {
            compareDragging = false;
            document.removeEventListener('mousemove', onCompareDrag);
            document.removeEventListener('mouseup', _onCompareDocMouseUp);
        };
        const onCompareDragStart = (e) => {
            compareDragging = true;
            e.stopPropagation();
            e.preventDefault();
            document.addEventListener('mousemove', onCompareDrag);
            document.addEventListener('mouseup', _onCompareDocMouseUp);
        };
        const onCompareDragEnd = () => { compareDragging = false; };

        // ── Mobile library ───────────────────────────────────────────────────
        const libraryUrlLocal = ref('http://localhost:5100/library');
        const libraryUrlPhone = ref('');
        const libraryWslIp = ref('');
        const libraryFileCount = ref(0);
        const libraryFwCmd = 'New-NetFirewallRule -DisplayName "MangaUpscaler Web" -Direction Inbound -Protocol TCP -LocalPort 5100 -Action Allow -Profile Any';
        const libraryProxyCmd = computed(() =>
            `netsh interface portproxy add v4tov4 listenport=5100 listenaddress=0.0.0.0 connectport=5100 connectaddress=${libraryWslIp.value}`
        );

        const loadLibraryInfo = async () => {
            try {
                const r = await fetch('/api/webdav/status');
                const d = await r.json();
                libraryUrlLocal.value = d.library_url_local || 'http://localhost:5100/library';
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

        const downloadExportFile = (taskId) => {
            const task = exportTasks.value[taskId];
            if (!task) return;
            _triggerFileDownload(`/api/export/file/${taskId}`, task.filename || 'tomo.cbz');
            dismissExportTask(taskId);
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
        
        let _toastTimer = null;
        const showToast = (msg, type = 'info', duration = 3500) => {
            if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
            toast.value = { show: true, message: msg, type };
            _toastTimer = setTimeout(() => { toast.value.show = false; _toastTimer = null; }, duration);
        };
        const dismissToast = () => {
            if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
            toast.value.show = false;
        };
        const scrollToTop = () => {
            document.querySelector('.main-wrapper')?.scrollTo({ top: 0, behavior: 'smooth' });
        };
        
        // SSE — replaces pollTasks + pollExports setIntervals
        const initSSE = () => {
            const sse = new EventSource('/api/status/stream');
            const _activeStatuses = new Set(['downloading','upscaling','upscaleing','starting','started','queued']);
            const _completedSeen = new Set(); // prevent duplicate toasts on reconnect

            sse.onmessage = (event) => {
                let data;
                try { data = JSON.parse(event.data); } catch (e) { return; }

                // ── Discover tasks running on backend that this tab doesn't know yet ──
                // Handles page refresh during active download/upscale.
                const pools = [
                    { pool: data.downloads || {}, type: 'download' },
                    { pool: data.upscale   || {}, type: 'upscale'  },
                ];
                for (const { pool, type } of pools) {
                    for (const [taskId, st] of Object.entries(pool)) {
                        if (activeTasks.value[taskId]) continue;
                        if (!_activeStatuses.has(st.status)) continue;
                        const pct = st.percent ?? (st.total ? Math.round(((st.progress || 0) / st.total) * 100) : 0);
                        activeTasks.value = {
                            ...activeTasks.value,
                            [taskId]: {
                                type, status: st.status,
                                progress: st.progress ?? 0, total: st.total || 1, percent: pct,
                                misses: 0,
                                displayTitle: st.title || taskId.replace(/_(?:download|upscale)_ch.*$/, '').replace(/_/g, ' '),
                                displayChapter: st.chapter || taskId.replace(/.*_ch/, ''),
                            }
                        };
                    }
                }

                // ── Known task updates ────────────────────────────────────────
                const keys = Object.keys(activeTasks.value);
                for (const taskId of keys) {
                    const task = activeTasks.value[taskId];
                    if (!task || task.status === 'complete') continue;

                    const pool = task.type === 'download' ? (data.downloads || {}) : (data.upscale || {});
                    const st = pool[taskId];

                    if (!st) {
                        const misses = (task.misses || 0) + 1;
                        if (misses >= 8) {
                            showToast(task.type === 'download' ? 'Error en descarga' : 'Error en upscale', 'error');
                            const t = { ...activeTasks.value }; delete t[taskId]; activeTasks.value = t;
                        } else {
                            activeTasks.value = { ...activeTasks.value, [taskId]: { ...task, misses } };
                        }
                        continue;
                    }

                    if (_activeStatuses.has(st.status)) {
                        const pct = st.percent ?? (st.total ? Math.round(((st.progress || 0) / st.total) * 100) : 0);
                        activeTasks.value = {
                            ...activeTasks.value,
                            [taskId]: { ...task, status: st.status, progress: st.progress ?? st.current ?? 0, total: st.total || 1, percent: pct, misses: 0 }
                        };
                    } else if (st.status === 'complete' && !_completedSeen.has(taskId)) {
                        _completedSeen.add(taskId);
                        showToast(task.type === 'download' ? '✅ Descarga completa' : '🔥 Upscale completo', 'success');
                        const t = { ...activeTasks.value }; delete t[taskId]; activeTasks.value = t;
                        if (currentTitle.value) {
                            loadChapters(currentTitle.value);
                            if (task.type === 'upscale') loadChapterHealth(currentTitle.value);
                        }
                        if (task.type === 'download') loadLibrary();
                    } else if (st.status === 'cancelled') {
                        const t = { ...activeTasks.value }; delete t[taskId]; activeTasks.value = t;
                    } else if (st.status === 'error') {
                        showToast(task.type === 'download' ? 'Error en descarga' : 'Error en upscale', 'error');
                        const t = { ...activeTasks.value }; delete t[taskId]; activeTasks.value = t;
                    }
                }

                // ── Exports ───────────────────────────────────────────────────
                const activeExports = Object.values(exportTasks.value).filter(t => t.status === 'queued' || t.status === 'running');
                for (const task of activeExports) {
                    const st = (data.exports || {})[task.task_id];
                    if (!st) continue;
                    exportTasks.value = { ...exportTasks.value, [task.task_id]: { ...task, ...st } };
                    if (st.status === 'complete') {
                        showToast(`✅ "${task.title || 'Tomo'}" exportado — listo para descargar`, 'success');
                        exportTasks.value = { ...exportTasks.value, [task.task_id]: { ...task, ...st, status: 'ready' } };
                    } else if (st.status === 'error') {
                        showToast(`Error exportando "${task.title}": ${st.error || ''}`, 'error');
                        setTimeout(() => {
                            const t = { ...exportTasks.value }; delete t[task.task_id]; exportTasks.value = t;
                        }, 6000);
                    }
                }

                // ── Push events (watched, download_complete, …) ───────────────
                for (const ev of (data.events || [])) {
                    if (ev.type === 'watched') {
                        const aid = ev.anime_id;
                        const newState = ev.watched !== undefined ? ev.watched : true;
                        const applyWatched = (animeObj) => {
                            if (!animeObj) return;
                            const ep = (animeObj.episodes || []).find(e => String(e.num) === ev.ep_str);
                            if (ep) {
                                ep.watched = newState;
                                ep.resume_pos = 0;
                                if (ev.duration) ep.duration = ev.duration;
                            }
                            if (newState && ev.last_watched_at) animeObj.last_watched_at = ev.last_watched_at;
                        };
                        applyWatched(animeLibrary.value.find(a => a.id === aid));
                        if (animeLibraryDetail.value?.id === aid) applyWatched(animeLibraryDetail.value);
                    } else if (ev.type === 'position') {
                        const aid = ev.anime_id;
                        const applyPos = (animeObj) => {
                            if (!animeObj) return;
                            const ep = (animeObj.episodes || []).find(e => String(e.num) === ev.ep_str);
                            if (ep) {
                                ep.resume_pos = ev.position || 0;
                                if (ev.duration) ep.duration = ev.duration;
                            }
                        };
                        applyPos(animeLibrary.value.find(a => a.id === aid));
                        if (animeLibraryDetail.value?.id === aid) applyPos(animeLibraryDetail.value);
                    } else if (ev.type === 'download_complete') {
                        // Immediate chapter list refresh when the open manga just got a chapter
                        if (currentTitle.value && canonicalTitle(currentTitle.value) === canonicalTitle(ev.title || '')) {
                            loadChapters(currentTitle.value);
                        }
                        loadLibrary();
                    }
                }
            };

            sse.onerror = () => { /* EventSource auto-reconnects */ };
        };

        // Keep stub names so callers in onMounted still work during the transition
        const pollTasks = () => {};
        const pollExports = () => {};
        
        // Keyboard
        const handleKeydown = (e) => {
            // Global shortcuts — work outside reader too (skip when typing in inputs)
            const isInput = ['INPUT','TEXTAREA','SELECT'].includes(e.target?.tagName);
            if (!isInput) {
                if (e.key === '?' && !showReader.value && !showModal.value) {
                    showShortcuts.value = !showShortcuts.value;
                    return;
                }
                if (e.key === 'Escape') {
                    if (showShortcuts.value) { showShortcuts.value = false; return; }
                }
            }
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
            return Object.entries(activeTasks.value)
                .filter(([, task]) => task.status !== 'complete')
                .map(([id, task]) => ({
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
                // Load volumes, covers, and chapter list in parallel.
                // The chapter list must be ready before volumes become interactive so that
                // applyMdexVolume's localChaps filter has data to work with.
                const chapPromise = mdChapters.value.length
                    ? Promise.resolve()
                    : fetch('/api/mangadex/chapters/' + id).then(r => r.json()).catch(() => [])
                        .then(d => { if (Array.isArray(d)) mdChapters.value = d; });

                const [volRes, covRes] = await Promise.all([
                    fetch(`/api/mangadex/volumes/${id}`),
                    fetch(`/api/mangadex/covers/${id}`)
                ]);
                await chapPromise;

                let volumes = volRes.ok ? await volRes.json() : [];
                if (!Array.isArray(volumes)) volumes = [];
                const covers = covRes.ok ? await covRes.json() : [];

                // Scrape when: (a) API returned zero chapter data (manga fully unlisted), OR
                // (b) aggregate was empty and we fell back to the feed — feed volume assignments
                // can be wrong when multiple scanlation groups tag chapters with different volumes.
                // The scraper reads the canonical MangaDex website display, which is always correct.
                const apiVolCount = volumes.filter(v => v.chapters && v.chapters.length).length;
                const feedFallback = volumes.some(v => v.feedFallback);
                if (apiVolCount === 0 || feedFallback) {
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

            // Prefer locally downloaded chapters for the Tomo builder — they represent what
            // the user actually has. When opened from MangaDex search, groupedChapters uses
            // the sparse API list (e.g. 9 out of 133 chapters), which leaves most volumes empty.
            const chapSrc = chapters.value.length > 0 ? chapters.value : groupedChapters.value;
            const localChaps = chapSrc
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

                    const base = Math.floor(mainChaps.length / count);
                    // All extra chapters go to the LAST volume: vol 1..V-1 get exactly base
                    // chapters each, and the last volume absorbs any remainder. This is more
                    // accurate than spreading 1 extra across the last N volumes, which bloats
                    // early volumes that almost always have a uniform chapter count.
                    const slices = [];
                    for (let i = 0; i < count; i++) {
                        const start  = i * base;
                        const end    = (i === count - 1) ? mainChaps.length : start + base;
                        slices.push(mainChaps.slice(start, end).map(g => g.norm));
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
                // Volume has API chapter data — use strict API range.
                // Filter out special chapters (< 1, e.g. "0.1" prologues) for boundary math.
                const nums = vol.chapters.map(Number).filter(n => !isNaN(n));
                const regular = nums.filter(n => n >= 1);
                const anchor = regular.length ? regular : nums;
                const minCh = Math.min(...anchor);
                const maxCh = Math.max(...anchor);
                const inRange = localChaps.filter(g => g.num >= minCh && g.num <= maxCh);
                if (inRange.length > 0) {
                    selected = inRange.map(g => g.norm);
                } else if (!vol.fromScrape) {
                    // Aggregate data lists every chapter — use it directly when localChaps is empty
                    // (manga not downloaded yet, or user is browsing from MangaDex search).
                    selected = vol.chapters.map(c => normalizeChapter(String(c))).filter(c => c !== '');
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

        const setUpscaleModel = async (key) => {
            try {
                const res = await fetch('/api/upscale/set_model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: key }),
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    upscaleModel.value = key;
                    upscaleModelLabel.value = data.label;
                    showToast(`Modelo: ${data.label}`, 'success');
                } else {
                    showToast(`Error al cambiar modelo: ${data.message}`, 'error');
                }
            } catch (e) {
                showToast('Error al cambiar modelo', 'error');
            }
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

        // pollExports is now handled inside initSSE via the SSE stream

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
                if (data.online) {
                    if (sources.value.length === 0) await loadSources();
                    // Enrich existing library items with source name/lang if missing
                    fetch('/api/sources/enrich_library', { method: 'POST' })
                        .then(r => r.json())
                        .then(d => { if (d.count > 0) loadLibrary(); })
                        .catch(() => {});
                }
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

        let _globalSearchES = null;
        const searchAllSources = () => {
            if (!globalQuery.value.trim()) return;
            if (_globalSearchES) { _globalSearchES.close(); _globalSearchES = null; }
            globalSearchLoading.value = true;
            globalResults.value = [];
            globalSearchDone.value = 0;
            globalSearchTotal.value = 0;
            const params = new URLSearchParams({ q: globalQuery.value.trim() });
            if (globalLang.value) params.set('lang', globalLang.value);
            const es = new EventSource(`/api/sources/search_all_stream?${params}`);
            _globalSearchES = es;
            es.onmessage = (e) => {
                const msg = JSON.parse(e.data);
                if (msg.type === 'start') {
                    globalSearchTotal.value = msg.total;
                } else if (msg.type === 'result') {
                    globalSearchDone.value = msg.done;
                    globalSearchTotal.value = msg.total;
                    globalResults.value = [...globalResults.value, { source: msg.source, results: msg.results }];
                } else if (msg.type === 'progress') {
                    globalSearchDone.value = msg.done;
                    globalSearchTotal.value = msg.total;
                } else if (msg.type === 'done') {
                    globalSearchLoading.value = false;
                    es.close();
                    _globalSearchES = null;
                }
            };
            es.onerror = () => {
                globalSearchLoading.value = false;
                es.close();
                _globalSearchES = null;
                if (globalSearchDone.value === 0) showToast('Error en búsqueda global', 'error');
            };
        };

        const openSourceManga = async (manga, sourceOverride) => {
            const source = sourceOverride || activeSource.value;
            const sourceId = source?.id ?? manga.sourceId;
            const mangaId = manga.id;
            const srcName = source?.name ?? manga.sourceName ?? '';
            const srcLang = source?.lang ?? manga.sourceLang ?? '';

            // If a local folder exists with the same title but from a different source, auto-suggest a name with suffix
            const conflict = library.value.some(m => {
                const sameName = canonicalTitle(m.name || m.title || '') === canonicalTitle(manga.title);
                const sameSource = m.source_meta?.sourceId && String(m.source_meta.sourceId) === String(sourceId);
                return sameName && !sameSource;
            });
            const folderName = (conflict && srcName) ? manga.title.trimEnd() + ' [' + srcName + ']' : manga.title;

            currentTitle.value = folderName;
            sourceFolderName.value = folderName;
            currentCover.value = manga.thumbnailUrl || null;
            currentMdManga.value = null;
            currentManga.value = null;
            currentSourceContext.value = { sourceId, mangaId, sourceName: srcName, sourceLang: srcLang };
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
                        title: folderName,
                        sourceId,
                        mangaId,
                        thumbnailUrl: manga.thumbnailUrl || null,
                        sourceName: srcName || null,
                        sourceLang: srcLang || null,
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
                    language: srcLang || activeSource.value?.lang || 'und',
                    scanlator: ch.scanlator || '',
                    pageCount: ch.pageCount || 0,
                }));
                // Sync local download/upscale status with the resolved folder name
                const statusRes = await fetch('/api/library/' + encodeURIComponent(currentTitle.value)).catch(() => null);
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

        const applySourceFolder = () => {
            const newName = sourceFolderName.value.trim();
            if (!newName || newName === currentTitle.value) return;
            currentTitle.value = newName;
            loadChapters(newName);
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
                        sourceName: currentSourceContext.value?.sourceName || null,
                        sourceLang: currentSourceContext.value?.sourceLang || null,
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
                            sourceName: currentSourceContext.value.sourceName || null,
                            sourceLang: currentSourceContext.value.sourceLang || null,
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

        // ── MangaDex Popular / Trending ───────────────────────────────────────────

        const loadMdTags = async () => {
            try {
                const r = await fetch('/api/mangadex/tags');
                if (r.ok) mdTags.value = await r.json();
            } catch (e) {}
        };

        const loadMdPopular = async (type) => {
            mdViewTab.value = type;
            if (type === 'search') { mdPopular.value = []; return; }
            mdPopularLoading.value = true;
            mdPopular.value = [];
            try {
                const p = new URLSearchParams({ type });
                mdSelectedTags.value.forEach(id => p.append('tags[]', id));
                mdContentRating.value.forEach(r => p.append('rating[]', r));
                const r = await fetch(`/api/mangadex/popular?${p}`);
                if (!r.ok) throw new Error(r.statusText);
                const d = await r.json();
                mdPopular.value = d.results || [];
            } catch (e) {
                showToast('Error cargando populares: ' + e.message, 'error');
            } finally {
                mdPopularLoading.value = false;
            }
        };

        // ── AniList ───────────────────────────────────────────────────────────────

        const fetchAnilistScores = async (mal_ids) => {
            const newIds = mal_ids.filter(id => id && !anilistScores.value[String(id)]);
            if (!newIds.length) return;
            try {
                const res = await fetch('/api/anilist/scores', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mal_ids: newIds }),
                });
                const d = await res.json();
                anilistScores.value = { ...anilistScores.value, ...d };
            } catch (_) {}
        };

        const loadAnilistGenres = async () => {
            if (anilistGenres.value.length) return;
            try {
                const res = await fetch('/api/anilist/genres');
                anilistGenres.value = await res.json();
            } catch (_) {}
        };

        const loadAnilistTop = async (page = 1) => {
            anilistTopLoading.value = true;
            if (page === 1) anilistTop.value = [];
            anilistTopPage.value = page;
            try {
                const p = new URLSearchParams({ sort: anilistTopSort.value, page });
                if (anilistGenreFilter.value) {
                    const entry = anilistGenres.value.find(g => g.name === anilistGenreFilter.value);
                    if (entry?.type === 'tag') p.set('tag', anilistGenreFilter.value);
                    else p.set('genre', anilistGenreFilter.value);
                }
                const res = await fetch(`/api/anilist/top?${p}`);
                const d = await res.json();
                if (page === 1) {
                    anilistTop.value = d.results || [];
                } else {
                    anilistTop.value = [...anilistTop.value, ...(d.results || [])];
                }
                anilistTopHasMore.value = d.hasNextPage || false;
            } catch (_) {
                showToast('Error cargando AniList', 'error');
            } finally {
                anilistTopLoading.value = false;
            }
        };

        const loadMoreAnilistTop = () => loadAnilistTop(anilistTopPage.value + 1);

        const switchToAnilistTab = () => {
            mdViewTab.value = 'anilist';
            loadAnilistGenres();
            if (!anilistTop.value.length) loadAnilistTop();
        };

        const openAnilistManga = async (alManga) => {
            const query = alManga.title || alManga.title_romaji;
            if (!query) return;
            showToast('Buscando en MangaDex...', 'info');
            try {
                const res = await fetch(`/api/mangadex/search?q=${encodeURIComponent(query)}`);
                const results = await res.json();
                if (results?.length) {
                    openMdManga(results[0]);
                } else {
                    showToast('No encontrado en MangaDex', 'info');
                }
            } catch (_) {
                showToast('Error buscando en MangaDex', 'error');
            }
        };

        const getBadgeClass = (score) => {
            if (!score) return '';
            if (score >= 75) return 'badge--green';
            if (score >= 60) return 'badge--yellow';
            return 'badge--gray';
        };

        // ── CBZ / Local Comics ────────────────────────────────────────────────────

        const loadCbzLibrary = async () => {
            cbzLoading.value = true;
            try {
                const r = await fetch('/api/cbz/list');
                cbzManga.value = r.ok ? await r.json() : [];
            } catch (e) {
                cbzManga.value = [];
            } finally {
                cbzLoading.value = false;
            }
        };

        const openCbzManga = async (manga) => {
            cbzCurrentManga.value = manga;
            cbzVolumes.value = [];
            showCbzModal.value = true;
            cbzVolumesLoading.value = true;
            try {
                const r = await fetch(`/api/cbz/volumes?manga=${encodeURIComponent(manga.title)}`);
                if (r.ok) cbzVolumes.value = await r.json();
            } catch (e) {
                showToast('Error cargando volúmenes', 'error');
            } finally {
                cbzVolumesLoading.value = false;
            }
        };

        const readCbzVolume = async (volume) => {
            const manga = cbzCurrentManga.value;
            if (!manga) return;
            try {
                const r = await fetch(`/api/cbz/pages?manga=${encodeURIComponent(manga.title)}&volume=${encodeURIComponent(volume.name)}`);
                if (!r.ok) throw new Error(r.statusText);
                const data = await r.json();
                currentTitle.value = manga.title;
                currentChapter.value = volume.name;
                pages.value = data.pages;
                currentPage.value = 0;
                readerSource.value = 'original';
                showCbzModal.value = false;
                showReader.value = true;
            } catch (e) {
                showToast('Error abriendo volumen: ' + e.message, 'error');
            }
        };

        // ── Anime / Nyaa ──────────────────────────────────────────────────────────

        const formatBytes = (b) => {
            if (!b) return '0 B';
            if (b < 1024) return b + ' B';
            if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
            if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
            return (b / 1073741824).toFixed(2) + ' GB';
        };

        const formatSpeed = (bps) => {
            if (!bps) return '0 KB/s';
            if (bps < 1048576) return Math.round(bps / 1024) + ' KB/s';
            return (bps / 1048576).toFixed(1) + ' MB/s';
        };

        const formatEta = (secs) => {
            if (!secs || secs >= 8640000) return '∞';
            if (secs < 60) return secs + 's';
            if (secs < 3600) return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
            return Math.floor(secs / 3600) + 'h ' + Math.floor((secs % 3600) / 60) + 'm';
        };

        const qbtStateLabel = (state) => {
            const map = {
                downloading: 'Descargando', uploading: 'Subiendo',
                stalledDL: 'Sin seeds', stalledUP: 'Pausado',
                pausedDL: 'Pausado', pausedUP: 'Completado/Pausado',
                checkingDL: 'Verificando', checkingUP: 'Verificando',
                queuedDL: 'En cola', queuedUP: 'En cola',
                error: 'Error', missingFiles: 'Archivos faltantes',
                forcedDL: 'Descargando', forcedUP: 'Subiendo',
            };
            return map[state] || state;
        };

        const searchAnime = async () => {
            const q = animeQuery.value.trim();
            if (q.length < 2) return;
            animeLoading.value = true;
            animeAnilistDown.value = false;
            animeResults.value = [];
            try {
                const res = await fetch(`/api/anime/search?q=${encodeURIComponent(q)}`);
                const d = await res.json();
                if (Array.isArray(d)) {
                    animeResults.value = d;
                }
            } catch (e) {
                showToast('Error buscando anime', 'error');
            } finally {
                animeLoading.value = false;
            }
        };

        // Direct Nyaa search (bypasses AniList)
        const searchNyaaDirect = async () => {
            const q = animeTorrentQuery.value.trim() || animeQuery.value.trim();
            if (!q) return;
            animeCurrentAnime.value = { title: q, title_romaji: q, cover: null, genres: [], score: null, episodes: null, status: null };
            animeView.value = 'detail';
            animeTorrentQuery.value = q;
            await searchAnimeTorrents();
        };

        const searchAnimeTorrents = async () => {
            const q = animeTorrentQuery.value.trim();
            if (!q) return;
            animeTorrentsLoading.value = true;
            animeTorrents.value = [];
            try {
                const p = new URLSearchParams({ q, category: animeTorrentCategory.value });
                const res = await fetch(`/api/anime/torrents?${p}`);
                animeTorrents.value = await res.json();
            } catch (e) {
                showToast('Error buscando en Nyaa', 'error');
            } finally {
                animeTorrentsLoading.value = false;
            }
        };

        const animeToshoLoading = ref(false);

        const searchAnimetosho = async () => {
            const q = animeTorrentQuery.value.trim();
            if (!q) return;
            animeToshoLoading.value = true;
            animeTorrents.value = [];
            try {
                const res = await fetch(`/api/anime/torrents_tosho?q=${encodeURIComponent(q)}`);
                animeTorrents.value = await res.json();
            } catch (e) {
                showToast('Error buscando en Animetosho', 'error');
            } finally {
                animeToshoLoading.value = false;
            }
        };

        // Fetch multiple title variants in parallel and merge results (dedup by info_hash)
        const _nyaaMultiFetch = async (queries) => {
            const category = animeTorrentCategory.value;
            const results = await Promise.all(
                queries.map(q =>
                    fetch(`/api/anime/torrents?${new URLSearchParams({ q, category })}`)
                        .then(r => r.json()).catch(() => [])
                )
            );
            const seen = new Set();
            const merged = [];
            for (const list of results) {
                for (const t of list) {
                    const key = t.info_hash || t.title;
                    if (!seen.has(key)) { seen.add(key); merged.push(t); }
                }
            }
            return merged;
        };

        // ── SPA navigation helpers ────────────────────────────────────────────────
        const gotoView = (view, afterFn) => {
            if (currentView.value === view) return;
            const prev = currentView.value;
            _navStack.push(() => { currentView.value = prev; });
            history.pushState({ depth: _navStack.length }, '');
            currentView.value = view;
            if (afterFn) afterFn();
        };

        const isAdding = (key) => qbtAddingHashes.value.has(key);
        const isAdded  = (key) => {
            if (qbtAddedHashes.value.has(key)) return true;
            // Check persisted library so "already added" survives page reload
            if (!key || key.startsWith('http') || !animeCurrentAnime.value) return false;
            const cur = animeCurrentAnime.value;
            const libEntry = animeLibrary.value.find(a =>
                (cur.al_id  && a.al_id  === cur.al_id)  ||
                (cur.mal_id && a.mal_id === cur.mal_id)
            );
            return !!(libEntry?.episodes || []).find(e => e.info_hash === key);
        };

        const openAnime = async (anime) => {
            const prevView  = animeView.value;
            const prevAnime = animeCurrentAnime.value;
            _navStack.push(() => {
                animeView.value = prevView;
                animeCurrentAnime.value = prevAnime;
            });
            history.pushState({ depth: _navStack.length }, '');
            animeCurrentAnime.value = anime;
            animeView.value = 'detail';
            animeTorrents.value = [];
            animeLangFilter.value = 'all';
            animeHideDead.value = true;
            animeQualityFilter.value = '';
            animeGroupFilter.value = '';
            animeEpFilter.value = 'all';
            targetEpisodeNum.value = null;
            // Collect all unique non-empty title variants (romaji, english, generic title)
            const variants = [...new Set(
                [anime.title_romaji, anime.title_english, anime.title]
                .map(t => (t || '').trim()).filter(Boolean)
            )];
            animeTorrentQuery.value = variants[0] || '';
            animeTorrentsLoading.value = true;
            try {
                animeTorrents.value = await _nyaaMultiFetch(variants);
            } catch (e) {
                showToast('Error buscando en Nyaa', 'error');
            } finally {
                animeTorrentsLoading.value = false;
            }
        };

        const addToQbt = async (torrent) => {
            if (!qbtConnected.value) {
                showToast('qBittorrent no conectado — ve a Descargas para configurarlo', 'error');
                return;
            }
            const key = torrent.info_hash || torrent.torrent_url;
            if (qbtAddingHashes.value.has(key)) return;      // prevent double-click

            const addingSet = new Set(qbtAddingHashes.value);
            addingSet.add(key);
            qbtAddingHashes.value = addingSet;

            try {
                const res = await fetch('/api/anime/qbt/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ magnet: torrent.magnet || '', torrent_url: torrent.torrent_url, anime_title: animeCurrentAnime.value?.title || '' }),
                });
                const d = await res.json();
                if (d.ok) {
                    showToast('Torrent agregado ✓', 'success');
                    const added = new Set(qbtAddedHashes.value);
                    added.add(key);
                    qbtAddedHashes.value = added;
                    setTimeout(() => {
                        const s = new Set(qbtAddedHashes.value);
                        s.delete(key);
                        qbtAddedHashes.value = s;
                    }, 2500);
                    // Register in anime library
                    if (animeCurrentAnime.value) {
                        const a = animeCurrentAnime.value;
                        fetch('/api/anime/library/add', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                al_id: a.al_id, mal_id: a.mal_id,
                                title: a.title, title_romaji: a.title_romaji || '',
                                cover: a.cover || '',
                                total_episodes: a.episodes || null,
                                format: a.format || '',
                                // Batch torrent (episode=0): register as ep 0 → all individual eps show as downloaded via libDetailHasBatch
                                episode: torrent.episode === 0 ? 0 : (targetEpisodeNum.value !== null ? targetEpisodeNum.value : torrent.episode),
                                torrent_title: torrent.title,
                                info_hash: torrent.info_hash || '',
                            }),
                        }).then(() => loadAnimeLibrary(true)).catch(() => {});
                        // Warn about multi-season batches so user can register in other season entries
                        if (torrent.episode === 0 && /Season\s+\d+\s*[\+\-&\/]\s*\d+|S\d{2}\s*[\+\-]\s*S?\d{2}|\bSeason\s+\d+.*Season\s+\d+/i.test(torrent.title)) {
                            setTimeout(() => showToast('Batch multi-temporada: ábrelo también en la entrada de la Temporada 2 para registrarlo allí también', 'info', 6000), 800);
                        }
                    }
                } else {
                    showToast('qBittorrent: ' + (d.msg || d.error || 'Error'), 'error');
                }
            } catch (e) {
                showToast('Error conectando qBittorrent', 'error');
            } finally {
                const s = new Set(qbtAddingHashes.value);
                s.delete(key);
                qbtAddingHashes.value = s;
            }
        };

        const checkQbt = async () => {
            try {
                const res = await fetch('/api/anime/qbt/status');
                const d = await res.json();
                qbtConnected.value = d.connected;
                qbtVersion.value = d.version || '';
                if (d.url) qbtUrl.value = d.url;
            } catch (_) {}
        };

        const configureQbt = async () => {
            try {
                const res = await fetch('/api/anime/qbt/configure', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: qbtUrl.value, username: qbtUsername.value, password: qbtPassword.value }),
                });
                const d = await res.json();
                qbtConnected.value = d.connected;
                if (d.connected) {
                    qbtConfigShow.value = false;
                    showToast('qBittorrent conectado ✓', 'success');
                    loadQbtTorrents();
                } else {
                    showToast('No se pudo conectar a qBittorrent', 'error');
                }
            } catch (e) {
                showToast('Error configurando qBittorrent', 'error');
            }
        };

        const loadQbtTorrents = async () => {
            qbtLoading.value = true;
            try {
                const res = await fetch('/api/anime/qbt/list');
                qbtTorrents.value = await res.json();
            } catch (_) {
                qbtTorrents.value = [];
            } finally {
                qbtLoading.value = false;
            }
        };

        const qbtAction = async (action, hash, deleteFiles = false) => {
            try {
                const res = await fetch('/api/anime/qbt/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, hash, delete_files: deleteFiles }),
                });
                const json = await res.json().catch(() => ({}));
                if (!res.ok || json.error) throw new Error(json.error || res.statusText);
                if (action === 'recheck') {
                    showToast('Recalculando archivos… espera unos segundos', 'info');
                    await new Promise(r => setTimeout(r, 3000));
                }
                await loadQbtTorrents();
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            }
        };

        const switchToAnimeView = async (view) => {
            const prev = animeView.value;
            if (view !== prev) {
                _navStack.push(() => { animeView.value = prev; });
                history.pushState({ depth: _navStack.length }, '');
            }
            animeView.value = view;
            if (view === 'downloads') {
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
                await checkQbt();
                await loadQbtTorrents();
                if (qbtPollTimer) clearInterval(qbtPollTimer);
                qbtPollTimer = setInterval(loadQbtTorrents, 5000);
            } else if (view === 'library') {
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
                // Load silently if we already have data (instant view, refresh in background)
                await loadAnimeLibrary(animeLibrary.value.length > 0);
                if (libPollTimer) clearInterval(libPollTimer);
                libPollTimer = setInterval(() => loadAnimeLibrary(true), 3500);
            } else if (view === 'seasonal') {
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
                await loadSeasonalAnime();
            } else {
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
            }
        };

        const loadAnimeLibrary = async (silent = false) => {
            // Show spinner only on the very first load (empty list); never flash on refreshes
            const firstLoad = animeLibrary.value.length === 0;
            if (!silent && firstLoad) animeLibraryLoading.value = true;
            try {
                const res = await fetch('/api/anime/library');
                const newData = await res.json();
                // Smart merge: keep existing object references for unchanged items so Vue
                // skips re-rendering cards (and avoids image reload flicker)
                const byId = new Map(animeLibrary.value.map(a => [a.id, a]));
                animeLibrary.value = newData.map(n => {
                    const old = byId.get(n.id);
                    if (!old) return n;
                    const epSig = eps => JSON.stringify(
                        (eps || []).map(e => `${e.num}:${e.watched}:${e.progress}:${e.state}`)
                    );
                    if (old.downloaded_count === n.downloaded_count &&
                        old.status === n.status &&
                        epSig(old.episodes) === epSig(n.episodes))
                        return old; // same reference → Vue skips this card entirely
                    return n;
                });
                if (animeLibraryDetail.value) {
                    const updated = animeLibrary.value.find(a => a.id === animeLibraryDetail.value.id);
                    if (updated && updated !== animeLibraryDetail.value) animeLibraryDetail.value = updated;
                }
            } catch (_) {
                if (!silent && firstLoad) animeLibrary.value = [];
            } finally {
                if (!silent) animeLibraryLoading.value = false;
            }
        };

        const loadWatchHistory = async () => {
            try {
                const r = await fetch('/api/anime/history');
                watchHistory.value = await r.json();
                watchHistoryLoaded.value = true;
            } catch (_) {}
        };

        const clearWatchHistory = async () => {
            await fetch('/api/anime/history/clear', { method: 'POST' });
            watchHistory.value = [];
            showToast('Historial borrado', 'info');
        };

        const checkMangaUpdates = async () => {
            try {
                const r = await fetch('/api/mangadex/updates');
                if (r.ok) mangaNewChapters.value = await r.json();
            } catch (_) {}
        };

        // Subtitle state per episode key "animeId_epNum"
        const epSubtitles = ref({});  // key → [{name, path}]

        const loadEpSubtitles = async (anime, ep) => {
            const key = `${anime.id}_${ep.num}`;
            if (epSubtitles.value[key] !== undefined) return;
            epSubtitles.value[key] = [];  // mark as loading
            try {
                const r = await fetch(`/api/anime/subtitles/${anime.id}/${ep.num}`);
                epSubtitles.value[key] = await r.json();
            } catch (_) { epSubtitles.value[key] = []; }
        };

        const playEpisode = async (anime, ep, subFile = '') => {
            if ((!ep.in_qbt || ep.progress < 100) && !(ep.num > 0 && libDetailBatchDone.value) && !ep.in_local) return;
            try {
                const body = ep.in_local
                    ? { anime_id: anime.id, episode: ep.num, local_path: ep.local_path, sub_file: subFile }
                    : { anime_id: anime.id, episode: ep.num, info_hash: ep.info_hash, sub_file: subFile };
                const r = await fetch('/api/anime/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                let d;
                try { d = await r.json(); } catch (_) {
                    showToast(`Error al reproducir: HTTP ${r.status} — reinicia el servidor`, 'error');
                    return;
                }
                if (d.error) {
                    showToast('Error al reproducir: ' + d.error, 'error');
                } else {
                    showToast('Reproduciendo en MPV…', 'success');
                    await loadAnimeLibrary(true);
                }
            } catch (e) {
                showToast('Error al reproducir: ' + e.message, 'error');
            }
        };

        // ── Episode auto-renamer ──────────────────────────────────────────────
        const openRenamePreview = async (anime) => {
            renameItems.value = [];
            renameAnime.value = anime;
            const r = await fetch(`/api/anime/rename_preview/${anime.id}`);
            if (r.ok) {
                const d = await r.json();
                if (d.error) { showToast('Sin episodios locales para renombrar', 'info'); renameAnime.value = null; return; }
                renameItems.value = d.renames || [];
            }
        };

        const applyRenames = async () => {
            if (!renameAnime.value || renameBusy.value) return;
            renameBusy.value = true;
            const r = await fetch(`/api/anime/rename_apply/${renameAnime.value.id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ renames: renameItems.value }),
            });
            const d = await r.json();
            renameBusy.value = false;
            renameAnime.value = null;
            showToast(`${d.renamed} episodio(s) renombrado(s)`, d.errors?.length ? 'info' : 'success');
            await loadAnimeLibrary(true);
        };

        // ── Scanlation comparison ─────────────────────────────────────────────
        const openComparePanel = async (group, title) => {
            if (scanCompareChapter.value?.chapter === group.chapter) {
                scanCompareChapter.value = null;
                return;
            }
            scanCompareChapter.value = group;
            scanCompareVariants.value = [];
            scanCompareLoading.value = true;
            try {
                const r = await fetch(`/api/library/${encodeURIComponent(title)}/compare_variants/${group.chapter}`);
                if (r.ok) scanCompareVariants.value = await r.json();
            } finally { scanCompareLoading.value = false; }
        };

        const downloadCompareVariant = async (variant, title, chapter) => {
            const r = await fetch('/api/download/download_compare', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chapterId: variant.id,
                    title,
                    chapter,
                    group: variant.groups?.[0] || 'unknown',
                    lang: variant.language,
                }),
            });
            if (r.ok) {
                showToast('Descargando variante para comparar…', 'info');
                // Reload variants after a delay
                setTimeout(() => openComparePanel({ chapter }, title), 8000);
            }
        };

        const readCompareSources = async (title, chapter, compareDir, group, lang) => {
            try {
                const [r1, r2] = await Promise.all([
                    fetch('/api/reader/read_chapter', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title, chapter, source: 'original' }),
                    }),
                    fetch('/api/reader/read_compare', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title, chapter, compare_dir: compareDir }),
                    }),
                ]);
                const d1 = await r1.json();
                const d2 = await r2.json();
                pages.value = d1.pages || [];
                comparePages2.value = d2.pages || [];
                currentPage.value = 0;
                compareMode.value = true;
                scanCompareMode.value = true;
                readerSource.value = 'original';
                currentTitle.value = title;
                currentChapter.value = chapter;
                showReader.value = true;
            } catch (e) {
                showToast('Error al cargar comparación: ' + e.message, 'error');
            }
        };

        // ── Scan paths ────────────────────────────────────────────────────────
        const openScanPaths = async () => {
            scanPathsShow.value = true;
            scanBrowsing.value = false;
            const r = await fetch('/api/anime/scanpaths');
            if (r.ok) {
                scanPaths.value = await r.json();
                if (scanPaths.value.length > 0) loadScanFolders();
            }
        };

        const openBrowse = () => {
            scanBrowsing.value = true;
            navigateBrowse('');
        };

        const navigateBrowse = async (path) => {
            scanBrowseLoading.value = true;
            try {
                const url = path ? `/api/anime/browse?path=${encodeURIComponent(path)}` : '/api/anime/browse';
                const r = await fetch(url);
                if (!r.ok) return;
                const d = await r.json();
                scanBrowsePath.value   = d.path || '';
                scanBrowseWinPath.value = d.win_path || '';
                scanBrowseParent.value = d.parent ?? null;
                scanBrowseItems.value  = d.items || [];
            } finally {
                scanBrowseLoading.value = false;
            }
        };

        const selectBrowsePath = async () => {
            const p = scanBrowseWinPath.value || scanBrowsePath.value;
            if (!p) return;
            await fetch('/api/anime/scanpaths', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: p }),
            });
            const r = await fetch('/api/anime/scanpaths');
            if (r.ok) scanPaths.value = await r.json();
            scanBrowsing.value = false;
            showToast(`Ruta agregada: ${p}`, 'success');
            loadScanFolders();
        };

        const addScanPath = async () => {
            const p = scanNewPath.value.trim();
            if (!p) return;
            await fetch('/api/anime/scanpaths', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: p }),
            });
            scanNewPath.value = '';
            const r = await fetch('/api/anime/scanpaths');
            if (r.ok) scanPaths.value = await r.json();
        };

        const removeScanPath = async (path) => {
            await fetch('/api/anime/scanpaths', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path }),
            });
            const r = await fetch('/api/anime/scanpaths');
            if (r.ok) scanPaths.value = await r.json();
            // Remove from current scan results too
            scanFolders.value = scanFolders.value.filter(f => !f.folder.startsWith(path));
        };

        const loadScanFolders = async () => {
            scanFoldersLoading.value = true;
            try {
                const r = await fetch('/api/anime/scan/folders');
                if (r.ok) {
                    const data = await r.json();
                    scanFolders.value = data.map(f => ({
                        ...f,
                        _searchQuery: '',
                        _searchResults: [],
                        _searching: false,
                        _suggLoading: false,
                    }));
                    // Fetch AniList suggestions lazily — one at a time to avoid rate-limiting
                    for (const folder of scanFolders.value) {
                        if (folder.mapped_id || folder.suggestion) continue;
                        folder._suggLoading = true;
                        try {
                            const sr = await fetch(`/api/anime/scan/suggest?name=${encodeURIComponent(folder.name)}`);
                            if (sr.ok) folder.suggestion = await sr.json();
                        } catch (_) {}
                        folder._suggLoading = false;
                    }
                }
            } finally {
                scanFoldersLoading.value = false;
            }
        };

        const matchFolder = async (folder, match) => {
            await fetch('/api/anime/scan/match', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder: folder.folder,
                    anilist_id: match.id,
                    title: match.title,
                    cover: match.cover,
                }),
            });
            folder.mapped_id     = String(match.id);
            folder.matched_title = match.title;
            folder.matched_cover = match.cover;
            folder._searchResults = [];
            await loadAnimeLibrary(true);
        };

        const unmatchFolder = async (folder) => {
            await fetch('/api/anime/scan/unmatch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder: folder.folder }),
            });
            folder.mapped_id     = null;
            folder.matched_title = '';
            folder.matched_cover = '';
            await loadAnimeLibrary(true);
        };

        const searchForScanFolder = async (folder) => {
            const q = folder._searchQuery.trim();
            if (!q) return;
            folder._searching = true;
            folder._searchResults = [];
            try {
                const r = await fetch(`/api/anime/search?q=${encodeURIComponent(q)}`);
                if (r.ok) {
                    const items = await r.json();
                    folder._searchResults = items.slice(0, 6).map(a => ({
                        id: a.al_id || a.id,
                        title: a.title,
                        cover: a.cover,
                    }));
                }
            } finally {
                folder._searching = false;
            }
        };
        // ─────────────────────────────────────────────────────────────────────

        const toggleWatched = async (anime, ep) => {
            const newState = !ep.watched;
            // Optimistic update — flip immediately in both detail and library list
            ep.watched = newState;
            const libAnime = animeLibrary.value.find(a => a.id === anime.id);
            const libEp = libAnime?.episodes?.find(e => e.num === ep.num);
            if (libEp) libEp.watched = newState;
            try {
                const r = await fetch(`/api/anime/library/${anime.id}/watched`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ episode: ep.num }),
                });
                const d = await r.json();
                if (!d.ok) {
                    ep.watched = !newState;
                    if (libEp) libEp.watched = !newState;
                }
            } catch (e) {
                ep.watched = !newState;
                if (libEp) libEp.watched = !newState;
                showToast('Error al actualizar estado visto', 'error');
            }
        };

        // ── Subtitle translation ──────────────────────────────────────────────────
        // Key format: "{animeId}_{epNum}" — persisted to localStorage across reloads
        const subTaskKey = (animeId, epNum) => `${animeId}_${epNum}`;
        const _loadSubTasks = () => { try { return JSON.parse(localStorage.getItem('subTasks') || '{}'); } catch { return {}; } };
        const subTasks = ref(_loadSubTasks());
        watch(subTasks, (val) => { try { localStorage.setItem('subTasks', JSON.stringify(val)); } catch {} }, { deep: true });

        const subTrackModal  = ref(null); // {anime, ep, tracks, externalTracks} when picker is open
        const subFetchingKey = ref(null); // key of episode currently fetching subtitle tracks

        const _subPoll = (key, taskId) => {
            const iv = setInterval(async () => {
                try {
                    const r = await fetch(`/api/subtitle/status/${taskId}`);
                    if (!r.ok) {
                        // Task not found — server restarted or task expired; stop polling
                        clearInterval(iv);
                        subTasks.value = { ...subTasks.value, [key]: { status: 'cancelled', progress: 0, message: 'Tarea perdida (servidor reiniciado)', task_id: taskId } };
                        return;
                    }
                    const d = await r.json();
                    // Preserve task_id so cancel button always has it
                    subTasks.value = { ...subTasks.value, [key]: { ...d, task_id: taskId } };
                    if (d.status === 'done' || d.status === 'error' || d.status === 'cancelled') {
                        clearInterval(iv);
                        if (d.status === 'done')
                            showToast(`✓ Subtítulos ESP añadidos: ${d.output?.split('/').pop() || ''}`, 'success');
                        else if (d.status === 'error')
                            showToast(`Error traduciendo: ${d.message}`, 'error');
                    }
                } catch { clearInterval(iv); }
            }, 1500);
        };

        const directInjectSub = async (anime, ep, subInfo) => {
            subTrackModal.value = null;
            const key = subTaskKey(anime.id, ep.num);
            subTasks.value = { ...subTasks.value, [key]: { status: 'injecting', progress: 50, message: 'Descargando subtítulo…' } };
            try {
                const r = await fetch('/api/subtitle/inject_direct', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        info_hash:   ep.info_hash || '',
                        episode:     ep.num,
                        anime_id:    anime.id,
                        ...(ep.local_path ? { local_path: ep.local_path } : {}),
                        external_sub: subInfo,
                    }),
                });
                const d = await r.json();
                if (!r.ok || d.error) {
                    subTasks.value = { ...subTasks.value, [key]: { status: 'error', progress: 0, message: d.error || 'Error' } };
                    showToast(d.error || 'Error al inyectar subtítulo', 'error');
                    return;
                }
                subTasks.value = { ...subTasks.value, [key]: { status: 'done', progress: 100, message: '¡Completado!' } };
                showToast(`✓ Subtítulos ESP añadidos: ${d.file || ''}`, 'success');
            } catch (e) {
                subTasks.value = { ...subTasks.value, [key]: { status: 'error', progress: 0, message: String(e) } };
                showToast('Error de conexión', 'error');
            }
        };

        const startTranslate = async (anime, ep, subIndex = 0, externalSub = null) => {
            subTrackModal.value = null;
            const key = subTaskKey(anime.id, ep.num);
            subTasks.value = { ...subTasks.value, [key]: { status: 'starting', progress: 0, message: 'Iniciando…' } };
            try {
                const r = await fetch('/api/subtitle/translate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        info_hash:  ep.info_hash || '',
                        episode:    ep.num,
                        anime_id:   anime.id,
                        sub_index:  subIndex,
                        ...(ep.local_path ? { local_path: ep.local_path } : {}),
                        ...(externalSub    ? { external_sub: externalSub } : {}),
                    }),
                });
                const d = await r.json();
                if (!r.ok) {
                    subTasks.value = { ...subTasks.value, [key]: { status: 'error', progress: 0, message: d.error || 'Error' } };
                    showToast(d.error || 'Error al traducir', 'error');
                    return;
                }
                // Store task_id immediately so cancel button works from the first poll
                subTasks.value = { ...subTasks.value, [key]: { ...subTasks.value[key], task_id: d.task_id } };
                _subPoll(key, d.task_id);
            } catch (e) {
                subTasks.value = { ...subTasks.value, [key]: { status: 'error', progress: 0, message: String(e) } };
                showToast('Error de conexión', 'error');
            }
        };

        const translateSubs = async (anime, ep) => {
            const key = subTaskKey(anime.id, ep.num);
            subFetchingKey.value = key;
            const params = new URLSearchParams({
                info_hash: ep.info_hash || '', episode: ep.num, anime_id: anime.id,
                ...(ep.local_path ? { local_path: ep.local_path } : {}),
            });
            try {
                const r = await fetch(`/api/subtitle/tracks?${params}`);
                const d = await r.json();
                if (!r.ok) { showToast(d.error || 'No se encontró el archivo', 'error'); return; }
                const tracks        = d.tracks          || [];
                const extTracks     = d.external_tracks || [];
                const spanishTracks = d.spanish_tracks  || [];
                const missingKeys   = d.sources_missing_key || [];
                const engTracks     = tracks.filter(t => t.language === 'eng' || t.language === 'und');

                // If Spanish subs found, always show modal (Spanish section first)
                if (spanishTracks.length > 0 || tracks.length > 0 || extTracks.length > 0) {
                    // Auto-select only when exactly one Spanish sub and nothing else to choose from
                    if (spanishTracks.length === 1 && tracks.length === 0 && extTracks.length === 0) {
                        await directInjectSub(anime, ep, spanishTracks[0]);
                        return;
                    }
                    subTrackModal.value = { anime, ep, tracks, externalTracks: extTracks, spanishTracks, missingKeys };
                    return;
                }

                // No subtitles found at all
                const hint = missingKeys.length
                    ? ` (sin API key: ${missingKeys.join(', ')} — configura JIMAKU_API_KEY / OPENSUBTITLES_API_KEY)`
                    : '';
                showToast(`No se encontraron subtítulos${hint}`, 'error', 8000);
            } catch (e) {
                showToast('Error al obtener pistas de subtítulos', 'error');
            } finally {
                subFetchingKey.value = null;
            }
        };

        const cancelTranslation = async (animeId, epNum) => {
            const key = subTaskKey(animeId, epNum);
            const taskId = subTasks.value[key]?.task_id;
            // If no task_id yet (cancelled during 'starting' phase), mark cancelled locally
            if (!taskId) {
                subTasks.value = { ...subTasks.value, [key]: { ...subTasks.value[key], status: 'cancelled', message: 'Cancelado' } };
                showToast('Traducción cancelada', 'info');
                return;
            }
            try {
                await fetch(`/api/subtitle/cancel/${taskId}`, { method: 'POST' });
                subTasks.value = { ...subTasks.value, [key]: { ...subTasks.value[key], status: 'cancelled', message: 'Cancelado' } };
                showToast('Traducción cancelada', 'info');
            } catch (e) {
                showToast('Error al cancelar', 'error');
            }
        };

        const removeFromAnimeLibrary = async (animeId, deleteFiles = false) => {
            try {
                await fetch(`/api/anime/library/${animeId}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ delete_files: deleteFiles }),
                });
                if (animeLibraryDetail.value?.id === animeId) animeLibraryDetail.value = null;
                await loadAnimeLibrary(true);
                showToast('Eliminado de la biblioteca', 'success');
            } catch (e) {
                showToast('Error al eliminar', 'error');
            }
        };

        const deleteEpisode = async (anime, ep) => {
            if (!confirm(`¿Borrar episodio ${ep.num} de "${anime.title}"?\nEsto lo eliminará de la biblioteca y de qBittorrent.`)) return;
            try {
                const r = await fetch(`/api/anime/library/${anime.id}/episode/${ep.num}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ delete_files: true }),
                });
                const d = await r.json();
                if (d.ok) {
                    await loadAnimeLibrary(true);
                    showToast(`Episodio ${ep.num} eliminado`, 'success');
                } else {
                    showToast(d.error || 'Error al eliminar', 'error');
                }
            } catch (e) {
                showToast('Error al eliminar: ' + e.message, 'error');
            }
        };

        // ── Episode type override (local episodes) ────────────────────────────
        const epOverrideMenu = ref(null); // {anime, ep, x, y}

        const openEpOverrideMenu = (event, anime, ep) => {
            event.stopPropagation();
            event.preventDefault();
            epOverrideMenu.value = { anime, ep };
        };

        const setEpOverride = async (type) => {
            const { anime, ep } = epOverrideMenu.value || {};
            epOverrideMenu.value = null;
            if (!anime || !ep) return;
            try {
                const r = await fetch(`/api/anime/library/${anime.id}/ep_override`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: ep.filename, type }),
                });
                const d = await r.json();
                if (d.ok) {
                    await loadAnimeLibrary(true);
                    const labels = { special: 'especial', hidden: 'oculto', episode: 'episodio normal' };
                    showToast(`Marcado como ${labels[type] || type}`, 'success');
                } else {
                    showToast(d.error || 'Error', 'error');
                }
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            }
        };

        // ── Recommendations ──────────────────────────────────────────────────
        const animeRecs      = ref({});   // al_id → array of rec objects
        const animeRecsState = ref({});   // al_id → 'loading' | 'done' | 'error' | 'none'

        const loadAnimeRecs = async (anime) => {
            const id = anime?.al_id;
            if (!id || animeRecsState.value[id]) return;
            animeRecsState.value[id] = 'loading';
            try {
                const r = await fetch(`/api/anime/recommendations/${id}`);
                const data = await r.json();
                if (data.error) throw new Error(data.error);
                animeRecs.value[id] = data;
                animeRecsState.value[id] = data.length ? 'done' : 'none';
            } catch (_) {
                animeRecsState.value[id] = 'error';
            }
        };

        // ── Tags ──────────────────────────────────────────────────────────────
        const animeTags      = ref({});  // al_id → [{name, rank, category}]
        const animeTagsState = ref({});
        const animeMalUrls   = ref({});  // al_id → MAL url string (with slug)

        // Extract MAL numeric ID from a MAL URL like https://myanimelist.net/anime/20/Naruto
        const _malIdFromUrl = (url) => {
            const m = (url || '').match(/\/anime\/(\d+)\//);
            return m ? parseInt(m[1]) : null;
        };

        const loadAnimeTags = async (anime) => {
            const id = anime?.al_id;
            if (!id || animeTagsState.value[id]) return;
            animeTagsState.value[id] = 'loading';
            try {
                const r = await fetch(`/api/anime/tags/${id}`);
                const data = await r.json();
                if (data.error) throw new Error(data.error);
                animeTags.value[id]    = data.tags || [];
                animeMalUrls.value[id] = data.mal_url || '';
                animeTagsState.value[id] = (data.tags || []).length ? 'done' : 'none';
                // Now we know the mal_id — trigger stacks if not already loading
                const mid = anime.mal_id || _malIdFromUrl(data.mal_url);
                if (mid) loadAnimeStacks({ ...anime, mal_id: mid });
            } catch (_) {
                animeTagsState.value[id] = 'error';
            }
        };

        const tagBrowse      = ref(null);   // tag name being browsed
        const tagBrowseAnime = ref([]);
        const tagBrowseState = ref('idle'); // 'idle'|'loading'|'done'|'error'

        const browseByTag = async (tagName, sourceAlId = null) => {
            tagBrowse.value = tagName;
            tagBrowseState.value = 'loading';
            tagBrowseAnime.value = [];
            try {
                let url = `/api/anime/browse_tag?tag=${encodeURIComponent(tagName)}`;
                if (sourceAlId) url += `&al_id=${sourceAlId}`;
                const r = await fetch(url);
                const data = await r.json();
                if (data.error) throw new Error(data.error);
                tagBrowseAnime.value = data;
                tagBrowseState.value = 'done';
            } catch (_) {
                tagBrowseState.value = 'error';
            }
        };

        // ── MAL Interest Stacks ─────────────────────────────────────────────
        // Keyed by al_id so the HTML condition stays simple and works even when
        // mal_id isn't in the library entry but is derived later from animeMalUrls.
        const animeStacks      = ref({});  // al_id → [{id, name, url}]
        const animeStacksState = ref({});  // al_id → 'loading'|'done'|'error'|'none'

        const loadAnimeStacks = async (anime) => {
            const alId = anime?.al_id;
            if (!alId || animeStacksState.value[alId]) return;
            // Resolve mal_id: from library data first, then animeMalUrls
            const mid = anime.mal_id || _malIdFromUrl(animeMalUrls.value[alId] || '');
            if (!mid) return;   // no mal_id available yet (tags not loaded)
            animeStacksState.value[alId] = 'loading';
            try {
                const r = await fetch(`/api/anime/stacks?mal_id=${mid}`);
                const data = await r.json();
                if (data.error) throw new Error(data.error);
                animeStacks.value[alId]      = data;
                animeStacksState.value[alId] = data.length ? 'done' : 'none';
            } catch (_) {
                animeStacksState.value[alId] = 'error';
            }
        };

        // Stack browse panel (in-app)
        const stackBrowse      = ref(null);   // {id, name, url} of the stack being browsed
        const stackBrowseMeta  = ref(null);   // {name, description, entries, restacks, url} from API
        const stackBrowseAnime = ref([]);
        const stackBrowseState = ref('idle'); // 'idle'|'loading'|'done'|'error'

        const browseStack = async (stack) => {
            stackBrowse.value      = stack;
            stackBrowseMeta.value  = null;
            stackBrowseState.value = 'loading';
            stackBrowseAnime.value = [];
            try {
                const r    = await fetch(`/api/anime/stacks/browse/${stack.id}`);
                const data = await r.json();
                if (data.error) throw new Error(data.error);
                stackBrowseMeta.value  = data.stack  || null;
                stackBrowseAnime.value = data.items  || [];
                stackBrowseState.value = 'done';
            } catch (_) {
                stackBrowseState.value = 'error';
            }
        };

        const openAnimeLibraryDetail = (anime) => {
            const prev = animeLibraryDetail.value;
            _navStack.push(() => { animeLibraryDetail.value = prev; });
            history.pushState({ depth: _navStack.length }, '');
            animeLibraryDetail.value = anime;
            loadAnimeRecs(anime);
            loadAnimeTags(anime);
            loadAnimeStacks(anime);
        };

        const isInAnimeLibrary = (anime) => {
            if (!anime) return false;
            return animeLibrary.value.some(a =>
                (anime.al_id  && a.al_id  === anime.al_id)  ||
                (anime.mal_id && a.mal_id === anime.mal_id) ||
                (anime.title  && a.title  === anime.title)
            );
        };

        const addAnimeToLibrary = async (anime) => {
            try {
                const res = await fetch('/api/anime/library/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        al_id: anime.al_id, mal_id: anime.mal_id,
                        title: anime.title, title_romaji: anime.title_romaji || '',
                        cover: anime.cover || '',
                        total_episodes: anime.episodes || null,
                        format: anime.format || '',
                        track_only: true,
                    }),
                });
                const d = await res.json();
                if (d.ok) {
                    showToast(`"${anime.title}" añadido a Mi Anime`, 'success');
                    await loadAnimeLibrary(true);
                }
            } catch (e) {
                showToast('Error al añadir a biblioteca', 'error');
            }
        };

        const setAnimeStatus = async (animeId, status) => {
            // Optimistic update — mutate in-place so the card re-renders immediately
            const entry = animeLibrary.value.find(a => a.id === animeId);
            const prev = entry?.status;
            if (entry) entry.status = status;
            if (animeLibraryDetail.value?.id === animeId) animeLibraryDetail.value.status = status;
            try {
                const res = await fetch(`/api/anime/library/${animeId}/status`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status }),
                });
                if (!(await res.json()).ok) throw new Error();
                // Silent background refresh to sync any other fields
                loadAnimeLibrary(true);
            } catch (e) {
                // Roll back on error
                if (entry) entry.status = prev;
                if (animeLibraryDetail.value?.id === animeId) animeLibraryDetail.value.status = prev;
                showToast('Error al cambiar estado', 'error');
            }
        };

        const clearAnimeEpisodes = async (anime, removeFromQbt = false, deleteFiles = false) => {
            if (!confirm(`¿Borrar todos los episodios de "${anime.title}"? La serie permanecerá en la biblioteca.`)) return;
            try {
                await fetch(`/api/anime/library/${anime.id}/clear_episodes`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ remove_from_qbt: removeFromQbt, delete_files: deleteFiles }),
                });
                showToast('Episodios eliminados', 'success');
                await loadAnimeLibrary(true);
            } catch (e) { showToast('Error al borrar episodios', 'error'); }
        };

        const openLinkTorrent = async () => {
            linkTorrentShow.value = true;
            linkTorrentLoading.value = true;
            linkTorrentList.value = [];
            linkTorrentSubpath.value = '';
            try {
                const res = await fetch('/api/anime/qbt/list');
                linkTorrentList.value = (await res.json()) || [];
            } catch (e) { linkTorrentList.value = []; }
            linkTorrentLoading.value = false;
        };

        const linkExistingTorrent = async (anime, torrent) => {
            if (!torrent?.hash) return;
            try {
                const res = await fetch(`/api/anime/library/${anime.id}/link_torrent`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        info_hash: torrent.hash, episode: 0, torrent_title: torrent.name,
                        subpath: linkTorrentSubpath.value.trim(),
                    }),
                });
                const d = await res.json();
                if (d.ok) {
                    showToast(`Torrent enlazado como batch en "${anime.title}"`, 'success');
                    linkTorrentShow.value = false;
                    linkTorrentSubpath.value = '';
                    await loadAnimeLibrary(true);
                }
            } catch (e) { showToast('Error al enlazar', 'error'); }
        };

        const libDetailBatchEp = computed(() =>
            animeLibraryDetail.value?.episodes?.find(e => e.num === 0 && e.in_qbt) || null
        );
        const libDetailHasBatch = computed(() => !!libDetailBatchEp.value);
        const libDetailBatchDone = computed(() => (libDetailBatchEp.value?.progress ?? 0) >= 100);

        const fmtPos = (secs) => {
            if (!secs) return '';
            const h = Math.floor(secs / 3600);
            const m = Math.floor((secs % 3600) / 60);
            const s = Math.floor(secs % 60);
            if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
            return `${m}:${String(s).padStart(2,'0')}`;
        };

        const nextUnwatchedEp = (anime) => {
            if (!anime?.episodes) return null;
            return anime.episodes.find(e =>
                e.ep_type !== 'special' && e.num > 0 && !e.watched &&
                (e.in_local || (e.in_qbt && e.progress >= 100))
            ) || null;
        };

        const continueWatching = computed(() => {
            return animeLibrary.value
                .filter(a => a.last_watched_at > 0)
                .map(a => {
                    const inProgress = (a.episodes || []).find(e =>
                        e.ep_type !== 'special' && e.num > 0 && !e.watched &&
                        e.resume_pos > 0 &&
                        (e.in_local || (e.in_qbt && e.progress >= 100))
                    );
                    const ep = inProgress || nextUnwatchedEp(a);
                    if (!ep) return null;
                    return { anime: a, ep };
                })
                .filter(Boolean)
                .sort((a, b) => (b.anime.last_watched_at || 0) - (a.anime.last_watched_at || 0))
                .slice(0, 12);
        });

        const _watchedFraction = (anime) => {
            const eps = (anime.episodes || []).filter(e => e.ep_type !== 'special' && e.num > 0 &&
                (e.in_local || (e.in_qbt && e.progress >= 100)));
            if (!eps.length) return 0;
            return eps.filter(e => e.watched).length / eps.length;
        };
        const _lastDownloaded = (anime) => {
            const eps = (anime.episodes || []).filter(e => e.num > 0 && e.added_on > 0 &&
                (e.in_local || (e.in_qbt && e.progress >= 100)));
            return eps.length ? Math.max(...eps.map(e => e.added_on)) : 0;
        };

        const sortedAnimeLibrary = computed(() => {
            let list = [...animeLibrary.value];
            const q = animeLibSearch.value.trim().toLowerCase();
            if (q) list = list.filter(a =>
                (a.title || '').toLowerCase().includes(q) ||
                (a.title_romaji || '').toLowerCase().includes(q)
            );
            // Filter by explicit status field
            const f = animeLibFilter.value;
            if (f !== 'all') list = list.filter(a => (a.status || '') === f);

            if (animeLibSort.value === 'last_added') {
                list.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
            } else if (animeLibSort.value === 'last_watched') {
                list.sort((a, b) => (b.last_watched_at || 0) - (a.last_watched_at || 0));
            } else if (animeLibSort.value === 'last_downloaded') {
                list.sort((a, b) => _lastDownloaded(b) - _lastDownloaded(a));
            } else if (animeLibSort.value === 'progress') {
                list.sort((a, b) => _watchedFraction(b) - _watchedFraction(a));
            } else if (animeLibSort.value === 'episodes') {
                list.sort((a, b) => (b.downloaded_count || 0) - (a.downloaded_count || 0));
            } else {
                list.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
            }
            return list;
        });

        // Groups for the "Por estado" layout (only non-empty groups shown)
        const STATUS_ORDER = ['watching', 'plan_to_watch', 'on_hold', 'dropped', 'completed', ''];
        const animeLibGroups = computed(() => {
            const base = sortedAnimeLibrary.value;
            if (animeLibSort.value !== 'status') return null;
            const groups = {};
            for (const a of base) {
                const s = a.status || '';
                if (!groups[s]) groups[s] = [];
                groups[s].push(a);
            }
            return STATUS_ORDER.filter(s => groups[s]?.length).map(s => ({
                key: s,
                label: s ? ANIME_STATUS[s]?.label : 'Sin estado',
                color: s ? ANIME_STATUS[s]?.color : '#94a3b8',
                items: groups[s],
            }));
        });

        const animeLibCounts = computed(() => {
            const counts = { all: animeLibrary.value.length };
            for (const a of animeLibrary.value) {
                const s = a.status || '';
                counts[s] = (counts[s] || 0) + 1;
            }
            return counts;
        });

        const seasonLabel = computed(() => {
            const s = animeSeasonSeason.value;
            const y = animeSeasonYear.value;
            return (s && y) ? `${SEASON_ES[s] || s} ${y}` : 'Temporada actual';
        });

        const seasonYears = computed(() => {
            const cur = new Date().getFullYear();
            const arr = [];
            for (let y = cur + 1; y >= 1990; y--) arr.push(y);
            return arr;
        });

        const filteredSeasonalAnime = computed(() => {
            let list = animeSeasonalResults.value;
            if (animeSeasonGenreFilter.value)
                list = list.filter(a => a.genres.includes(animeSeasonGenreFilter.value));
            return list;
        });

        const seasonalGenres = computed(() => {
            const s = new Set();
            animeSeasonalResults.value.forEach(a => a.genres.forEach(g => s.add(g)));
            return [...s].sort();
        });

        const loadSeasonalAnime = async () => {
            animeSeasonalLoading.value = true;
            try {
                const p = new URLSearchParams({ sort: animeSeasonSort.value });
                if (animeSeasonSeason.value) p.set('season', animeSeasonSeason.value);
                if (animeSeasonYear.value)   p.set('year',   animeSeasonYear.value);
                const r = await fetch(`/api/anime/seasonal?${p}`);
                const d = await r.json();
                animeSeasonalResults.value = d.results || [];
                // Only set on initial load — don't overwrite a user-chosen season/year
                if (!animeSeasonSeason.value) animeSeasonSeason.value = d.season || '';
                if (!animeSeasonYear.value)   animeSeasonYear.value   = d.year   || 0;
            } catch { animeSeasonalResults.value = []; }
            finally  { animeSeasonalLoading.value = false; }
        };

        const seasonNav = (dir) => {
            const idx = SEASONS.indexOf(animeSeasonSeason.value);
            if (idx === -1) { loadSeasonalAnime(); return; }
            let ni = idx + dir;
            if (ni < 0)              { ni = 3; animeSeasonYear.value--; }
            else if (ni > 3)         { ni = 0; animeSeasonYear.value++; }
            animeSeasonSeason.value = SEASONS[ni];
            loadSeasonalAnime();
        };

        const onSeasonChange = (e) => { animeSeasonSeason.value = e.target.value; loadSeasonalAnime(); };
        const onYearChange   = (e) => { animeSeasonYear.value = Number(e.target.value); loadSeasonalAnime(); };

        const searchEpisodeInNyaa = async (anime, epNum) => {
            // Map library anime to the shape openAnime expects
            animeCurrentAnime.value = { ...anime, episodes: anime.total_episodes };
            const prev = animeView.value;
            _navStack.push(() => { animeView.value = prev; animeLibraryDetail.value = anime; });
            history.pushState({ depth: _navStack.length }, '');
            animeView.value = 'detail';
            animeLibraryDetail.value = null;
            animeTorrents.value = [];
            animeLangFilter.value = 'all';
            animeHideDead.value = true;
            animeQualityFilter.value = '';
            animeGroupFilter.value = '';
            animeEpFilter.value = 'all';
            // MOVIE/MUSIC don't have episode numbers — don't pre-filter
            const fmt = anime.format || '';
            targetEpisodeNum.value = (fmt === 'MOVIE' || fmt === 'MUSIC') ? null : (epNum > 0 ? epNum : null);

            // Search all title variants so batches and alternate names are included
            const variants = [...new Set(
                [anime.title_romaji, anime.title]
                .map(t => (t || '').trim()).filter(Boolean)
            )];
            animeTorrentQuery.value = variants[0] || '';
            animeTorrentsLoading.value = true;
            try {
                animeTorrents.value = await _nyaaMultiFetch(variants);
            } catch (e) {
                showToast('Error buscando en Nyaa', 'error');
            } finally {
                animeTorrentsLoading.value = false;
            }
        };

        // ── Suwayomi Popular ──────────────────────────────────────────────────────

        const loadSourcesPopular = async (source) => {
            sourcePopularLoading.value = true;
            sourcePopular.value = [];
            searchMode.value = 'popular';
            activeSource.value = source;
            try {
                const r = await fetch(`/api/sources/popular?source=${source.id}&type=POPULAR`);
                const d = await r.json();
                if (d.error) throw new Error(d.error);
                sourcePopular.value = d.results || [];
            } catch (e) {
                showToast('Error cargando populares: ' + e.message, 'error');
            } finally {
                sourcePopularLoading.value = false;
            }
        };

        // Init
        // Lazy-load flags: only fetch once per session
        let _mdTagsLoaded = false;
        let _mdLibraryLoaded = false;
        let _cbzLoaded = false;

        // Auto-fetch AniList scores when search/popular results arrive
        watch(currentView, (val) => {
            if (val === 'anime') {
                loadAnimeLibrary();
                if (libPollTimer) clearInterval(libPollTimer);
                libPollTimer = setInterval(() => loadAnimeLibrary(true), 3500);
            } else if (val === 'mangadex' || val === 'followed') {
                if (val === 'mangadex' && !_mdTagsLoaded) { _mdTagsLoaded = true; loadMdTags(); }
                if (!_mdLibraryLoaded) { _mdLibraryLoaded = true; loadMdLibrary(); }
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
            } else if (val === 'cbz') {
                if (!_cbzLoaded) { _cbzLoaded = true; loadCbzLibrary(); }
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
            } else {
                if (libPollTimer)  { clearInterval(libPollTimer);  libPollTimer  = null; }
                if (qbtPollTimer)  { clearInterval(qbtPollTimer);  qbtPollTimer  = null; }
            }
        });
        watch(searchResults, (val) => {
            const ids = val.filter(m => m.mal_id).map(m => m.mal_id);
            if (ids.length) fetchAnilistScores(ids);
        });
        watch(mdPopular, (val) => {
            const ids = val.filter(m => m.mal_id).map(m => m.mal_id);
            if (ids.length) fetchAnilistScores(ids);
        });
        watch(mdLibrary, (val) => {
            const ids = val.filter(m => m.mal_id).map(m => m.mal_id);
            if (ids.length) fetchAnilistScores(ids);
        });

        // Persist session on every view/filter change
        watch([currentView, animeView, libFilter, libSearch, animeLibFilter, animeLibSort], _saveSession, { flush: 'post' });

        onMounted(() => {
            loadLibrary();
            loadLocalLibrary();
            initSSE();
            syncExportTasks();
            checkSuwayomi();
            checkDrive();
            checkQbt();
            loadLibraryInfo();

            // Restore view-specific data based on persisted session
            // (the currentView watch doesn't fire on initial value, so we do it manually)
            const _initView = currentView.value;
            if (_initView === 'anime') {
                loadAnimeLibrary(false);
                libPollTimer = setInterval(() => loadAnimeLibrary(true), 3500);
                if (animeView.value === 'history') loadWatchHistory();
            } else if (_initView === 'mangadex' || _initView === 'followed') {
                if (!_mdTagsLoaded) { _mdTagsLoaded = true; loadMdTags(); }
                if (!_mdLibraryLoaded) { _mdLibraryLoaded = true; loadMdLibrary(); }
            } else if (_initView === 'local') {
                if (!_cbzLoaded) { _cbzLoaded = true; loadCbzLibrary(); }
            }
            // 'library' and 'sources' handled by loadLibrary() + checkSuwayomi() above

            // Check for new manga chapters (silently, shows badge if any)
            setTimeout(checkMangaUpdates, 3000);
            // Re-check every 15 minutes
            setInterval(checkMangaUpdates, 15 * 60 * 1000);
            document.addEventListener('keydown', handleKeydown);
            // On reload: reconnect polls for subtitle tasks that were in progress and have a task_id
            const _terminal = new Set(['done', 'error', 'cancelled']);
            Object.entries(subTasks.value).forEach(([key, t]) => {
                if (!_terminal.has(t.status) && t.task_id) {
                    _subPoll(key, t.task_id);
                }
            });
            // SPA history: seed an initial state so back button stays within the app
            history.replaceState({ depth: 0 }, '');
            window.addEventListener('popstate', () => {
                if (_navStack.length > 0) _navStack.pop()();
            });
        });
        return {
            currentView, library, localLibrary, mdLibrary, searchQuery, searchResults,
            chapters, mdChapters, mdChaptersLoading, chapterStatus, showModal, showReader,
            currentManga, currentMdManga, currentTitle, currentCover, currentCoverUrl,
            currentChapter, currentPage, pages, isZoomed, selectedLang, toast,
            stats, groupedChapters, filteredChapters, availableLangs, currentPageUrl, combinedLibrary, filteredLibrary, libSearch, libFilter,
            mangaUpdates, updatesLoading, updatesById, loadMangaUpdates, refreshMangaUpdates,
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
            isInLibrary, taskQueueExpanded, taskQueueList, taskQueueSummary, deleteChapter, downloadedLang, normalizeChapter, formatChapter, toggleChapterRead,
            currentModalTab, openTomoTab,
            exportVolumeName, exportFormat, exportQuality, exportDownscaleHalf, exportSelectedChapters, exportChapterSet, exportBusy, exportPreview,
            bulkFrom, bulkTo, bulkPreview, bulkDownload, bulkUpscale,
            upscaleModel, upscaleModelLabel, setUpscaleModel,
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
            sourceFolderName, applySourceFolder,
            globalQuery, globalResults, globalSearchLoading, globalLang, globalSearchDone, globalSearchTotal, sourceLangs, searchMode,
            checkSuwayomi, loadSources, searchSources, searchAllSources, openSourceManga,
            sourcePopular, sourcePopularLoading, loadSourcesPopular,
            offlineCoverStatus, downloadCoversOffline,
            editMetaShow, editMetaTitle, editMetaCoverUrl, editMetaBusy, openEditMeta, saveEditMeta,
            corruptScanResult, corruptScanBusy, scanCorruptPages,
            anilistScores, anilistTop, anilistTopLoading, anilistTopHasMore, anilistTopPage,
            anilistGenres, anilistGenreFilter, anilistTopSort, anilistChipSearch, filteredAnilistChips,
            fetchAnilistScores, loadAnilistGenres, loadAnilistTop, loadMoreAnilistTop,
            switchToAnilistTab, openAnilistManga, getBadgeClass,
            mdViewTab, mdPopular, mdPopularLoading, loadMdPopular, loadMdTags,
            mdTags, mdSelectedTags, mdContentRating, mdShowTagFilter, toggleMdTag, toggleMdRating,
            currentMdDetail,
            cbzManga, cbzLoading, cbzCurrentManga, cbzVolumes, cbzVolumesLoading, showCbzModal,
            loadCbzLibrary, openCbzManga, readCbzVolume,
            activeTasksByTitle, canonicalTitle, seriesReadCounts,
            driveConfigured, driveConnected, driveEmail, driveUploading, driveUploadResult,
            connectDrive, disconnectDrive, uploadToDrive,
            compareMode, compareX, canCompare, readerSource,
            currentPageOrigUrl, currentPageUpUrl,
            toggleCompare, onCompareDragStart, onCompareDrag, onCompareDragEnd,
            libraryUrlLocal, libraryUrlPhone, libraryWslIp, libraryFileCount, libraryFwCmd, libraryProxyCmd,
            loadLibraryInfo, saveToLibrary, dismissExportTask, downloadExportFile,
            animeLibSearch,
            watchHistory, watchHistoryLoaded, loadWatchHistory, clearWatchHistory,
            mangaNewChapters, mangaNewCount, checkMangaUpdates,
            epSubtitles, loadEpSubtitles,
            renameAnime, renameItems, renameBusy, renameChanges, openRenamePreview, applyRenames,
            scanCompareChapter, scanCompareVariants, scanCompareLoading, scanCompareMode,
            comparePages2, currentPageCompare2Url, openComparePanel, downloadCompareVariant, readCompareSources,
            animeView, animeQuery, animeResults, animeLoading, animeAnilistDown, animeCurrentAnime,
            searchNyaaDirect,
            animeTorrents, animeTorrentsLoading, animeTorrentQuery, animeTorrentCategory,
            animeLangFilter, animeHideDead, animeLangCounts,
            animeQualityFilter, animeGroupFilter, animeEpFilter, targetEpisodeNum,
            filteredAnimeTorrents, animeGroups, animeQualities,
            qbtConnected, qbtVersion, qbtUrl, qbtUsername, qbtPassword, qbtConfigShow, qbtTorrents, qbtLoading,
            qbtAddingHashes, qbtAddedHashes, isAdding, isAdded,
            searchAnime, searchAnimeTorrents, searchAnimetosho, animeToshoLoading, openAnime, addToQbt, checkQbt, configureQbt,
            loadQbtTorrents, qbtAction, switchToAnimeView,
            formatBytes, formatSpeed, formatEta, qbtStateLabel, animeFormatLabel, animeEpLabel,
            groupedAnimeEpisodes, animeExpandedEps, toggleEpGroup, isEpExpanded, isSpanishOrMulti, isEnglishSub,
            animeLibrary, animeLibraryLoading, animeLibraryDetail,
            animeLibSort, animeLibFilter, sortedAnimeLibrary, animeLibGroups, animeLibCounts,
            nextUnwatchedEp, continueWatching, fmtPos,
            ANIME_STATUS, STATUS_ORDER,
            loadAnimeLibrary, removeFromAnimeLibrary, openAnimeLibraryDetail, searchEpisodeInNyaa,
            isInAnimeLibrary, addAnimeToLibrary, libDetailHasBatch, libDetailBatchDone, libDetailBatchEp,
            setAnimeStatus, clearAnimeEpisodes,
            linkTorrentShow, linkTorrentList, linkTorrentLoading, linkTorrentSubpath, openLinkTorrent, linkExistingTorrent,
            animeRecs, animeRecsState,
            animeTags, animeTagsState, animeMalUrls, browseByTag,
            animeStacks, animeStacksState,
            stackBrowse, stackBrowseMeta, stackBrowseAnime, stackBrowseState, browseStack,
            tagBrowse, tagBrowseAnime, tagBrowseState,
            playEpisode, toggleWatched, deleteEpisode,
            scanPathsShow, scanPaths, scanNewPath, scanFolders, scanFoldersLoading,
            scanBrowsing, scanBrowsePath, scanBrowseWinPath, scanBrowseParent, scanBrowseItems, scanBrowseLoading,
            openScanPaths, addScanPath, removeScanPath, loadScanFolders,
            openBrowse, navigateBrowse, selectBrowsePath,
            matchFolder, unmatchFolder, searchForScanFolder,
            subTasks, subTrackModal, subFetchingKey, translateSubs, startTranslate, directInjectSub, subTaskKey, cancelTranslation,
            epOverrideMenu, openEpOverrideMenu, setEpOverride,
            animeSeasonalResults, animeSeasonalLoading, animeSeasonSort, animeSeasonGenreFilter,
            animeSeasonSeason, animeSeasonYear, seasonLabel, seasonYears, filteredSeasonalAnime, seasonalGenres,
            loadSeasonalAnime, seasonNav, onSeasonChange, onYearChange, SEASON_ES,
            gotoView,
            sidebarCollapsed, sidebarMobileOpen, saveSidebarState,
            showShortcuts, mainScrolled, scrollToTop, dismissToast,
        };
    }
});

app.mount('#app');