<script setup>
import { computed, ref, watch } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { formatChapter, MANGA_STATUS } from '@/lib/manga'
import { imgProxy } from '@/lib/img'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangaStore()
const ui = useUiStore()
// The X / overlay just closes the modal and stays on the current view — it must NOT
// navigate browser history (that was jumping to the previous page, sometimes Mi Anime).
// replaceNav updates the current history entry to "closed"; the browser back/forward
// buttons still reopen/close via the snapshot model.
function closeModal() { store.close(); ui.replaceNav() }
const m = computed(() => store.current)
const upState = (ch) => store.upscaled[ch]          // true | 'partial' | undefined

const LANG_FLAG = { en: '🇬🇧', es: '🇪🇸', 'es-la': '🇲🇽', ja: '🇯🇵', 'pt-br': '🇧🇷', fr: '🇫🇷', ko: '🇰🇷', zh: '🇨🇳', 'zh-hk': '🇭🇰', it: '🇮🇹', de: '🇩🇪', ru: '🇷🇺' }
const flag = (l) => LANG_FLAG[l] || l

// bulk upscale range
const rangeFrom = ref('')
const rangeTo = ref('')
function doRange() {
  if (!rangeFrom.value || !rangeTo.value) return
  const excludePages = store.current?.source_meta?.imported ? store.excludedPages : []
  store.upscaleRange(rangeFrom.value, rangeTo.value, false, excludePages)
}
// color pages
function detectColors() { store.loadColorPages([...sel.value]) }

// modal tabs + tomo export form
const tab = ref('chapters')
const sel = ref(new Set())
const volName = ref('')
const fmt = ref('cbz')
const quality = ref(92)
const codec = ref('jpeg')
const downscale = ref(false)
// WebP holds up at lower quality than JPEG (no ringing on text), so its slider floor is lower.
const qMin = computed(() => codec.value === 'webp' ? 70 : 85)
// Keep quality within the codec's valid range when switching codecs.
watch(codec, () => { if (quality.value < qMin.value) quality.value = qMin.value })

// management panel
const showManage = ref(false)
const renameVal = ref('')
const coverUrlVal = ref('')
const tomoCover = ref('')      // b64 cover for the exported tomo

// Traducir (trasplante)
const tp = computed(() => store.tp)
const tpSel = ref(new Set())
const toggleTp = (ch) => { const s = new Set(tpSel.value); s.has(ch) ? s.delete(ch) : s.add(ch); tpSel.value = s }
const onArt = (e) => store.tpSelectArt(tp.value.artCands.find(c => store._candKey(c) === e.target.value))
const onEs = (e) => store.tpSelectEs(tp.value.esCands.find(c => store._candKey(c) === e.target.value))
const tpPhaseLabel = computed(() => {
  const p = tp.value.discoverProgress || {}
  switch (tp.value.phase) {
    case 'start': case 'variants': return 'Buscando variantes del título…'
    case 'searching': return p.searchTotal ? `Rastreando fuentes… ${p.searched || 0}/${p.searchTotal}` : 'Rastreando fuentes…'
    case 'ranking': return p.rankTotal ? `Midiendo calidad… ${p.ranked || 0}/${p.rankTotal}` : 'Midiendo calidad…'
    default: return 'Trabajando…'
  }
})
const tpRunPct = computed(() => {
  const s = tp.value.runStatus
  if (!s?.chapterTotal) return 0
  return Math.round(((s.chapterDone || 0) / s.chapterTotal) * 100)
})

// Versiones (ranking de calidad)
const ver = computed(() => store.ver)
const verLangs = computed(() => Object.keys(store.ver.byLang || {}))
const verList = computed(() => {
  const f = store.ver.langFilter
  return f ? (store.ver.byLang[f] || []) : store.ver.versions
})
const verBest = computed(() => verList.value[0] || null)   // mejor de la selección actual (para "recomendada ↑")
const verSampleKey = (c) => `${c.sourceId}_${c.mangaId}`
// Calidad "irregular": el peor capítulo muestreado es notablemente peor que la mediana
// (consistency = peor/mediana). El ranking ya lo penaliza; aquí solo lo señalamos.
const isIrregular = (q) => !!(q && q.consistency != null && q.consistency < 0.85
  && q.heightMax && q.heightMin && q.heightMax - q.heightMin > 150)
function isCurrentVersion(c) {
  const sm = store.current?.source_meta
  return !!(sm && c.sourceId === sm.sourceId && String(c.mangaId) === String(sm.mangaId))
}
// La versión local como "candidato" sintético para poder compararla en A|B
const localCand = computed(() => store.ver.local
  ? { local: true, sourceId: '__local__', mangaId: '__local__', sourceName: 'Tu versión local', sourceLang: store.current?.source_meta?.sourceLang || '', quality: store.ver.local }
  : null)
const recommended = computed(() => store.current?.source_meta?.recommended_source || null)
function isPrimary(c) {
  const r = recommended.value
  return !!(r && r.sourceId === c.sourceId && String(r.mangaId) === String(c.mangaId))
}
function isInCompare(c) {
  return store.ver.cmpSel.some(s => s.sourceId === c.sourceId && String(s.mangaId) === String(c.mangaId))
}
const verPhaseLabel = computed(() => {
  const p = ver.value.discoverProgress || {}
  switch (ver.value.phase) {
    case 'start': case 'variants': return 'Buscando variantes del título…'
    case 'searching': return p.searchTotal ? `Rastreando fuentes… ${p.searched || 0}/${p.searchTotal}` : 'Rastreando fuentes…'
    case 'ranking': return p.rankTotal ? `Midiendo calidad… ${p.ranked || 0}/${p.rankTotal}` : 'Midiendo calidad…'
    default: return 'Trabajando…'
  }
})

// Reparto manual de un tomo (cuando el reparto automático por nº de páginas ES falla)
const manualVol = ref(null)
const manualCounts = ref([])
function openManualSplit(vol) {
  manualVol.value = vol
  manualCounts.value = (vol.esChapters?.length ? vol.esChapters : [{ chapter: vol.chapterStartHint || '1' }])
    .map(c => ({ chapter: c.chapter, pages: c.pageCount || 0 }))
}
function closeManualSplit() { manualVol.value = null; manualCounts.value = [] }
const manualTotal = computed(() => manualCounts.value.reduce((sum, c) => sum + (Number(c.pages) || 0), 0))
function addManualRow() {
  const last = manualCounts.value[manualCounts.value.length - 1]
  const next = last ? (Number(last.chapter) || 0) + 1 : 1
  manualCounts.value.push({ chapter: String(next), pages: 0 })
}
function removeManualRow(i) { manualCounts.value.splice(i, 1) }
function submitManualSplit() {
  if (!manualVol.value) return
  const pageCounts = manualCounts.value.map(c => ({ chapter: c.chapter, pages: Number(c.pages) || 0 }))
  store.tpResolveVolumeManual(manualVol.value.prefix, pageCounts)
  closeManualSplit()
}

watch(m, (v) => {
  tab.value = 'chapters'; sel.value = new Set(); volName.value = v?.name || ''
  showManage.value = false; renameVal.value = v?.name || ''; coverUrlVal.value = ''
  tomoCover.value = ''; rangeFrom.value = ''; rangeTo.value = ''; tpSel.value = new Set()
  store.verReset()
  if (v) { store.resetMdex(); store.loadHealth(); store.colorPages = []; store.excludedPages = []; store.exportPreview = { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 } }
  if (v && !Object.keys(store.models).length) store.loadModels()
  if (v) store.loadDestinations()
}, { immediate: true })

// Live tomo estimate: recompute whenever the selection, quality or exclusions change
// while the export tab is open.
watch([sel, quality, codec, tab, () => store.excludedPages.length], () => {
  if (tab.value === 'tomo') store.loadExportPreview([...sel.value], quality.value, codec.value)
})

function applyVol(vol) {
  const { selected, label } = store.applyMdexVolume(vol)
  if (selected.length) { sel.value = new Set(selected); volName.value = label }
}

function onTomoCover(e) {
  const f = e.target.files?.[0]; if (!f) return
  const r = new FileReader(); r.onload = () => { tomoCover.value = r.result }; r.readAsDataURL(f)
}

async function saveMeta() {
  const ok = await store.editMeta({ newTitle: renameVal.value.trim(), coverUrl: coverUrlVal.value.trim() })
  if (ok) showManage.value = false
}
function onCoverFile(e) {
  const f = e.target.files?.[0]; if (!f) return
  store.applyCoverFile(f)   // aplica + refresca la portada mostrada (cache-bust)
  e.target.value = ''
}

const toggleSel = (ch) => { const s = new Set(sel.value); s.has(ch) ? s.delete(ch) : s.add(ch); sel.value = s }
const allSelected = computed(() => store.chapters.length > 0 && sel.value.size === store.chapters.length)
const selectAll = () => { sel.value = allSelected.value ? new Set() : new Set(store.chapters.map(c => c.chapter)) }

async function doExport(toDrive = false) {
  if (!sel.value.size) return
  const chapters = [...sel.value].sort((a, b) => parseFloat(a) - parseFloat(b))
  await store.exportTomo({ chapters, volumeName: volName.value, format: fmt.value, quality: quality.value, codec: codec.value, downscaleHalf: downscale.value, coverB64: tomoCover.value || store.mdex.coverB64, toDrive })
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="m" class="ov" @click.self="closeModal">
        <div class="modal">
          <button class="modal__x" @click="closeModal"><Icon name="close" :size="18" /></button>

          <header class="modal__head">
            <img v-if="m.cover" :src="imgProxy(m.cover)" class="modal__cover" :alt="m.name" />
            <div v-else class="modal__cover modal__cover--ph"><Icon name="library" :size="30" /></div>
            <div class="modal__info">
              <h2 class="modal__title">{{ m.name }}</h2>
              <p class="modal__sub">
                {{ chapters?.length || store.chapters.length }} capítulos
                <template v-if="m.source_meta?.sourceName"> · {{ m.source_meta.sourceName }}</template>
              </p>
              <a v-if="store.mdId" :href="'https://mangadex.org/title/' + store.mdId" target="_blank" rel="noopener" class="mdlink" title="Ver en MangaDex">
                <Icon name="globe" :size="13" /> MangaDex
              </a>
              <select class="modal__status" :value="m.status || ''"
                      :style="{ color: MANGA_STATUS[m.status]?.color || 'var(--ink-faint)' }"
                      @change="store.setStatus(m, $event.target.value)">
                <option value="">Sin estado</option>
                <option v-for="(v, k) in MANGA_STATUS" :key="k" :value="k">{{ v.label }}</option>
              </select>
              <div class="modal__legend">
                <span><span class="lg lg--4k" /> 4K</span>
                <span><span class="lg lg--part" /> parcial</span>
                <span><span class="lg lg--orig" /> original</span>
              </div>
              <div class="modal__hacts">
                <button class="hbtn hbtn--accent" @click="store.upscaleAll(m.source_meta?.imported ? { excludePages: store.excludedPages } : {})"><Icon name="spark" :size="13" /> Escalar todo 4K</button>
                <span class="rangebox">
                  <input v-model="rangeFrom" placeholder="de" inputmode="decimal" />
                  <input v-model="rangeTo" placeholder="a" inputmode="decimal" />
                  <button @click="doRange" title="Escalar rango">4K rango</button>
                </span>
                <button class="hbtn" @click="showManage = !showManage" :class="{ 'is-on': showManage }">Gestionar</button>
                <button class="hbtn" @click="store.scanCorrupt()">Verificar</button>
              </div>
            </div>
          </header>

          <!-- Management panel -->
          <Transition name="info">
            <div v-if="showManage" class="manage">
              <section class="manage__sect">
                <span class="manage__label">Información del manga</span>
                <div class="manage__row">
                  <label class="mf"><span>Renombrar</span><input v-model="renameVal" type="text" /></label>
                  <label class="mf"><span>Portada (URL)</span><input v-model="coverUrlVal" type="text" placeholder="https://…" /></label>
                </div>

                <!-- Selector visual de portada (AniList + MangaDex) -->
                <button class="cvp__toggle" @click="store.coverPicker.open ? store.closeCoverPicker() : store.openCoverPicker()">
                  <Icon name="library" :size="13" /> {{ store.coverPicker.open ? 'Ocultar portadas' : 'Elegir portada (AniList / MangaDex)' }}
                </button>
                <div v-if="store.coverPicker.open" class="cvp">
                  <div v-if="store.coverPicker.loading" class="cvp__load"><span class="xspin" /> Buscando portadas online…</div>
                  <template v-else>
                    <div class="cvp__grid">
                      <div v-if="store.coverPicker.current" class="cvp__item is-current" title="Portada actual">
                        <img :src="store.coverPicker.current" referrerpolicy="no-referrer" alt="" />
                        <span class="cvp__tag">Actual</span>
                      </div>
                      <button v-for="c in store.coverPicker.anilist" :key="'al' + c.url" class="cvp__item"
                              :disabled="!!store.coverPicker.applying" @click="store.applyCover(c.url)" title="Usar esta portada">
                        <img :src="c.thumb" referrerpolicy="no-referrer" loading="lazy" alt="" />
                        <span class="cvp__tag cvp__tag--al">AniList</span>
                        <span v-if="store.coverPicker.applying === c.url" class="cvp__busy"><span class="xspin" /></span>
                      </button>
                      <button v-for="(c, i) in store.coverPicker.mangadex" :key="'md' + i" class="cvp__item"
                              :disabled="!!store.coverPicker.applying" @click="store.applyCover(c.url)" title="Usar esta portada">
                        <img :src="c.thumb" referrerpolicy="no-referrer" loading="lazy" alt="" />
                        <span v-if="c.volume && c.volume !== '?'" class="cvp__tag">Vol {{ c.volume }}</span>
                        <span v-if="store.coverPicker.applying === c.url" class="cvp__busy"><span class="xspin" /></span>
                      </button>
                    </div>
                    <p v-if="!store.coverPicker.anilist.length && !store.coverPicker.mangadex.length" class="cvp__empty">
                      No se encontraron portadas online. Pega una URL arriba o usa "Subir portada".
                    </p>
                  </template>
                </div>

                <div class="manage__actions">
                  <button class="delbtn" @click="store.deleteManga()" title="Eliminar este manga de la biblioteca"><Icon name="close" :size="13" /> Eliminar manga</button>
                  <span class="manage__spacer" />
                  <label class="upbtn"><Icon name="library" :size="13" /> Subir portada<input type="file" accept="image/*" @change="onCoverFile" hidden /></label>
                  <button class="savebtn" @click="saveMeta">Guardar cambios</button>
                </div>
              </section>
              <section class="manage__sect manage__sect--up">
                <span class="manage__label">Escalado 4K <em>· se aplica al instante</em></span>
                <div class="manage__row">
                  <label class="mf"><span>Modelo</span>
                    <select :value="store.activeModel" @change="store.setModel($event.target.value)">
                      <option v-for="(label, key) in store.models" :key="key" :value="key">{{ label }}</option>
                    </select>
                  </label>
                  <label class="mf mf--chk"><span>Modo eco <em>(deja correr MPV al escalar)</em></span>
                    <input type="checkbox" :checked="store.eco" @change="store.setEco($event.target.checked)" />
                  </label>
                </div>
              </section>
            </div>
          </Transition>

          <div class="modal__tabs">
            <button class="mtab" :class="{ 'is-active': tab === 'chapters' }" @click="tab = 'chapters'">Capítulos</button>
            <button class="mtab" :class="{ 'is-active': tab === 'versions' }" @click="tab = 'versions'"><Icon name="spark" :size="13" /> Versiones</button>
            <button class="mtab" :class="{ 'is-active': tab === 'translate' }" @click="tab = 'translate'"><Icon name="globe" :size="13" /> Traducir
              <span v-if="store.current?.transplant_meta?.translated?.length" class="mtab__badge">ES</span>
            </button>
            <button class="mtab" :class="{ 'is-active': tab === 'tomo' }" @click="tab = 'tomo'"><Icon name="library" :size="13" /> Exportar Tomo</button>
          </div>

          <div class="modal__body">
            <div v-if="store.modalLoading" class="center"><Spinner /></div>
            <div v-else-if="!store.chapters.length && !store.hasSourceMeta" class="empty">Sin capítulos descargados.</div>

            <!-- TOMO EXPORT -->
            <div v-else-if="tab === 'tomo'" class="tomo">
              <div class="tomo__form">
                <label class="fld"><span>Nombre del tomo</span><input v-model="volName" type="text" placeholder="Volumen 1" /></label>
                <div class="fld-row">
                  <label class="fld"><span>Formato</span>
                    <select v-model="fmt"><option value="cbz">CBZ</option><option value="cbr">CBR</option></select>
                  </label>
                  <label class="fld"><span>Compresión</span>
                    <select v-model="codec"><option value="jpeg">JPEG</option><option value="webp">WebP (menor tamaño)</option></select>
                  </label>
                  <label class="fld"><span>Calidad: {{ quality }}</span><input v-model.number="quality" type="range" :min="qMin" max="100" /></label>
                </div>
                <p v-if="codec === 'webp'" class="codec-hint">WebP pesa menos a calidad equivalente y conserva mejor el texto al comprimir. Requiere que tu lector lo soporte (la mayoría de los modernos sí).</p>
                <label class="chk"><input v-model="downscale" type="checkbox" /> Reducir a la mitad (menor tamaño)</label>
                <label class="upbtn"><Icon name="library" :size="13" /> {{ tomoCover ? 'Portada elegida ✓' : 'Portada del tomo (opcional)' }}<input type="file" accept="image/*" @change="onTomoCover" hidden /></label>
                <div v-if="sel.size && store.exportPreview.pages" class="tprev">
                  <span class="tprev__main">{{ store.exportPreview.pages }} págs · ~{{ store.exportPreview.est_mb }} MB</span>
                  <span v-if="store.exportPreview.upscaled_pages" class="tprev__tag tprev__tag--4k">{{ store.exportPreview.upscaled_pages }} en 4K</span>
                  <span v-if="store.exportPreview.original_pages" class="tprev__tag">{{ store.exportPreview.original_pages }} original</span>
                </div>
                <button class="exportbtn" :disabled="!sel.size" @click="doExport(false)">
                  <Icon name="download" :size="15" /> Exportar {{ sel.size }} {{ sel.size === 1 ? 'capítulo' : 'capítulos' }}
                </button>
                <button v-if="store.drive.connected" class="exportbtn exportbtn--drive" :disabled="!sel.size" @click="doExport(true)">
                  <Icon name="globe" :size="15" /> Exportar a Google Drive
                </button>
                <div class="dests">
                  <button v-if="!store.drive.connected" class="dlink" @click="store.connectDrive()">Conectar Google Drive</button>
                  <template v-else><span class="dok">Drive: {{ store.drive.email }}</span><button class="dlink" @click="store.disconnectDrive()">Desconectar</button></template>
                  <a v-if="store.webdav.localUrl" :href="store.webdav.localUrl" target="_blank" class="dlink">Biblioteca móvil (PC) ↗</a>
                </div>

                <!-- color pages exclusion -->
                <div class="colors">
                  <button class="btn-xs" :disabled="store.colorLoading || !sel.size" @click="detectColors">
                    <span v-if="store.colorLoading" class="xspin" />Detectar páginas a color
                  </button>
                  <div v-if="store.colorPages.length" class="colors__grid">
                    <button v-for="cp in store.colorPages" :key="cp.filename" class="colorpg" :class="{ 'is-excl': store.excludedPages.includes(cp.filename) }"
                            :title="cp.label + (store.excludedPages.includes(cp.filename) ? ' (excluida)' : '')" @click="store.toggleExclude(cp.filename)">
                      <img :src="cp.url" loading="lazy" alt="" />
                      <span v-if="store.excludedPages.includes(cp.filename)" class="colorpg__x"><Icon name="close" :size="12" /></span>
                    </button>
                  </div>
                  <p v-else-if="!store.colorLoading && store.colorPages.length === 0 && sel.size" class="colors__hint">Pulsa para detectar y excluir páginas a color del tomo.</p>
                </div>
              </div>
              <!-- MangaDex volumes + covers -->
              <div class="mdex">
                <div class="mdex__head">
                  <span>Tomos y portadas de MangaDex</span>
                  <button class="btn-xs" :disabled="store.mdex.volumesLoading" @click="store.loadMdexVolumes(); store.loadMdexCovers()">
                    <span v-if="store.mdex.volumesLoading" class="xspin" />{{ store.mdex.volumes.length ? 'Recargar' : 'Cargar' }}
                  </button>
                </div>
                <!-- Coincidencia activa + corrección manual (la auto-resolución puede acertar
                     aproximado; el buscador permite fijar la obra correcta y sus portadas). -->
                <p v-if="store.mdex.mdManga" class="mdex__match" :class="{ 'is-approx': store.mdex.approx }">
                  {{ store.mdex.approx ? '≈' : '✓' }} {{ store.mdex.mdManga.title }}
                  <a :href="'https://mangadex.org/title/' + store.mdex.id" target="_blank" rel="noopener">↗</a>
                </p>
                <div class="mdex__search">
                  <input v-model="store.mdex.search" placeholder="¿Manga incorrecto? Búscalo en MangaDex…"
                         @keyup.enter="store.searchMdexForTomo()" />
                  <button class="btn-xs" :disabled="store.mdex.searching" @click="store.searchMdexForTomo()">
                    <span v-if="store.mdex.searching" class="xspin" />Buscar
                  </button>
                </div>
                <div v-if="store.mdex.results.length" class="mdex__results">
                  <button v-for="r in store.mdex.results" :key="r.id" class="mdres" @click="store.selectMdexEntry(r)">
                    <img v-if="r.cover" :src="r.cover" loading="lazy" alt="" />
                    <span class="mdres__t">{{ r.title }}<small v-if="r.year"> · {{ r.year }}</small></span>
                  </button>
                </div>
                <div v-if="store.mdex.volumes.length" class="mdex__vols">
                  <button v-for="v in store.mdex.volumes" :key="v.volume" class="volchip" @click="applyVol(v)">{{ v.label || ('Tomo ' + v.volume) }}</button>
                </div>
                <div v-if="store.mdex.covers.length" class="mdex__covers">
                  <button v-for="c in store.mdex.covers" :key="c.id || c.url" class="covsel" :class="{ 'is-sel': store.mdex.selectedCover?.url === c.url }" @click="store.selectMdexCover(c)">
                    <img :src="c.url256 || c.url" loading="lazy" alt="" />
                    <span v-if="c.volume && c.volume !== 'none'" class="covsel__v">{{ c.volume }}</span>
                    <span v-if="store.mdex.coverLoadingId === c.id" class="covsel__load"><span class="xspin" /></span>
                  </button>
                </div>
              </div>

              <div class="tomo__pick">
                <button class="selall" @click="selectAll">{{ allSelected ? 'Ninguno' : 'Todos' }}</button>
                <ul class="picklist">
                  <li v-for="c in store.sortedChapters" :key="c.chapter" class="pick" :class="{ 'is-sel': sel.has(c.chapter) }" @click="toggleSel(c.chapter)">
                    <span class="pick__box"><Icon v-if="sel.has(c.chapter)" name="check" :size="12" /></span>
                    <span class="pick__num">Cap. {{ c.chapter }}</span>
                    <span v-if="upState(c.chapter) === true" class="pick__4k">4K</span>
                  </li>
                </ul>
              </div>
            </div>

            <!-- VERSIONES (ranking de calidad de imagen) -->
            <div v-if="tab === 'versions' && !store.modalLoading" class="vr">
              <p class="vr__lead">Busca todas las versiones de este manga en las fuentes y compáralas por calidad de imagen (resolución nativa × nitidez). Tu versión local aparece como referencia.</p>

              <!-- descubrimiento en curso -->
              <div v-if="ver.loading" class="vr__disc">
                <Spinner :size="18" />
                <span class="muted">{{ verPhaseLabel }}</span>
              </div>

              <!-- aún sin buscar -->
              <div v-else-if="ver.phase !== 'ready'" class="vr__cta">
                <button class="hbtn hbtn--accent" @click="store.verDiscover()"><Icon name="spark" :size="14" /> Buscar versiones</button>
                <span v-if="ver.phase === 'error'" class="tl__err">No se encontraron fuentes. ¿Suwayomi en línea?</span>
                <span v-else class="vr__hint">Requiere Suwayomi en línea.</span>
              </div>

              <!-- resultados -->
              <template v-else>
                <div class="vr__head">
                  <div class="vr__filter">
                    <span class="muted">Idioma</span>
                    <select class="vr__langsel" :value="ver.langFilter" @change="store.verSetLangFilter($event.target.value)">
                      <option value="">Todos ({{ ver.versions.length }})</option>
                      <option v-for="l in verLangs" :key="l" :value="l">{{ flag(l) }} {{ l }} ({{ ver.byLang[l].length }})</option>
                    </select>
                  </div>
                  <button class="btn-xs" @click="store.verDiscover(true)" title="Volver a buscar (ignora la caché)">↻ Buscar de nuevo</button>
                </div>

                <!-- versión principal fijada -->
                <div v-if="recommended" class="vr__primary">
                  <Icon name="check" :size="13" /> Principal: <strong>{{ recommended.sourceName }}</strong>
                  <span v-if="recommended.quality?.height" class="muted">· {{ recommended.quality.height }}px</span>
                </div>

                <!-- bandeja de comparación A|B -->
                <div v-if="ver.cmpSel.length" class="vr__tray">
                  <span class="muted">Comparar:</span>
                  <span v-for="(c, i) in ver.cmpSel" :key="i" class="vr__chip">
                    {{ c.sourceName }}
                    <button class="vr__chipx" @click="store.verToggleCompare(c)"><Icon name="close" :size="10" /></button>
                  </span>
                  <span v-if="ver.cmpSel.length < 2" class="vr__trayhint">elige {{ 2 - ver.cmpSel.length }} más</span>
                  <button class="hbtn hbtn--accent vr__traygo" :disabled="ver.cmpSel.length !== 2" @click="store.verRunCompare()">
                    <Icon name="globe" :size="13" /> Comparar A|B
                  </button>
                </div>

                <!-- descarga de versión en curso -->
                <div v-if="ver.dl" class="vr__dl">
                  <Spinner :size="14" />
                  <span>Descargando capítulos que faltan… {{ ver.dl.done }}/{{ ver.dl.total || '?' }}</span>
                </div>

                <!-- línea base: tu versión local -->
                <div v-if="ver.local" class="vr__row vr__row--local" :class="{ 'vr__row--cmp': isInCompare(localCand) }">
                  <div class="vr__main">
                    <span class="vr__src"><Icon name="library" :size="13" /> Tu versión local <span class="vr__badge vr__badge--actual">ACTUAL</span></span>
                    <span class="vr__q">{{ ver.local.height }}px · score {{ ver.local.score }}</span>
                  </div>
                  <div class="vr__acts">
                    <button class="vr__eye" :class="{ 'is-on': isInCompare(localCand) }" @click="store.verToggleCompare(localCand)" title="Añadir a comparación A|B">
                      <Icon name="globe" :size="13" /> A|B
                    </button>
                  </div>
                </div>

                <!-- ranking de candidatos -->
                <ul v-if="verList.length" class="vr__list">
                  <li v-for="c in verList" :key="verSampleKey(c)" class="vr__item">
                    <div class="vr__row" :class="{ 'vr__row--best': c === verBest && !isCurrentVersion(c) && !isPrimary(c), 'vr__row--primary': isPrimary(c), 'vr__row--cmp': isInCompare(c) }">
                      <div class="vr__main">
                        <span class="vr__src">
                          {{ c.sourceName }} <em class="vr__lang">{{ flag(c.sourceLang) }} {{ c.sourceLang }}</em>
                          <span v-if="isPrimary(c)" class="vr__badge vr__badge--primary">★ PRINCIPAL</span>
                          <span v-else-if="isCurrentVersion(c)" class="vr__badge vr__badge--actual">ACTUAL</span>
                          <span v-else-if="c === verBest" class="vr__badge vr__badge--best">★ mejor calidad</span>
                        </span>
                        <span class="vr__q">{{ c.quality?.height }}px · score {{ c.quality?.score }}</span>
                        <span v-if="isIrregular(c.quality)" class="vr__irr" :title="`Calidad irregular entre capítulos (${c.quality.heightMin}–${c.quality.heightMax}px). Algún capítulo es notablemente peor — penalizado en el ranking.`">⚠ irregular</span>
                      </div>
                      <div class="vr__acts">
                        <button class="vr__eye" :class="{ 'is-on': ver.sample.key === verSampleKey(c) && ver.sample.open }" @click="store.verReadSample(c)" title="Leer páginas de muestra">
                          <Icon name="search" :size="13" />
                        </button>
                        <button class="vr__eye" :class="{ 'is-on': isInCompare(c) }" @click="store.verToggleCompare(c)" title="Añadir a comparación A|B">
                          <Icon name="globe" :size="13" /> A|B
                        </button>
                        <button class="vr__eye" :disabled="!!ver.dl" @click="store.verDownloadVersion(c)" title="Descargar de esta versión los capítulos que falten">
                          <Icon name="download" :size="13" />
                        </button>
                        <button class="vr__fix" :class="{ 'is-on': isPrimary(c) }" @click="store.verSetPrimary(c)" :title="isPrimary(c) ? 'Quitar como principal' : 'Fijar como versión principal'">
                          {{ isPrimary(c) ? 'Fijada ✓' : 'Fijar' }}
                        </button>
                      </div>
                    </div>
                    <!-- tira de páginas de muestra -->
                    <div v-if="ver.sample.key === verSampleKey(c) && ver.sample.open" class="vr__prev">
                      <div v-if="ver.sample.loading" class="vr__prevload"><Spinner :size="16" /></div>
                      <div v-else-if="ver.sample.pages.length" class="tl__strip">
                        <a v-for="(u, i) in ver.sample.pages" :key="i" :href="u" target="_blank" rel="noopener" class="tl__thumb" :title="`Página ${i + 1}`">
                          <img :src="u" loading="lazy" referrerpolicy="no-referrer" />
                        </a>
                      </div>
                      <div v-else class="muted vr__prevempty">Sin páginas de muestra.</div>
                    </div>
                  </li>
                </ul>
                <div v-else class="empty">No hay versiones en este idioma.</div>
              </template>
            </div>

            <!-- TRADUCIR (trasplante) -->
            <div v-if="tab === 'translate' && !store.modalLoading" class="tl">
              <p class="tl__lead">Busca la mejor fuente de arte (cualquier idioma) y una en español, y trasplanta el texto ES sobre el arte HD. El resultado reemplaza los capítulos del manga.</p>

              <!-- tomos importados sin repartir en capítulos reales (aún no se intentó) -->
              <div v-if="tp.pendingVolumes.length" class="tl__vol">
                <div class="tl__volmsg">
                  <Icon name="folder" :size="14" />
                  <span>{{ tp.pendingVolumes.length }} tomo(s) importado(s) abarcan varios capítulos — hace falta repartirlos para que el match de traducción funcione por capítulo.</span>
                </div>
                <button class="hbtn hbtn--accent" :disabled="tp.volResolving || !tp.esSel" @click="store.tpResolveVolumes()">
                  <Spinner v-if="tp.volResolving" :size="13" /><Icon v-else name="globe" :size="14" /> Repartir capítulos
                </button>
                <span v-if="!tp.esSel" class="tl__err">Elige primero una fuente en español.</span>
              </div>

              <!-- tomos que no se pudieron repartir automáticamente (page count no cuadra) -->
              <div v-if="tp.unresolvedVolumes.length" class="tl__vol tl__vol--warn">
                <div v-for="vol in tp.unresolvedVolumes" :key="vol.prefix" class="tl__volitem">
                  <div class="tl__volmsg">
                    <Icon name="spark" :size="14" />
                    <span>Tomo de {{ vol.pages }} pág. (sugerido desde cap. {{ vol.chapterStartHint || vol.esChapters?.[0]?.chapter || '?' }}): no encontré una racha de capítulos ES que cuadre exacto — ajusta a mano.</span>
                  </div>
                  <button v-if="manualVol?.prefix !== vol.prefix" class="tl__re2" @click="openManualSplit(vol)">Repartir a mano</button>
                  <div v-else class="tl__manual">
                    <div class="tl__manualrow" v-for="(c, i) in manualCounts" :key="i">
                      <span class="muted">Cap.</span>
                      <input type="text" class="tl__manchin" v-model="c.chapter">
                      <input type="number" min="0" class="tl__maninput" v-model="c.pages">
                      <span class="muted">pág.</span>
                      <button class="tl__rmrow" @click="removeManualRow(i)"><Icon name="close" :size="11" /></button>
                    </div>
                    <button class="tl__re2" @click="addManualRow">+ Añadir capítulo</button>
                    <div class="tl__manualtotal" :class="{ 'is-bad': manualTotal !== vol.pages }">
                      Total: {{ manualTotal }} / {{ vol.pages }} pág.
                    </div>
                    <div class="tl__manualacts">
                      <button class="hbtn hbtn--accent" :disabled="manualTotal !== vol.pages" @click="submitManualSplit">Guardar reparto</button>
                      <button class="tl__re2" @click="closeManualSplit">Cancelar</button>
                    </div>
                  </div>
                </div>
              </div>

              <!-- descubrimiento en curso -->
              <div v-if="tp.loading" class="tl__disc">
                <Spinner :size="18" />
                <span class="muted">{{ tpPhaseLabel }}</span>
              </div>

              <!-- sin fuentes aún -->
              <div v-else-if="tp.phase !== 'ready'" class="tl__cta">
                <button class="hbtn hbtn--accent" @click="store.tpDiscover()"><Icon name="globe" :size="14" /> Buscar mejor fuente</button>
                <span v-if="tp.phase === 'error'" class="tl__err">No se encontraron fuentes. ¿Suwayomi en línea?</span>
              </div>

              <!-- fuentes elegidas + capítulos -->
              <template v-else>
                <div class="tl__picks">
                  <div class="tl__pick">
                    <div class="tl__pickh">Arte <button class="tl__re" @click="store.tpDiscover()" title="Volver a buscar">↻</button></div>
                    <div v-if="tp.artSel?.local" class="tl__cand">
                      <span class="tl__src">Arte local (importado)</span>
                    </div>
                    <div v-else-if="tp.artSel" class="tl__cand">
                      <span class="tl__src">{{ tp.artSel.sourceName }} · {{ tp.artSel.sourceLang }}</span>
                      <span v-if="tp.artSel.quality" class="tl__q">{{ tp.artSel.quality.height }}px · score {{ tp.artSel.quality.score }}</span>
                    </div>
                    <select v-if="tp.artCands.length" class="tl__sel" :value="store._candKey(tp.artSel)" @change="onArt">
                      <option v-for="c in tp.artCands" :key="store._candKey(c)" :value="store._candKey(c)">{{ c.sourceName }} ({{ c.sourceLang }}) — {{ c.quality?.height }}px / {{ c.quality?.score }}</option>
                    </select>
                  </div>
                  <div class="tl__pick">
                    <div class="tl__pickh">Español</div>
                    <div v-if="tp.esSel" class="tl__cand">
                      <span class="tl__src">{{ tp.esSel.sourceName }} · {{ tp.esSel.sourceLang }}</span>
                      <span v-if="tp.esSel.quality" class="tl__q">{{ tp.esSel.quality.height }}px</span>
                    </div>
                    <select v-if="tp.esCands.length" class="tl__sel" :value="store._candKey(tp.esSel)" @change="onEs">
                      <option v-for="c in tp.esCands" :key="store._candKey(c)" :value="store._candKey(c)">{{ c.sourceName }} ({{ c.sourceLang }}) — {{ c.quality?.height }}px</option>
                    </select>
                  </div>
                </div>

                <!-- progreso de ejecución -->
                <div v-if="tp.running" class="tl__run">
                  <div class="tl__bar"><div class="tl__fill" :style="{ width: tpRunPct + '%' }" /></div>
                  <div class="tl__runinfo">
                    <span class="muted">Cap. {{ tp.runStatus?.chapterDone || 0 }}/{{ tp.runStatus?.chapterTotal || 0 }}
                      <template v-if="tp.runStatus?.chapter"> · #{{ tp.runStatus.chapter }}</template>
                      <template v-if="tp.runStatus?.phase === 'compose' && tp.runStatus?.pageTotal"> · pág {{ tp.runStatus.pageDone }}/{{ tp.runStatus.pageTotal }}</template>
                      <template v-else-if="tp.runStatus?.phase === 'download'"> · descargando</template>
                      <template v-else-if="tp.runStatus?.phase === 'cancelling'"> · deteniendo…</template>
                    </span>
                    <button class="tl__stop" @click="store.tpCancel()"><Icon name="close" :size="13" /> Parar</button>
                  </div>
                </div>

                <!-- lista de capítulos -->
                <div class="tl__chhead">
                  <span>Capítulos a traducir <span v-if="tp.chapters.length" class="muted">({{ tp.chapters.length }})</span></span>
                  <div class="tl__acts">
                    <button class="btn-xs" :disabled="tp.running || !tpSel.size" @click="store.tpRun([...tpSel])">Traducir {{ tpSel.size || '' }} sel.</button>
                    <button class="btn-xs btn-xs--accent" :disabled="tp.running || !tp.chapters.length" @click="store.tpRun('all')">Traducir todos</button>
                  </div>
                </div>
                <div v-if="tp.chaptersLoading" class="center"><Spinner :size="20" /></div>
                <ul v-else-if="tp.chapters.length" class="tl__chaps">
                  <li v-for="c in tp.chapters" :key="c.chapter" class="tl__chapwrap">
                    <div class="tl__chap" :class="{ 'is-sel': tpSel.has(c.chapter) }">
                      <span class="tl__box" @click="toggleTp(c.chapter)"><Icon v-if="tpSel.has(c.chapter)" name="check" :size="11" /></span>
                      <span class="tl__cnum" @click="toggleTp(c.chapter)">Cap. {{ c.chapter }}</span>
                      <span class="tl__chip" :class="'tl__chip--' + (tp.runStatus?.chapter === c.chapter && tp.running ? 'doing' : c.status)">
                        {{ tp.runStatus?.chapter === c.chapter && tp.running ? 'traduciendo' : c.status === 'done' ? 'hecho' : c.status === 'failed' ? 'falló' : 'pendiente' }}
                      </span>
                      <button class="tl__eye" :class="{ 'is-on': tp.preview[c.chapter]?.open }" @click.stop="store.tpTogglePreview(c.chapter)" title="Vista previa del arte">
                        <Icon name="search" :size="13" />
                      </button>
                    </div>
                    <div v-if="tp.preview[c.chapter]?.open" class="tl__prev">
                      <div v-if="tp.preview[c.chapter].loading" class="tl__prevload"><Spinner :size="16" /></div>
                      <div v-else-if="tp.preview[c.chapter].pages.length" class="tl__strip">
                        <a v-for="(u, i) in tp.preview[c.chapter].pages" :key="i" :href="u" target="_blank" rel="noopener" class="tl__thumb" :title="`Página ${i + 1}`">
                          <img :src="u" loading="lazy" referrerpolicy="no-referrer" />
                        </a>
                      </div>
                      <div v-else class="muted tl__prevempty">Sin páginas.</div>
                    </div>
                  </li>
                </ul>
                <div v-else class="empty">Sin capítulos comunes a ambas fuentes.</div>
              </template>
            </div>

            <template v-if="tab === 'chapters' && !store.modalLoading && (store.chapters.length || store.hasSourceMeta)">
            <!-- Taller: exclusión manual de páginas a color antes de escalar -->
            <div v-if="m.source_meta?.imported" class="colors colors--ws">
              <div class="colors__head">
                <span class="colors__title">Páginas a color (excluir del escalado)</span>
                <button class="btn-xs" :disabled="store.colorLoading || !store.chapters.length" @click="store.loadColorPages(store.chapters.map(c => c.chapter))">
                  <span v-if="store.colorLoading" class="xspin" />Detectar
                </button>
              </div>
              <div v-if="store.colorPages.length" class="colors__grid">
                <button v-for="cp in store.colorPages" :key="cp.filename" class="colorpg" :class="{ 'is-excl': store.excludedPages.includes(cp.filename) }"
                        :title="cp.label + (store.excludedPages.includes(cp.filename) ? ' (excluida)' : '')" @click="store.toggleExclude(cp.filename)">
                  <img :src="cp.url" loading="lazy" alt="" />
                  <span v-if="store.excludedPages.includes(cp.filename)" class="colorpg__x"><Icon name="close" :size="12" /></span>
                </button>
              </div>
              <p v-else-if="!store.colorLoading" class="colors__hint">Detecta y excluye páginas a color antes de pulsar "Escalar todo 4K".</p>
            </div>
            <div class="modal__chhead">
              <span>Capítulos</span>
              <span v-if="store.effectiveSource?.pinned" class="chsrc" :title="`Fuente fijada: ${store.effectiveSource.sourceName}`">
                <Icon name="spark" :size="11" /> {{ store.effectiveSource.sourceName || 'versión fijada' }}
              </span>
              <select v-if="store.mdLangs.length > 1" v-model="store.mdLang" class="langsel">
                <option value="">Todos</option>
                <option v-for="l in store.mdLangs" :key="l" :value="l">{{ flag(l) }} {{ l }}</option>
              </select>
            </div>

            <!-- Continuar leyendo donde lo dejaste -->
            <button v-if="store.continueInfo()" class="contbar" @click="store.resumeCurrent()">
              <Icon name="spark" :size="14" />
              <span class="contbar__t">Continuar — Cap. {{ formatChapter(store.continueInfo().chapter) }}</span>
              <span v-if="store.continueInfo().total" class="contbar__p">pág {{ store.continueInfo().page + 1 }}/{{ store.continueInfo().total }}</span>
            </button>

            <ul class="chaps">
              <template v-for="c in (store.hasSourceMeta ? store.mergedChapters : store.sortedChapters)" :key="c.chapter">
              <li class="chap"
                  :class="{ 'chap--4k': upState(c.chapter) === true, 'chap--part': upState(c.chapter) === 'partial', 'chap--src': c._sourceId || c._mdChapterId, 'chap--md': !!c._mdChapterId, 'chap--read': store.isChapterRead(c.chapter) }">
                <!-- Downloaded chapter: clic = leer · clic derecho = marcar/desmarcar leído -->
                <button v-if="!c._sourceId && !c._mdChapterId" class="chap__read" @click="store.read(c.chapter)"
                        @contextmenu.prevent="store.toggleChapterRead(c.chapter)" :title="store.isChapterRead(c.chapter) ? 'Leído · clic derecho para desmarcar' : 'Clic derecho: marcar leído'">
                  <span v-if="store.isChapterRead(c.chapter)" class="chap__read-dot" title="Leído" />
                  <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                  <span class="chap__pages">{{ c.page_count }} pág.</span>
                  <span v-if="upState(c.chapter) === true" class="chap__tag chap__tag--4k">4K</span>
                  <span v-else-if="upState(c.chapter) === 'partial'" class="chap__tag chap__tag--part" :title="store.health[c.chapter] ? `Faltan ${store.health[c.chapter].missing_upscaled} págs.` : ''">PARCIAL<template v-if="store.health[c.chapter]?.missing_upscaled"> ·{{ store.health[c.chapter].missing_upscaled }}</template></span>
                </button>
                <!-- Source/MD chapter: not clickable, show download info -->
                <div v-else class="chap__read">
                  <span v-if="c._mdLang" class="chap__flag" :title="c._mdLang">{{ flag(c._mdLang) }}</span>
                  <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                  <span class="chap__pages" v-if="c._sourceId">vía {{ store.current.source_meta?.sourceName }}</span>
                  <span class="chap__pages" v-else-if="c._mdLang">{{ c._mdGroup || 'MangaDex' }}<template v-if="c.page_count"> · {{ c.page_count }} pág.</template></span>
                  <span class="chap__pages" v-else>MangaDex</span>
                </div>

                <div class="chap__actions">
                  <!-- Source / MD chapter: download button with spinner -->
                  <template v-if="c._sourceId || c._mdChapterId">
                    <span v-if="c._mdGroup || c._mdTitle" class="chap__srcmeta">{{ c._mdGroup || c._scanlator }}<template v-if="c._mdTitle"> · {{ c._mdTitle }}</template></span>
                    <template v-if="store.downloadByChapter[c.chapter]">
                      <div class="chap__dlprog">
                        <svg class="dl-ring" viewBox="0 0 24 24">
                          <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                          <circle class="dl-ring__fill" cx="12" cy="12" r="9"
                            :style="{ strokeDashoffset: 56.5 - (56.5 * (store.downloadByChapter[c.chapter].pct / 100)) }" />
                        </svg>
                        <span class="chap__dlprog-n" v-if="store.downloadByChapter[c.chapter].total">{{ store.downloadByChapter[c.chapter].pct }}%</span>
                      </div>
                    </template>
                    <template v-else>
                      <button class="chap__dlbtn chap__dlbtn--ghost"
                        :disabled="store.onlineLoadingId === (c._sourceId || c._mdChapterId)" @click="store.readOnline(c)">
                        <Spinner v-if="store.onlineLoadingId === (c._sourceId || c._mdChapterId)" :size="13" /><Icon v-else name="library" :size="14" /> Leer
                      </button>
                      <button class="chap__dlbtn"
                        @click="c._sourceId ? store.downloadSourceChapter(c) : store.downloadMdChapter(c)">
                        <Icon name="download" :size="14" /> Descargar
                      </button>
                    </template>
                  </template>
                  <!-- running upscale -->
                  <template v-else>
                  <div v-if="store.upscaleByChapter[c.chapter]" class="chap__dlprog">
                    <svg class="dl-ring" viewBox="0 0 24 24">
                      <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                      <circle class="dl-ring__fill" cx="12" cy="12" r="9" :style="{ strokeDashoffset: 56.5 - (56.5 * (store.upscaleByChapter[c.chapter].pct / 100)) }" />
                    </svg>
                    <span class="chap__dlprog-n" v-if="store.upscaleByChapter[c.chapter].total">{{ store.upscaleByChapter[c.chapter].pct }}%</span>
                    <button class="ib ib--danger" title="Cancelar" @click="store.cancelUpscale(c.chapter)"><Icon name="close" :size="13" /></button>
                  </div>
                  <template v-else>
                    <button class="ib" :class="{ 'ib--read': store.isChapterRead(c.chapter) }" :title="store.isChapterRead(c.chapter) ? 'Marcar no leído' : 'Marcar leído'" @click="store.toggleChapterRead(c.chapter)"><Icon name="check" :size="14" /></button>
                    <button class="ib" title="Comparar versiones (scanlations)" :class="{ 'ib--accent': store.scanCmp.open && store.scanCmp.chapter === c.chapter }" @click="store.openComparePanel(c.chapter)"><Icon name="globe" :size="14" /></button>
                    <button class="ib" title="Leer original" @click="store.read(c.chapter, 'original')"><Icon name="library" :size="14" /></button>
                    <button v-if="upState(c.chapter) === 'partial'" class="ib ib--warn" title="Reparar upscale" @click="store.repairChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button v-else-if="upState(c.chapter) !== true" class="ib ib--accent" title="Escalar a 4K" @click="store.upscaleChapter(c.chapter, m.source_meta?.imported ? { excludePages: store.excludedPages } : {})"><Icon name="spark" :size="14" /></button>
                    <button class="ib ib--danger" title="Borrar capítulo" @click="store.deleteChapter(c.chapter)"><Icon name="close" :size="14" /></button>
                  </template>
                  </template>
                </div>

                <!-- scanlation variants compare panel -->
                <div v-if="store.scanCmp.open && store.scanCmp.chapter === c.chapter" class="cmpvar">
                  <div v-if="store.scanCmp.loading" class="cmpvar__load"><Spinner :size="14" /></div>
                  <template v-else-if="store.scanCmp.variants.length">
                    <span class="cmpvar__lbl">Comparar con variante descargada:</span>
                    <button v-for="v in store.scanCmp.variants" :key="v.dir" class="cmpvar__item" @click="store.readCompareSources(c.chapter, v.dir)">
                      {{ v.group }} <em>{{ v.lang }}</em> · {{ v.page_count }} pág.
                    </button>
                  </template>
                  <span v-else class="cmpvar__none">No hay variantes descargadas en <code>_compare/</code> para este capítulo.</span>
                </div>
              </li>
              </template>
            </ul>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5);
  background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(45rem, 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { color: var(--ink); border-color: var(--line-strong); }

.modal__head { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); flex-shrink: 0; }
/* align-self:flex-start stops the flex row from stretching the cover to the (taller)
   info column's height. Keep the natural aspect ratio (width fixed, height auto) so the
   cover is shown whole — no cropping the sides, no distortion. */
.modal__cover { width: 132px; height: auto; align-self: flex-start; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); width: 132px; aspect-ratio: 2/3; }
.modal__info { min-width: 0; padding-right: var(--s-7); }
.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); }
.modal__sub { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: var(--s-1); }
.mdlink { display: inline-flex; align-items: center; gap: 5px; margin-top: var(--s-2); font-size: var(--fs-xs); font-weight: 500; color: var(--violet); text-decoration: none; padding: 4px 10px; border-radius: var(--r-sm); border: 1px solid color-mix(in srgb, var(--violet) 25%, transparent); transition: all var(--t-fast); }
.mdlink:hover { background: color-mix(in srgb, var(--violet) 10%, transparent); border-color: var(--violet); }
.modal__status { margin-top: var(--s-2); margin-left: var(--s-2); padding: 4px 10px; border-radius: var(--r-sm);
  background: var(--surface); border: 1px solid var(--line-2); font-size: var(--fs-xs); font-weight: 600; cursor: pointer; }
.modal__status:focus { outline: none; border-color: var(--azure); }
.modal__legend { display: flex; gap: var(--s-3); margin-top: var(--s-3); font-size: var(--fs-2xs); color: var(--ink-faint); }
.modal__legend span { display: inline-flex; align-items: center; gap: 5px; }
.lg { width: 8px; height: 8px; border-radius: 2px; }
.lg--4k { background: var(--cyan); } .lg--part { background: var(--gold); } .lg--orig { background: var(--ink-ghost); }

.modal__hacts { display: flex; gap: var(--s-2); margin-top: var(--s-3); }
.hbtn { display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.hbtn:hover { color: var(--ink); border-color: var(--line-strong); }
.hbtn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.hbtn--accent { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 30%, transparent); }
.hbtn--accent:hover { background: var(--cyan-glow); color: #d6fffb; }
.mf--chk { flex-direction: row; align-items: center; justify-content: space-between; }
.mf--chk input { width: auto; }
.manage { padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); background: var(--base); overflow: hidden; flex-shrink: 0; }
/* expand/collapse animation for the management panel */
.info-enter-active, .info-leave-active { transition: max-height var(--t-base) var(--ease-silk), opacity var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { max-height: 0; opacity: 0; }
.info-enter-to, .info-leave-from { max-height: 340px; opacity: 1; }
.manage__sect { display: flex; flex-direction: column; gap: var(--s-2); }
.manage__sect + .manage__sect { margin-top: var(--s-3); padding-top: var(--s-3); border-top: 1px solid var(--line); }
.manage__label { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); text-transform: uppercase; color: var(--ink-faint); }
.manage__label em { font-style: normal; text-transform: none; letter-spacing: 0; color: var(--ink-ghost); }
.manage__sect--up .manage__label { color: var(--cyan); }
.mf em { font-style: normal; color: var(--ink-ghost); }
.manage__row { display: flex; gap: var(--s-3); }
.mf { flex: 1; display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-xs); color: var(--ink-faint); }
.mf input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.mf input:focus { outline: none; border-color: var(--azure); }
.mf select {
  padding: var(--s-2) 30px var(--s-2) var(--s-3); border-radius: var(--r-sm);
  background-color: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm);
  cursor: pointer; appearance: none; -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239aa7bd' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.mf select:hover { border-color: var(--line-strong); }
.mf select:focus { outline: none; border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.mf select option { background: var(--surface-2); color: var(--ink); }
.manage__actions { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); }
.manage__spacer { flex: 1; }
.delbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); background: transparent; transition: all var(--t-fast); }
.delbtn:hover { background: color-mix(in srgb, var(--coral) 12%, transparent); border-color: var(--coral); }
.upbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); cursor: pointer; }
.upbtn:hover { color: var(--ink); }
.savebtn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }
.savebtn:hover { background: var(--azure-bright); }

/* ── Selector visual de portada ───────────────────────────────────────── */
.cvp__toggle { display: inline-flex; align-items: center; gap: 6px; margin-top: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); font-size: var(--fs-xs); color: var(--azure-bright); }
.cvp__toggle:hover { border-color: var(--azure); background: var(--azure-haze); }
.cvp { margin-top: var(--s-2); }
.cvp__load { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); padding: var(--s-3); }
.cvp__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(5rem, 1fr)); gap: var(--s-2); max-height: 18rem; overflow-y: auto; padding: 2px; }
.cvp__item { position: relative; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; border: 2px solid transparent; background: var(--surface-2); cursor: pointer; transition: border-color var(--t-fast); }
.cvp__item:hover:not(:disabled) { border-color: var(--azure); }
.cvp__item.is-current { border-color: var(--azure-bright); cursor: default; }
.cvp__item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cvp__item:disabled { opacity: .7; cursor: progress; }
.cvp__tag { position: absolute; bottom: 0; left: 0; right: 0; font-size: 9px; line-height: 1.4; text-align: center; background: rgba(0,0,0,.6); color: #fff; padding: 1px 2px; }
.cvp__tag--al { background: color-mix(in oklab, var(--azure) 75%, #000); }
.cvp__busy { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.45); }
.cvp__empty { font-size: var(--fs-2xs); color: var(--ink-faint); padding: var(--s-2); }

.modal__tabs { display: flex; gap: var(--s-1); padding: var(--s-2) var(--s-4) 0; border-bottom: 1px solid var(--line); flex-shrink: 0; }
.mtab { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm) var(--r-sm) 0 0; font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint); border-bottom: 2px solid transparent; transition: all var(--t-fast); }
.mtab:hover { color: var(--ink); }
.mtab.is-active { color: var(--azure-bright); border-bottom-color: var(--azure); }
.mtab__badge { font-size: 9px; font-weight: 800; letter-spacing: .04em; padding: 1px 5px; border-radius: var(--r-pill); background: var(--jade); color: #04130c; }

/* Pestaña Traducir */
.tl { padding: var(--s-2) 0 var(--s-4); }
.tl__lead { font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); margin-bottom: var(--s-3); }
.tl__disc { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4); }
.tl__cta { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) 0; }
.tl__err { font-size: var(--fs-xs); color: var(--rose, #e8748b); }
.tl__picks { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-3); margin-bottom: var(--s-3); }
.tl__pick { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--s-3); }
.tl__pickh { display: flex; align-items: center; justify-content: space-between; font-weight: 600; font-size: var(--fs-xs); margin-bottom: 6px; }
.tl__re { color: var(--ink-faint); font-size: var(--fs-sm); padding: 0 4px; }
.tl__re:hover { color: var(--azure-bright); }
.tl__cand { display: flex; flex-direction: column; }
.tl__src { font-size: var(--fs-xs); color: var(--azure-bright); font-weight: 600; }
.tl__q { font-size: var(--fs-2xs); color: var(--ink-faint); }
.tl__sel { width: 100%; margin-top: 6px; font-size: var(--fs-2xs); padding: 4px 6px; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.tl__run { margin: var(--s-3) 0; }
.tl__bar { height: 6px; border-radius: var(--r-pill); background: var(--surface-2); overflow: hidden; }
.tl__fill { height: 100%; background: var(--azure); transition: width var(--t-base); }
.tl__runinfo { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
.tl__stop { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--rose, #e8748b); border: 1px solid color-mix(in srgb, var(--rose, #e8748b) 40%, transparent); }
.tl__stop:hover { background: color-mix(in srgb, var(--rose, #e8748b) 12%, transparent); }
.tl__chhead { display: flex; align-items: center; justify-content: space-between; margin: var(--s-3) 0 var(--s-2); font-weight: 600; }
.tl__acts { display: flex; gap: var(--s-2); }
.btn-xs--accent { color: #fff; background: var(--azure); border-color: transparent; }
.btn-xs--accent:hover:not(:disabled) { filter: brightness(1.1); }
.btn-xs:disabled { opacity: .45; cursor: default; }
.tl__chaps { max-height: 280px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tl__chap { display: flex; align-items: center; gap: var(--s-2); padding: 6px var(--s-2); border-radius: var(--r-sm); cursor: pointer; transition: background var(--t-fast); }
.tl__chap:hover { background: var(--surface); }
.tl__chap.is-sel { background: var(--azure-haze); }
.tl__box { width: 16px; height: 16px; flex-shrink: 0; display: grid; place-items: center; border-radius: 4px; border: 1px solid var(--line-strong); color: var(--azure-bright); }
.tl__chap.is-sel .tl__box { border-color: var(--azure); }
.tl__cnum { flex: 1; font-size: var(--fs-sm); cursor: pointer; }
.tl__box { cursor: pointer; }
.tl__eye { display: grid; place-items: center; width: 26px; height: 26px; flex-shrink: 0; border-radius: var(--r-sm); color: var(--ink-faint); border: 1px solid var(--line); }
.tl__eye:hover { color: var(--azure-bright); border-color: var(--azure); }
.tl__eye.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure); }
.tl__prev { padding: var(--s-2) var(--s-2) var(--s-3) 26px; }
.tl__prevload, .tl__prevempty { padding: var(--s-2); font-size: var(--fs-2xs); }
.tl__strip { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 6px; }
.tl__thumb { flex-shrink: 0; width: 64px; aspect-ratio: 2/3; border-radius: var(--r-sm); overflow: hidden; border: 1px solid var(--line); background: var(--surface-2); }
.tl__thumb img { width: 100%; height: 100%; object-fit: cover; }
.tl__thumb:hover { border-color: var(--azure); }
.tl__chip { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: var(--r-pill); text-transform: uppercase; letter-spacing: .03em; }
.tl__chip--pending { color: var(--ink-faint); background: var(--surface-2); }
.tl__chip--done { color: var(--jade); background: color-mix(in srgb, var(--jade) 14%, transparent); }
.tl__chip--failed { color: var(--rose, #e8748b); background: color-mix(in srgb, var(--rose, #e8748b) 14%, transparent); }
.tl__chip--doing { color: var(--azure-bright); background: var(--azure-haze); }
.muted { color: var(--ink-faint); font-weight: 400; }

/* Pestaña Versiones (ranking de calidad de imagen) */
.vr { padding: var(--s-2) 0 var(--s-4); }
.vr__lead { font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); margin-bottom: var(--s-3); }
.vr__disc { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4); }
.vr__cta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); padding: var(--s-3) 0; }
.vr__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); }
.vr__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--s-3); }
.vr__filter { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); }
.vr__langsel { font-size: var(--fs-xs); padding: 4px 8px; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); cursor: pointer; }
.vr__list { display: flex; flex-direction: column; gap: var(--s-1); }
.vr__row { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.vr__row--local { margin-bottom: var(--s-2); background: var(--surface-2); border-style: dashed; }
.vr__row--best { border-color: color-mix(in srgb, var(--cyan) 45%, transparent); background: var(--cyan-glow, color-mix(in srgb, var(--cyan) 8%, transparent)); }
.vr__main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.vr__src { display: inline-flex; align-items: center; gap: 5px; font-size: var(--fs-sm); font-weight: 600; color: var(--ink); }
.vr__lang { font-style: normal; font-weight: 400; font-size: var(--fs-2xs); color: var(--ink-faint); }
.vr__q { font-size: var(--fs-2xs); color: var(--ink-faint); font-family: var(--font-mono); }
.vr__irr { margin-left: 0.5rem; font-size: var(--fs-2xs); color: var(--warn); white-space: nowrap; cursor: help; }
.vr__badge { font-size: 9px; font-weight: 800; letter-spacing: .04em; padding: 2px 7px; border-radius: var(--r-pill); flex-shrink: 0; }
.vr__badge--actual { background: var(--ink-ghost); color: var(--base); }
.vr__badge--best { background: var(--cyan); color: #04130c; }
.vr__eye { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; padding: 5px 10px; border-radius: var(--r-sm); font-size: var(--fs-2xs); color: var(--ink-faint); border: 1px solid var(--line); }
.vr__eye:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.vr__eye.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure); }
.vr__eye:disabled { opacity: .4; cursor: default; }
.vr__dl { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); font-size: var(--fs-xs); color: var(--ink-soft); }
.vr__prev { padding: var(--s-2) var(--s-1) var(--s-1); }
.vr__prevload, .vr__prevempty { padding: var(--s-2); font-size: var(--fs-2xs); }
.vr__acts { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.vr__fix { padding: 5px 12px; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.vr__fix:hover { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 45%, transparent); }
.vr__fix.is-on { color: #04130c; background: var(--cyan); border-color: transparent; }
.vr__row--primary { border-color: color-mix(in srgb, var(--cyan) 55%, transparent); }
.vr__row--cmp { box-shadow: 0 0 0 1px var(--azure) inset; }
.vr__badge--primary { background: var(--cyan); color: #04130c; }
.vr__primary { display: flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--cyan); margin-bottom: var(--s-2); }
.vr__primary strong { color: var(--ink); font-weight: 600; }
.vr__tray { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.vr__chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 4px 3px 10px; border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; background: var(--surface); border: 1px solid var(--line); }
.vr__chipx { display: grid; place-items: center; width: 16px; height: 16px; border-radius: 50%; color: var(--ink-faint); }
.vr__chipx:hover { color: var(--coral); }
.vr__trayhint { font-size: var(--fs-2xs); color: var(--ink-faint); }
.vr__traygo { margin-left: auto; }

/* tomos sin repartir en capítulos reales */
.tl__vol { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-3); margin-bottom: var(--s-3); border-radius: var(--r-md); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.tl__vol--warn { background: color-mix(in srgb, var(--rose, #e8748b) 10%, transparent); border-color: color-mix(in srgb, var(--rose, #e8748b) 30%, transparent); }
.tl__volitem { display: flex; flex-direction: column; gap: var(--s-2); padding-bottom: var(--s-2); }
.tl__volitem + .tl__volitem { padding-top: var(--s-2); border-top: 1px solid color-mix(in srgb, var(--rose, #e8748b) 20%, transparent); }
.tl__volmsg { display: flex; align-items: flex-start; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-soft); line-height: var(--lh-body); }
.tl__re2 { align-self: flex-start; font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); padding: 4px 10px; border-radius: var(--r-sm); border: 1px solid var(--azure); }
.tl__re2:hover { background: var(--azure-haze); }
.tl__manual { display: flex; flex-direction: column; gap: 6px; padding: var(--s-2); background: var(--surface); border-radius: var(--r-sm); }
.tl__manualrow { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); }
.tl__manchin { width: 3.6rem; font-size: var(--fs-xs); padding: 3px 6px; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.tl__maninput { width: 5rem; font-size: var(--fs-xs); padding: 3px 6px; border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.tl__rmrow { color: var(--ink-faint); transition: color var(--t-fast); margin-left: auto; }
.tl__rmrow:hover { color: var(--coral); }
.tl__manualtotal { font-size: var(--fs-2xs); color: var(--ink-faint); }
.tl__manualtotal.is-bad { color: var(--rose, #e8748b); font-weight: 600; }
.tl__manualacts { display: flex; gap: var(--s-2); }

/* flex-basis auto (not 0): the modal has max-height, not a fixed height, so basis:0
   would collapse this scroll region to 0 and hide the chapters. auto lets it size to
   content and only shrink+scroll once the modal hits its max-height. */
.modal__body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--s-3); }

/* tomo export */
.tomo { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); padding: var(--s-2); align-items: start; }
.tomo__form { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; gap: var(--s-3); }
.mdex { grid-column: 1; grid-row: 2; border-top: 1px solid var(--line); padding-top: var(--s-3); }
.tomo__pick { grid-column: 2; grid-row: 1 / span 2; }

.mdex__head { display: flex; align-items: center; justify-content: space-between; font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: var(--s-2); }
.btn-xs { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); }
.btn-xs:hover { background: var(--azure-haze); }
.xspin { width: 11px; height: 11px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; display: inline-block; }
.mdex__match { font-size: var(--fs-2xs); color: var(--jade); margin-bottom: var(--s-2); display: flex; align-items: center; gap: 5px; }
.mdex__match.is-approx { color: var(--amber, var(--ink-faint)); }
.mdex__match a { color: var(--azure-bright); text-decoration: none; }
.mdex__search { display: flex; gap: var(--s-2); margin-bottom: var(--s-2); }
.mdex__search input { flex: 1; min-width: 0; padding: 5px 9px; border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-2xs); }
.mdex__search input:focus { outline: none; border-color: var(--azure); }
.mdex__results { display: flex; flex-direction: column; gap: 4px; max-height: 180px; overflow-y: auto; margin-bottom: var(--s-3); }
.mdres { display: flex; align-items: center; gap: var(--s-2); padding: 4px; border-radius: var(--r-sm); border: 1px solid var(--line); text-align: left; transition: all var(--t-fast); }
.mdres:hover { border-color: var(--azure); background: var(--azure-haze); }
.mdres img { width: 28px; height: 40px; object-fit: cover; border-radius: var(--r-xs); flex-shrink: 0; }
.mdres__t { font-size: var(--fs-2xs); color: var(--ink-soft); line-height: 1.25; }
.mdres__t small { color: var(--ink-faint); }
.mdex__vols { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: var(--s-3); }
.volchip { padding: 4px 10px; border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--violet); border: 1px solid color-mix(in srgb, var(--violet) 30%, transparent); transition: all var(--t-fast); }
.volchip:hover { background: color-mix(in srgb, var(--violet) 14%, transparent); }
.mdex__covers { display: grid; grid-template-columns: repeat(auto-fill, minmax(3rem, 1fr)); gap: 6px; max-height: 180px; overflow-y: auto; }
.covsel { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.covsel img { width: 100%; height: 100%; object-fit: cover; }
.covsel.is-sel { border-color: var(--azure); }
.covsel__v { position: absolute; bottom: 0; left: 0; right: 0; font-family: var(--font-mono); font-size: 8px; text-align: center; background: rgba(7,10,18,.75); color: var(--ice); }
.covsel__load { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(7,10,18,.6); }

.tomo__form { display: flex; flex-direction: column; gap: var(--s-3); }
.fld { display: flex; flex-direction: column; gap: 5px; font-size: var(--fs-xs); color: var(--ink-faint); }
.fld input[type=text], .fld select { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.fld input:focus, .fld select:focus { outline: none; border-color: var(--azure); }
.fld-row { display: flex; gap: var(--s-3); }
.fld-row .fld { flex: 1; }
.codec-hint { margin: calc(-1 * var(--s-1)) 0 0; font-size: var(--fs-2xs); line-height: 1.4; color: var(--ink-faint); }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; }
.exportbtn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; margin-top: var(--s-2); padding: var(--s-3); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.exportbtn:hover:not(:disabled) { background: var(--azure-bright); }
.exportbtn:disabled { opacity: .5; cursor: not-allowed; }
.exportbtn--drive { background: transparent; color: var(--azure-bright); border: 1px solid var(--azure); margin-top: var(--s-1); }
.exportbtn--drive:hover:not(:disabled) { background: var(--azure-haze); color: var(--azure-bright); }
.rangebox { display: inline-flex; align-items: center; gap: 3px; }
.rangebox input { width: 42px; padding: 4px 6px; border-radius: var(--r-xs); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-2xs); text-align: center; }
.rangebox button { padding: 4px 8px; border-radius: var(--r-xs); font-size: var(--fs-2xs); font-weight: 600; color: var(--cyan); border: 1px solid color-mix(in srgb, var(--cyan) 30%, transparent); }
.rangebox button:hover { background: var(--cyan-glow); color: #d6fffb; }
.chap__read-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--jade); flex-shrink: 0; }
.chap__flag { font-size: 1.05rem; line-height: 1; flex-shrink: 0; }
.colors { margin-top: var(--s-2); }
.colors--ws { margin: 0 0 var(--s-4); padding: var(--s-3); border-radius: var(--r-md); background: var(--surface-2); border: 1px solid var(--line); }
.colors__head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); }
.colors__title { font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); }
.colors__hint { font-size: var(--fs-2xs); color: var(--ink-faint); margin-top: var(--s-1); }
.colors__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(2.75rem, 1fr)); gap: 5px; margin-top: var(--s-2); max-height: 150px; overflow-y: auto; }
.colorpg { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.colorpg img { width: 100%; height: 100%; object-fit: cover; }
.colorpg.is-excl { border-color: var(--coral); }
.colorpg.is-excl img { opacity: .4; }
.colorpg__x { position: absolute; inset: 0; display: grid; place-items: center; color: var(--coral); background: rgba(7,10,18,.4); }
.tprev { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-top: var(--s-1); font-size: var(--fs-xs); color: var(--ink-soft); }
.tprev__main { font-family: var(--font-mono); color: var(--ink); }
.tprev__tag { font-size: var(--fs-2xs); padding: 1px 7px; border-radius: var(--r-pill); color: var(--ink-faint); border: 1px solid var(--line-2); }
.tprev__tag--4k { color: var(--cyan); border-color: var(--cyan-glow); }
.dests { display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center; margin-top: var(--s-2); font-size: var(--fs-xs); }
.dlink { color: var(--azure-bright); }
.dok { color: var(--jade); }

.tomo__pick { display: flex; flex-direction: column; min-height: 0; }
.selall { align-self: flex-start; margin-bottom: var(--s-2); font-size: var(--fs-xs); color: var(--azure-bright); }
.picklist { overflow-y: auto; max-height: 320px; display: flex; flex-direction: column; gap: 2px; }
.pick { display: flex; align-items: center; gap: var(--s-2); padding: 6px var(--s-2); border-radius: var(--r-xs); cursor: pointer; transition: background var(--t-fast); }
.pick:hover { background: var(--surface); }
.pick.is-sel { background: var(--azure-haze); }
.pick__box { width: 18px; height: 18px; display: grid; place-items: center; border-radius: 4px; border: 1px solid var(--line-strong); color: #fff; flex-shrink: 0; }
.pick.is-sel .pick__box { background: var(--azure); border-color: var(--azure); }
.pick__num { flex: 1; font-size: var(--fs-sm); }
.pick__4k { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

@media (max-width: 560px) { .tomo { grid-template-columns: 1fr; } }
.center { display: grid; place-items: center; padding: var(--s-7); }
.empty { text-align: center; color: var(--ink-faint); padding: var(--s-7); }

.chaps { display: flex; flex-direction: column; gap: 4px; }
.modal__chhead { display: flex; align-items: center; justify-content: space-between; padding: var(--s-3) var(--s-3); font-weight: 600; font-size: var(--fs-sm); border-bottom: 1px solid var(--line); }
.contbar { display: flex; align-items: center; gap: var(--s-2); width: 100%; margin: var(--s-2) 0; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--azure-haze); border: 1px solid var(--azure); color: var(--azure-bright); font-size: var(--fs-sm); font-weight: 500; transition: background var(--t-fast); }
.contbar:hover { background: color-mix(in oklab, var(--azure) 22%, transparent); }
.contbar__t { flex: 1; text-align: left; }
.contbar__p { font-size: var(--fs-2xs); color: var(--ink-faint); font-variant-numeric: tabular-nums; }
.chap--read { opacity: .55; }
.chap--read:hover { opacity: 1; }
.chsrc { display: inline-flex; align-items: center; gap: 4px; margin-right: auto; margin-left: var(--s-3); padding: 2px 8px; border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.langsel { padding: 4px 8px; border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-xs); }
.langsel:focus { outline: none; border-color: var(--azure); }
.chap { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid transparent; transition: background var(--t-fast), border-color var(--t-fast); }
.chap:hover { background: var(--surface); border-color: var(--line); }
.chap--4k { border-left: 2px solid var(--cyan); }
.chap--part { border-left: 2px solid var(--gold); }
.chap--src { border-left: 2px solid var(--violet); opacity: .85; }
.chap--md { border-left: 2px solid var(--coral); opacity: .85; }
.chap--src .chap__read, .chap--md .chap__read { cursor: default; }
.chap__read { flex: 1; display: flex; align-items: center; gap: var(--s-3); text-align: left; min-width: 0; }
.chap__num { font-family: var(--font-display); font-weight: 600; font-size: var(--fs-md); min-width: 48px; }
.chap__pages { font-size: var(--fs-xs); color: var(--ink-faint); }
.chap__tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 1px 6px; border-radius: var(--r-xs); }
.chap__tag--4k { color: var(--cyan); background: var(--cyan-glow); }
.chap__tag--part { color: var(--gold); background: color-mix(in srgb, var(--gold) 16%, transparent); }

.chap__actions { display: flex; align-items: center; gap: 4px; }
.ib { width: 32px; height: 30px; display: grid; place-items: center; border-radius: var(--r-xs); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast); }
.ib:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ib--accent:hover { color: var(--cyan); border-color: var(--cyan-glow); }
.ib--warn:hover { color: var(--gold); border-color: color-mix(in srgb, var(--gold) 40%, transparent); }
.ib--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.ib--read { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 35%, transparent); background: color-mix(in srgb, var(--jade) 10%, transparent); }
.chap__dlbtn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); background: transparent; transition: all var(--t-fast); }
.chap__dlbtn:hover:not(:disabled) { background: var(--azure-haze); color: #fff; }
.chap__dlbtn:disabled { opacity: .5; cursor: not-allowed; }
.chap__dlbtn--ghost { color: var(--ink-soft); border-color: var(--line-2); }
.chap__dlbtn--ghost:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.chap__srcmeta { font-size: var(--fs-2xs); color: var(--ink-ghost); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chap__dlprog { display: inline-flex; align-items: center; gap: var(--s-2); padding: 4px 10px; border-radius: var(--r-sm); background: var(--azure-haze); border: 1px solid var(--azure); }
.chap__dlprog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure-bright); min-width: 28px; }
.dl-ring { width: 16px; height: 16px; flex-shrink: 0; }
.dl-ring__track { fill: none; stroke: var(--surface-3); stroke-width: 3; }
.dl-ring__fill { fill: none; stroke: var(--azure); stroke-width: 3; stroke-linecap: round; stroke-dasharray: 56.5; transform: rotate(-90deg); transform-origin: 12px 12px; transition: stroke-dashoffset .4s var(--ease-silk); }

.chap__prog { display: flex; align-items: center; gap: var(--s-2); }
.chap__prog-bar { width: 80px; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.chap__prog-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.chap__prog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }
.cmpvar { width: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-top: 2px; border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line); }
.cmpvar__load { padding: var(--s-1); }
.cmpvar__lbl { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cmpvar__item { padding: 4px 10px; border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--violet); border: 1px solid color-mix(in srgb, var(--violet) 30%, transparent); }
.cmpvar__item em { font-style: normal; color: var(--ink-faint); }
.cmpvar__item:hover { background: color-mix(in srgb, var(--violet) 14%, transparent); }
.cmpvar__none { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cmpvar__none code { font-family: var(--font-mono); }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 540px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
