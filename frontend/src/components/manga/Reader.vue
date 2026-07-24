<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { useUiStore } from '@/stores/ui'
import { pageUrl } from '@/lib/manga'
import { imgProxy } from '@/lib/img'
import { coverRGB, vivid, rgbaCss } from '@/lib/coverColor'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { smoothBehavior } from '@/lib/motion'

const store = useMangaStore()
const ui = useUiStore()
const open = computed(() => !!store.reader)
const isManga = computed(() => store.reader?.kind !== 'cbz')
const isUpscaled = computed(() => store.reader?.source === 'upscaled')
const isRTL = computed(() => store.dir === 'rtl')

// Ancho de imagen a pedir al backend: al tamaño del viewport (nítido) en lectura
// normal, o full-res (w=0) al hacer zoom o en fit "original". El backend cachea el
// reescalado por mtime → decodificar ~2000px en vez de ~5760px elimina el lag del
// WebView2 al leer 4K (mismo contenido que en el navegador, que sí iba fluido).
const reqWidth = computed(() => {
  if (store.zoom > 1.01 || store.fit === 'original') return 0
  const dpr = Math.min(2, window.devicePixelRatio || 1)
  return Math.min(2560, Math.round((window.innerWidth || 1280) * dpr))
})

// Modo QA (testing): marcar la página actual como mal traducida. Solo sobre páginas locales.
const qaCanFlag = computed(() => store.qaMode && isManga.value && !['online', 'compare'].includes(store.reader?.source))
const qaOpen = ref(false)
const nightOpen = ref(false)   // popover de brillo/modo noche
const setOpen = ref(false)     // panel "Ajustes de lectura" (consolida los controles crípticos)
const qaNote = ref('')
const QA_REASONS = [
  { id: 'globo', label: 'Globo equivocado' },
  { id: 'desalineado', label: 'Desalineado' },
  { id: 'cortado', label: 'Texto cortado' },
  { id: 'emparejado', label: 'Página mal emparejada' },
  { id: 'no_traducido', label: 'No traducido' },
  { id: 'tapo_arte', label: 'Tapó arte' },
  { id: 'color', label: 'Página a color' },
  { id: 'otro', label: 'Otro' },
]
function qaSubmit(reason) {
  store.qaFlagPage(reason, qaNote.value.trim())
  qaNote.value = ''
  qaOpen.value = false
}

// ── chapter-end screen: al pasar de la última página aparece un cierre estilo streaming.
const endOpen = ref(false)
const webtoonEnd = ref(false)   // tarjeta de fin del webtoon: solo tras llegar al fondo del scroll
const nextChapterNum = computed(() => store.chapterListAsc[store.chapterIndex + 1]?.chapter)
const endCover = computed(() => imgProxy(store.reader?.cover || store.current?.cover || ''))

// Tinte de fondo del lector: color dominante de la portada (mismo muestreo que las tarjetas).
const rdTint = ref('')
watch(endCover, async (u) => {
  rdTint.value = ''
  if (!u) return
  const c = await coverRGB(u)
  if (c && endCover.value === u) rdTint.value = rgbaCss(vivid(c), 0.16)
}, { immediate: true })
function reReadChapter() { endOpen.value = false; store.setPage(0) }

// Page counter label — a range when showing a two-page spread.
const counterLabel = computed(() => {
  const total = store.pages.length
  const pair = store.spreadPair
  if (pair.length === 2) return `${pair[0] + 1}–${pair[1] + 1} / ${total}`
  return `${store.page + 1} / ${total}`
})

// ── neighbour preloading (zero flicker on page turn) ──
// We only track requested URLs (cheap strings); the Image objects are dropped to GC and
// the browser keeps the decoded bitmap in its HTTP cache, so turning a page is instant.
let preloaded = new Set()
function warm(i) {
  if (i < 0 || i >= store.pages.length) return
  const u = pageUrl(store.pages[i], reqWidth.value)
  if (preloaded.has(u)) return
  preloaded.add(u)
  const img = new Image(); img.src = u
}
function preloadNeighbors() {
  if (!store.pages.length) return
  const remote = store.reader?.source === 'online'
  // WEBTOON: la tira se lee bajando; `loading="lazy" decoding="async"` carga tarde y deja HUECOS en blanco al
  // bajar rápido. Precargamos una VENTANA por delante de la imagen central (store.page, que
  // onScroll mantiene) + un par atrás para volver sin recargar. Más margen si la fuente es remota.
  if (store.mode === 'webtoon') {
    const ahead = remote ? 8 : 5
    warm(store.page - 1)
    for (let n = 0; n <= ahead; n++) warm(store.page + n)
    return
  }
  // PAGINADO: solo hacen falta los vecinos inmediatos (salto instantáneo al pasar página).
  const step = store.spreadActive ? 2 : 1
  const ahead = remote ? 3 : 2
  warm(store.page - step)
  for (let n = 1; n <= ahead; n++) warm(store.page + step * n)
}
// ── next-chapter prefetch (instant chapter jump) ──
// Al acercarse al final, pide la lista de páginas del capítulo siguiente (barato, solo local) y
// calienta sus PRIMERAS imágenes, de modo que pulsar "Siguiente" no muestre ni carga ni parpadeo.
// Es idempotente: prefetchNextChapter() cachea, así que llamarlo en cada avance no repite trabajo.
let warmedNextFor = null   // clave del capítulo cuyas imágenes ya calentamos (evita recalentar)
async function maybePrefetchNextChapter() {
  if (!isManga.value || !store.canNextChapter) return
  const near = store.mode === 'webtoon' || store.page >= store.pages.length - 3
  if (!near) return
  const pages = await store.prefetchNextChapter()
  if (!pages || !pages.length) return
  const key = store.chapterListAsc[store.chapterIndex + 1]?.chapter
  if (warmedNextFor === key) return
  warmedNextFor = key
  for (let i = 0; i < Math.min(3, pages.length); i++) {
    const img = new Image(); img.src = pageUrl(pages[i], reqWidth.value)
  }
}
// New chapter/file → reset the cache-key set, then warm the neighbours.
watch(() => store.pages, () => { preloaded = new Set(); endOpen.value = false; webtoonEnd.value = false; warmedNextFor = null; preloadNeighbors() })
watch(() => store.page, () => { preloadNeighbors(); maybePrefetchNextChapter(); centerThumb() })

// La tira de miniaturas no seguía a la lectura: pasabas de página y el recuadro activo se
// quedaba fuera de la vista. `nearest` (no `center`) para no zarandear la tira cuando la
// miniatura ya se ve.
const thumbsEl = ref(null)
function centerThumb() {
  nextTick(() => {
    const el = thumbsEl.value?.querySelector(`[data-page="${store.page}"]`)
    el?.scrollIntoView({ behavior: smoothBehavior(), block: 'nearest', inline: 'nearest' })
  })
}
watch(() => store.mode, () => { endOpen.value = false })

// Advance: on the last page, open the chapter-end screen instead of a dead click (paged
// manga only — webtoon shows its end card inline, cbz has no chapter concept).
function tryNext() {
  if (isManga.value && store.mode === 'paged' && store.page >= store.pages.length - 1) { endOpen.value = true; return }
  store.nextPage()
}
function goPrev() { endOpen.value = false; store.prevPage() }

const wrap = ref(null)
let barsTimer = null

// ── transform for paged/compare layers ──
const transform = computed(() => `translate(${store.panX}px, ${store.panY}px) scale(${store.zoom})`)

// ── drag-to-pan + click navigation ──
let drag = { active: false, moved: false, ox: 0, oy: 0, px: 0, py: 0 }
function onDown(e) {
  // En modo comparar permitimos paneo SOLO con zoom (para inspeccionar el detalle); sin
  // zoom no registramos arrastre para no estorbar al divisor/etiquetas. (El divisor usa
  // @mousedown.stop, así que agarrarlo no dispara este handler.)
  if (store.compareMode && store.zoom <= 1.01) return
  // Always register the press so a plain click navigates; panning only kicks in when zoomed.
  drag = { active: true, moved: false, ox: e.clientX, oy: e.clientY, px: store.panX, py: store.panY }
}
function onMove(e) {
  // Pan only while dragging AND zoomed in — never on a plain cursor move. Sí se permite en
  // modo comparar cuando hay zoom (ambas capas comparten el mismo transform → se mueven juntas).
  if (!drag.active || store.zoom <= 1.01) return
  const dx = e.clientX - drag.ox, dy = e.clientY - drag.oy
  if (Math.abs(dx) > 4 || Math.abs(dy) > 4) drag.moved = true
  if (drag.moved) {
    const mx = (store.zoom - 1) * window.innerWidth * 0.6
    const my = (store.zoom - 1) * window.innerHeight * 0.6
    store.panX = Math.max(-mx, Math.min(mx, drag.px + dx))
    store.panY = Math.max(-my, Math.min(my, drag.py + dy))
  }
}
function onUp(e) {
  const active = drag.active
  const wasDrag = drag.active && drag.moved
  drag.active = false
  // Navigate only on a genuine click (mouse was pressed here, no drag, not compare/zoom)
  if (!active || wasDrag || store.mode !== 'paged' || store.compareMode || store.zoom > 1.01) return
  const x = e.clientX / window.innerWidth
  const goNext = isRTL.value ? x < 0.5 : x > 0.5
  goNext ? tryNext() : goPrev()
}
// Leaving the area must NOT navigate — only cancel an in-progress drag.
function endDrag() { drag.active = false }

// Click handler on the page image itself — this is the element that receives mouse events.
function pageClick(e) {
  if (setOpen.value) { setOpen.value = false; return }
  if (nightOpen.value) { nightOpen.value = false; return }
  if (store.mode !== 'paged' || store.compareMode || store.zoom > 1.01) return
  if (drag.moved) { drag.moved = false; return }
  const x = e.clientX / window.innerWidth
  const goNext = isRTL.value ? x < 0.5 : x > 0.5
  goNext ? tryNext() : goPrev()
}
function onWheel(e) {
  if (store.mode === 'webtoon' && !(e.ctrlKey || e.metaKey)) return
  e.preventDefault()
  store.zoomBy(e.deltaY < 0 ? 0.15 : -0.15)
}

// ── compare divider drag ──
let cmpDrag = false
function cmpStart(e) { cmpDrag = true; e.stopPropagation(); e.preventDefault(); window.addEventListener('mousemove', cmpMove); window.addEventListener('mouseup', cmpEnd) }
function cmpMove(e) {
  if (!cmpDrag || !wrap.value) return
  const r = wrap.value.getBoundingClientRect()
  store.compareX = Math.max(3, Math.min(97, ((e.clientX - r.left) / r.width) * 100))
}
function cmpEnd() { cmpDrag = false; window.removeEventListener('mousemove', cmpMove); window.removeEventListener('mouseup', cmpEnd) }

// ── webtoon scroll tracking ──
function onScroll(e) {
  const c = e.target
  const imgs = c.querySelectorAll('.rd__wimg')
  const mid = c.scrollTop + c.clientHeight / 2
  let best = 0, bd = Infinity
  imgs.forEach((img, i) => { const d = Math.abs(img.offsetTop + img.offsetHeight / 2 - mid); if (d < bd) { bd = d; best = i } })
  if (best !== store.page) store.setPage(best)
  // En webtoon la ÚLTIMA página (a menudo corta) puede no ser NUNCA la "central", así que
  // setPage(pages.length-1) no llega a dispararse y el capítulo no se marcaba leído — afectaba
  // sobre todo a manhwas leídos ONLINE (no descargados). Marcamos al llegar al FONDO del scroll.
  const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 48
  if (isManga.value && atBottom) store.markRead(store.reader?.chapter)
  // La tarjeta de fin solo aparece al ALCANZAR el fondo de verdad (no al abrir, cuando las
  // imágenes lazy aún no tienen altura y el fondo del scroll queda arriba).
  if (atBottom) webtoonEnd.value = true
  // Cerca del fondo (último 20%): calienta el siguiente capítulo para un salto instantáneo.
  if (c.scrollTop + c.clientHeight >= c.scrollHeight * 0.8) maybePrefetchNextChapter()
}

// ── auto-hide bars ──
function poke() {
  store.barsHidden = false
  clearTimeout(barsTimer)
  if (store.mode === 'paged') barsTimer = setTimeout(() => { store.barsHidden = true }, 1600)
}

// Pantalla completa por el store unificado: en la shell nativa entra en fullscreen
// real de ventana (Rust), en navegador usa la API DOM. Al cerrar el capítulo,
// closeReader() del store llama setFullscreen(false) → nunca se queda atascado.
function toggleFullscreen() { ui.toggleFullscreen() }

// ── keyboard ──
function onKey(e) {
  if (!open.value) return
  switch (e.key) {
    case 'Escape': setOpen.value ? (setOpen.value = false) : nightOpen.value ? (nightOpen.value = false) : endOpen.value ? (endOpen.value = false) : store.closeReader(); break
    case 'ArrowRight': e.preventDefault(); isRTL.value ? goPrev() : tryNext(); break
    case 'ArrowLeft': e.preventDefault(); isRTL.value ? tryNext() : goPrev(); break
    case ' ': e.preventDefault(); tryNext(); break
    case 'f': store.cycleFit(); break
    case 'w': store.setMode(store.mode === 'paged' ? 'webtoon' : 'paged'); break
    case 's': if (store.mode === 'paged') store.toggleSpread(); break
    case 'd': if (isManga.value) store.toggleDir(); break
    case 'c': if (isManga.value) store.toggleCompare(); break
    case ']': if (isManga.value) store.goNextChapter(); break
    case '[': if (isManga.value) store.goPrevChapter(); break
  }
}
onMounted(() => { window.addEventListener('keydown', onKey) })
onUnmounted(() => { window.removeEventListener('keydown', onKey); clearTimeout(barsTimer); cmpEnd() })
</script>

<template>
  <Teleport to="body">
    <Transition name="reader">
      <div v-if="open" class="rd" :style="{ '--rd-tint': rdTint }" @mousemove="poke">
        <!-- Top bar -->
        <header class="rd__bar" :class="{ 'is-hidden': store.barsHidden }">
          <button class="rd__btn" @click="store.closeReader()"><Icon name="chevron" :size="18" :style="{ transform: 'rotate(180deg)' }" /></button>
          <div class="rd__meta">
            <span class="rd__title">{{ isManga ? store.current?.name : store.reader.title }}</span>
            <span v-if="store.reader.chapter" class="rd__ch">{{ isManga ? 'Cap. ' : '' }}{{ store.reader.chapter }}</span>
            <span v-if="isManga && store.reader.source !== 'compare'" class="rd__src" :class="{ 'is-4k': isUpscaled }">{{ isUpscaled ? '4K' : 'ORIG' }}</span>
          </div>
          <div class="rd__tools">
            <!-- Marcar leído (acción frecuente → directa) -->
            <button v-if="isManga" class="rd__btn" :class="{ 'is-on': store.isChapterRead(store.reader.chapter) }" title="Marcar capítulo leído" @click="store.toggleChapterRead(store.reader.chapter)"><Icon name="check" :size="16" /></button>
            <!-- Comparar original / 4K -->
            <button v-if="store.canCompare" class="rd__btn" :class="{ 'is-on': store.compareMode }" title="Comparar original / 4K (C)" @click="store.toggleCompare()"><Icon name="grid" :size="15" /></button>
            <!-- QA (solo modo testing) -->
            <button v-if="qaCanFlag" class="rd__btn rd__btn--qa" :class="{ 'is-on': qaOpen }" title="Marcar página mal traducida (QA)" @click="qaOpen = !qaOpen"><span class="rd__txt">⚑</span></button>
            <!-- Ajustes de lectura: consolida ajuste/modo/dirección/doble-página/brillo/zoom con ETIQUETAS -->
            <div class="rd__setwrap">
              <button class="rd__btn" :class="{ 'is-on': setOpen }" title="Ajustes de lectura" @click.stop="setOpen = !setOpen; nightOpen = false"><Icon name="settings" :size="16" /></button>
              <div v-if="setOpen" class="rd__set" @click.stop>
                <!-- Modo -->
                <div class="rd__set-group">
                  <span class="rd__set-lbl">Modo de lectura</span>
                  <div class="rd__seg">
                    <button :class="{ 'is-on': store.mode === 'paged' }" @click="store.setMode('paged')"><Icon name="book" :size="15" /> Páginas</button>
                    <button :class="{ 'is-on': store.mode === 'webtoon' }" @click="store.setMode('webtoon')"><Icon name="scroll" :size="15" /> Webtoon</button>
                  </div>
                </div>
                <!-- Ajuste de página -->
                <div class="rd__set-group">
                  <span class="rd__set-lbl">Ajustar imagen</span>
                  <div class="rd__seg">
                    <button :class="{ 'is-on': store.fit === 'width' }" @click="store.setFit ? store.setFit('width') : store.cycleFit()">Ancho</button>
                    <button :class="{ 'is-on': store.fit === 'height' }" @click="store.setFit ? store.setFit('height') : store.cycleFit()">Alto</button>
                    <button :class="{ 'is-on': store.fit === 'original' }" @click="store.setFit ? store.setFit('original') : store.cycleFit()">Original</button>
                  </div>
                </div>
                <!-- Opciones de modo paginado -->
                <template v-if="store.mode === 'paged'">
                  <div class="rd__set-group" v-if="isManga">
                    <span class="rd__set-lbl">Dirección</span>
                    <div class="rd__seg">
                      <button :class="{ 'is-on': store.dir === 'ltr' }" @click="store.dir !== 'ltr' && store.toggleDir()">Izq → Der</button>
                      <button :class="{ 'is-on': store.dir === 'rtl' }" @click="store.dir !== 'rtl' && store.toggleDir()">Der ← Izq</button>
                    </div>
                  </div>
                  <button class="rd__set-toggle" :class="{ 'is-on': store.spread }" @click="store.toggleSpread()">
                    <Icon name="book-open" :size="16" /> <span>Doble página</span>
                    <span class="rd__set-state">{{ store.spread ? 'Activada' : 'Desactivada' }}</span>
                  </button>
                </template>
                <!-- Brillo + modo noche -->
                <div class="rd__set-group">
                  <span class="rd__set-lbl"><Icon name="sun" :size="13" /> Brillo</span>
                  <div class="rd__set-slider">
                    <input type="range" min="0.4" max="1" step="0.05" :value="store.readerBrightness"
                           @input="store.setBrightness(Number($event.target.value))" />
                    <span class="rd__set-val">{{ Math.round(store.readerBrightness * 100) }}%</span>
                  </div>
                </div>
                <button class="rd__set-toggle" :class="{ 'is-on': store.readerInvert }" @click="store.toggleInvert()">
                  <Icon name="palette" :size="15" /> <span>Invertir (leer en oscuridad)</span>
                  <span class="rd__set-state">{{ store.readerInvert ? 'Activado' : 'Desactivado' }}</span>
                </button>
                <!-- Zoom -->
                <button v-if="store.zoom > 1.01" class="rd__set-toggle" @click="store.resetZoom()">
                  <Icon name="search" :size="15" /> <span>Restablecer zoom</span>
                  <span class="rd__set-state">{{ Math.round(store.zoom * 100) }}%</span>
                </button>
              </div>
            </div>
            <!-- Pantalla completa (acción frecuente → directa) -->
            <button class="rd__btn" :class="{ 'is-on': ui.fullscreen }" :title="ui.fullscreen ? 'Salir de pantalla completa (F11)' : 'Pantalla completa (F11)'" @click="toggleFullscreen"><Icon :name="ui.fullscreen ? 'collapse' : 'expand'" :size="16" /></button>
          </div>

          <!-- QA: picker de motivo para la página actual -->
          <div v-if="qaCanFlag && qaOpen" class="rd__qa" :class="{ 'is-hidden': store.barsHidden }" @click.stop>
            <div class="rd__qa-head">Marcar <b>pág. {{ store.page + 1 }}</b> como mal traducida</div>
            <div class="rd__qa-reasons">
              <button v-for="r in QA_REASONS" :key="r.id" class="rd__qa-chip" @click="qaSubmit(r.id)">{{ r.label }}</button>
            </div>
            <input v-model="qaNote" class="rd__qa-note" type="text" placeholder="Nota opcional (qué falla)…" @keydown.enter="qaSubmit('otro')" />
          </div>
        </header>

        <div v-if="store.readerLoading" class="rd__center"><Spinner :size="32" /></div>
        <div v-else-if="!store.pages.length" class="rd__center rd__empty">Sin páginas.</div>

        <!-- Paged -->
        <div v-else-if="store.mode === 'paged'" class="rd__paged"
             :class="{ 'is-grab': store.zoom > 1.01, 'is-cmp': store.compareMode }"
             @mousedown="onDown" @mousemove="onMove" @mouseup="onUp" @mouseleave="endDrag" @wheel="onWheel">
          <!-- chapter ghost zones -->
          <button v-if="isManga && store.canPrevChapter" class="rd__ghost rd__ghost--prev" @click.stop="store.goPrevChapter()"><span>‹ Cap. {{ store.chapterListAsc[store.chapterIndex - 1]?.chapter }}</span></button>
          <button v-if="isManga && store.canNextChapter" class="rd__ghost rd__ghost--next" @click.stop="store.goNextChapter()"><span>Cap. {{ store.chapterListAsc[store.chapterIndex + 1]?.chapter }} ›</span></button>

          <!-- click-zone hints (visible on hover) -->
          <div class="rd__zones">
            <span class="rd__zone rd__zone--prev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg></span>
            <span class="rd__zone rd__zone--next"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg></span>
          </div>

          <!-- compare overlay: both images fill the same container so they render at identical
               CSS dimensions regardless of their natural pixel counts (1440 vs 5760). -->
          <div v-if="store.compareMode" ref="wrap" class="rd__cmp" :class="`fit-${store.fit}`" :style="{ transform }">
            <img :src="store.pageUpUrl" class="rd__cmp-img" referrerpolicy="no-referrer" alt="" />
            <img :src="store.pageOrigUrl" class="rd__cmp-img rd__cmp-orig" referrerpolicy="no-referrer"
                 :style="{ clipPath: `inset(0 ${100 - store.compareX}% 0 0)` }" alt="" />
            <div class="rd__divider" :style="{ left: store.compareX + '%' }" @mousedown.stop="cmpStart"><span class="rd__handle">⟷</span></div>
            <span class="rd__clabel rd__clabel--l">{{ store.compareLabels?.left || (store.scanCompareMode ? 'PRINCIPAL' : 'ORIGINAL') }}</span>
            <span class="rd__clabel rd__clabel--r">{{ store.compareLabels?.right || (store.scanCompareMode ? 'VARIANTE' : '4K') }}</span>
          </div>
          <!-- two-page spread -->
          <div v-else-if="store.spreadActive && store.spreadPair.length === 2" :key="'sp' + store.page"
               class="rd__spread rd__fade" :class="[`fit-${store.fit}`, { 'is-rtl': isRTL }]" :style="{ transform, filter: store.pageFilter }"
               @click.stop.prevent="pageClick">
            <img :src="pageUrl(store.pages[store.spreadPair[0]], reqWidth)" class="rd__simg" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.spreadPair[0] + 1}`" />
            <img :src="pageUrl(store.pages[store.spreadPair[1]], reqWidth)" class="rd__simg" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.spreadPair[1] + 1}`" />
          </div>
          <!-- single page -->
          <img v-else :key="'pg' + store.page" :src="pageUrl(store.pages[store.page], reqWidth)" class="rd__img rd__fade" :class="`fit-${store.fit}`" :style="{ transform, filter: store.pageFilter }" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.page + 1}`"
               @click.stop.prevent="pageClick" />

          <!-- Contador SIEMPRE visible (antes se ocultaba con las barras y "desaparecía" a los 1.6s).
               Cuando la barra inferior (miniaturas/progreso) está visible, sube para no taparla. -->
          <div class="rd__counter" :class="{ 'rd__counter--up': !store.barsHidden }">{{ counterLabel }}</div>
        </div>

        <!-- Webtoon -->
        <div v-else class="rd__webtoon" @scroll="onScroll">
          <!-- Las primeras imágenes NO son lazy: cargan ya para que abrir el capítulo muestre la
               página de inmediato (antes, con todas lazy y sin altura, la tarjeta de fin quedaba
               arriba y parecía "capítulo completado" al abrir). -->
          <img v-for="(p, i) in store.pages" :key="i" :src="pageUrl(p, reqWidth)" :loading="i < 3 ? 'eager' : 'lazy'" :fetchpriority="i === 0 ? 'high' : 'auto'" decoding="async" class="rd__wimg" :style="{ maxWidth: store.fit === 'width' ? '900px' : 'none', transform: `scale(${store.zoom})`, filter: store.pageFilter }" :alt="`Página ${i + 1}`" />
          <!-- Tarjeta de fin: SOLO tras llegar al fondo (webtoonEnd), no al abrir. -->
          <div v-if="isManga && store.pages.length && webtoonEnd" class="rd__wend">
            <span class="rd__end-check"><Icon name="check" :size="20" /></span>
            <p class="rd__end-eyebrow">CAPÍTULO COMPLETADO</p>
            <h2 class="rd__end-title">Cap. {{ store.reader.chapter }}</h2>
            <button v-if="store.canNextChapter" class="rd__end-primary" @click="store.goNextChapter()">
              <Icon name="play" :size="17" /> Siguiente · Cap. {{ nextChapterNum }}
            </button>
            <p v-else class="rd__end-done">Has llegado al último capítulo disponible.</p>
            <div class="rd__end-sub">
              <button class="rd__end-sbtn" @click="reReadChapter"><Icon name="clock" :size="14" /> Releer</button>
              <button class="rd__end-sbtn" @click="store.closeReader()"><Icon name="library" :size="14" /> Biblioteca</button>
            </div>
          </div>
        </div>
        <!-- Contador de página (webtoon no tiene barra inferior): fuera del contenedor scrollable
             para que no se desplace; siempre visible, discreto. onScroll fija store.page a la
             imagen central, así el número refleja dónde estás en la tira. -->
        <div v-if="store.mode === 'webtoon' && store.pages.length" class="rd__wcount">{{ store.page + 1 }} <span>/ {{ store.pages.length }}</span></div>

        <!-- Chapter-end takeover (paged manga) — streaming-style "next episode" card -->
        <Transition name="rd-end">
          <div v-if="endOpen && isManga && store.mode === 'paged'" class="rd__end">
            <div class="rd__end-bg" :style="endCover ? { backgroundImage: `url('${endCover}')` } : {}" />
            <div class="rd__end-shade" />
            <div class="rd__end-card">
              <span class="rd__end-check"><Icon name="check" :size="24" /></span>
              <p class="rd__end-eyebrow">CAPÍTULO COMPLETADO</p>
              <h2 class="rd__end-title">Cap. {{ store.reader.chapter }}</h2>
              <div class="rd__end-stats">
                <span>{{ store.pages.length }} páginas</span>
                <span class="rd__end-dot" />
                <span>{{ isUpscaled ? '4K' : 'Original' }}</span>
              </div>
              <div class="rd__end-btns">
                <button v-if="store.canNextChapter" class="rd__end-primary" @click="store.goNextChapter()">
                  <Icon name="play" :size="17" /> Siguiente capítulo · Cap. {{ nextChapterNum }}
                </button>
                <p v-else class="rd__end-done">Has llegado al último capítulo disponible.</p>
                <div class="rd__end-sub">
                  <button class="rd__end-sbtn" @click="reReadChapter"><Icon name="clock" :size="14" /> Releer</button>
                  <button class="rd__end-sbtn" @click="store.closeReader()"><Icon name="library" :size="14" /> Biblioteca</button>
                </div>
              </div>
            </div>
          </div>
        </Transition>

        <!-- Bottom bar (paged) -->
        <footer v-if="store.mode === 'paged' && store.pages.length" class="rd__bottom" :class="{ 'is-hidden': store.barsHidden }">
          <div class="rd__chnav">
            <button class="rd__chbtn" :disabled="!isManga || !store.canPrevChapter" @click="store.goPrevChapter()">‹ Cap.</button>
            <input class="rd__scrub" type="range" min="0" :max="store.pages.length - 1" :value="store.page"
                   :style="{ direction: isRTL ? 'rtl' : 'ltr' }" @input="store.setPage(Number($event.target.value))" />
            <button class="rd__chbtn" :disabled="!isManga || !store.canNextChapter" @click="store.goNextChapter()">Cap. ›</button>
          </div>
          <div class="rd__thumbs" ref="thumbsEl">
            <button v-for="(p, i) in store.pages" :key="i" class="rd__thumb" :class="{ 'is-active': i === store.page }"
                    :data-page="i" @click="store.setPage(i)">
              <img :src="pageUrl(p, 120)" loading="lazy" decoding="async" alt="" />
            </button>
          </div>
        </footer>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Fondo teñido con el color de la portada en vez del negro plano: abrir un capítulo deja de
   ser un fogonazo negro y da continuidad entre la tarjeta y la lectura. Decorativo y muy
   tenue — el arte de la página sigue sobre negro, que es lo que pide un manga B/N. */
.rd { position: fixed; inset: 0; z-index: 110; display: flex; flex-direction: column;
  background: radial-gradient(120% 90% at 50% 0%, var(--rd-tint, transparent) 0%, transparent 62%), #05070d;
  transition: background 1.2s var(--ease-silk); }

.rd__bar { position: absolute; top: 0; left: 0; right: 0; z-index: 6; display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-4); height: 3.25rem;
  background: linear-gradient(180deg, rgba(7,10,18,.95), rgba(7,10,18,.5)); backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); transition: transform var(--t-base) var(--ease-silk); }
.rd__bar.is-hidden { transform: translateY(-100%); }
.rd__btn { min-width: 2.25rem; height: 2.25rem; padding: 0 0.5rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__btn:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface); }
.rd__btn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.rd__txt, .rd__zoom { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; }
.rd__meta { display: flex; align-items: center; gap: var(--s-3); flex: 1; min-width: 0; }
.rd__title { font-weight: 600; font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rd__ch { color: var(--ink-faint); font-size: var(--fs-xs); white-space: nowrap; }
.rd__src { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 0.4375rem; border-radius: var(--r-pill); color: var(--ink-faint); background: var(--surface); }
.rd__src.is-4k { color: var(--cyan); background: var(--cyan-glow); }
.rd__tools { display: flex; gap: var(--s-1); }
.rd__btn--qa.is-on { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 50%, transparent); background: color-mix(in srgb, var(--coral) 12%, transparent); }

/* QA: popover de marcado (testing) */
.rd__qa { position: absolute; top: 3.5rem; right: var(--s-4); z-index: 7; width: min(22rem, 92vw);
  padding: var(--s-3); border-radius: var(--r-md); background: var(--glass-strong); backdrop-filter: blur(16px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-lg); transition: opacity var(--t-fast); }
.rd__qa.is-hidden { opacity: 0; pointer-events: none; }
.rd__qa-head { font-size: var(--fs-xs); color: var(--ink-soft); margin-bottom: var(--s-2); }
.rd__qa-head b { color: var(--ink); }
.rd__qa-reasons { display: flex; flex-wrap: wrap; gap: var(--s-1); margin-bottom: var(--s-2); }
.rd__qa-chip { font-size: var(--fs-2xs); font-weight: 600; padding: 0.3125rem 0.625rem; border-radius: var(--r-pill);
  color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__qa-chip:hover { color: #fff; background: var(--coral); border-color: transparent; }
.rd__qa-note { width: 100%; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); font-size: var(--fs-xs); outline: none; }
.rd__qa-note:focus { border-color: var(--azure); }
@media (prefers-reduced-motion: reduce) { .rd__qa { transition: none; } }

.rd__center { flex: 1; display: grid; place-items: center; }
.rd__empty { color: var(--ink-faint); }

.rd__paged { flex: 1; display: grid; place-items: center; overflow: hidden; position: relative; cursor: pointer; }
.rd__paged.is-grab { cursor: grab; }
.rd__zones { position: absolute; inset: 0; pointer-events: none; z-index: 3; opacity: 0; transition: opacity .12s ease; }
.rd__paged:hover .rd__zones { opacity: .35; }
.rd__paged.is-cmp .rd__zones { display: none; }
.rd__zone { position: absolute; top: 50%; transform: translateY(-50%); color: #fff; filter: drop-shadow(0 1px 4px rgba(0,0,0,.7)); }
.rd__zone--prev { left: var(--s-5); }
.rd__zone--next { right: var(--s-5); }
.rd__img { display: block; user-select: none; }
/* Modo noche — popover de brillo/inversión */
.rd__night { position: relative; }
.rd__night-pop {
  position: absolute; top: calc(100% + 0.5rem); right: 0; z-index: 20; width: 15rem;
  display: flex; flex-direction: column; gap: var(--s-2);
  padding: var(--s-3); border-radius: var(--r-md);
  background: var(--glass-strong); backdrop-filter: blur(16px);
  border: 1px solid var(--line); box-shadow: var(--shadow-lg);
}
.rd__night-row { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-soft); }
.rd__night-row input[type=range] { flex: 1; accent-color: var(--azure); }
.rd__night-val { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink); min-width: 2.6rem; text-align: right; }
.rd__night-inv {
  padding: 0.375rem 0.625rem; border-radius: var(--r-sm); cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--ink-soft);
  font-size: var(--fs-2xs); font-weight: 600; transition: all var(--t-fast);
}
.rd__night-inv:hover { border-color: var(--azure); color: var(--ink); }
.rd__night-inv.is-on { background: var(--azure); border-color: var(--azure); color: #fff; }

/* ── Panel "Ajustes de lectura" (consolida los controles antes crípticos, con etiquetas) ── */
.rd__setwrap { position: relative; }
.rd__set {
  position: absolute; top: calc(100% + 0.5rem); right: 0; z-index: 20; width: 17rem;
  display: flex; flex-direction: column; gap: var(--s-3);
  padding: var(--s-3); border-radius: var(--r-md);
  background: var(--glass-strong); backdrop-filter: blur(16px);
  border: 1px solid var(--line); box-shadow: var(--shadow-lg);
}
.rd__set-group { display: flex; flex-direction: column; gap: 0.375rem; }
.rd__set-lbl { display: flex; align-items: center; gap: 0.3125rem; font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .03em; text-transform: uppercase; color: var(--ink-ghost); }
/* Control segmentado: opciones lado a lado, la activa resaltada (claro qué está puesto). */
.rd__seg { display: flex; gap: 4px; background: var(--surface-2); border-radius: var(--r-sm); padding: 3px; }
.rd__seg button { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 0.3125rem; padding: 0.375rem 4px; border-radius: calc(var(--r-sm) - 2px); font-size: var(--fs-2xs); font-weight: 600; color: var(--ink-soft); transition: all var(--t-fast); }
.rd__seg button:hover { color: var(--ink); }
.rd__seg button.is-on { background: var(--azure); color: #fff; box-shadow: var(--shadow-sm); }
/* Toggle de línea con estado a la derecha (nada de glifos que adivinar). */
.rd__set-toggle { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.625rem; border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-soft); font-size: var(--fs-xs); font-weight: 600; transition: all var(--t-fast); }
.rd__set-toggle span:first-of-type { flex: 1; text-align: left; }
.rd__set-toggle:hover { border-color: var(--azure); color: var(--ink); }
.rd__set-toggle.is-on { border-color: var(--azure); color: var(--ink); }
.rd__set-state { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-ghost); }
.rd__set-toggle.is-on .rd__set-state { color: var(--azure-bright); }
.rd__set-slider { display: flex; align-items: center; gap: var(--s-2); }
.rd__set-slider input[type=range] { flex: 1; accent-color: var(--azure); }
.rd__set-val { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink); min-width: 2.6rem; text-align: right; }
/* will-change SOLO al hacer zoom/paneo (is-grab). Dejarlo siempre forzaba a
   WebView2 (composición/DComp transparente) a mantener una textura GPU del tamaño
   NATIVO de la página 4K (~5760px) aunque se muestre a ~1000px → lag brutal solo
   en la shell nativa (el navegador normal usa otro compositor y no se nota). */
.rd__paged.is-grab .rd__img,
.rd__paged.is-grab .rd__spread,
.rd__paged.is-grab .rd__cmp { will-change: transform; }
.rd__img.fit-width { width: 100%; max-width: 62.5rem; height: auto; }
.rd__img.fit-height { height: 100vh; width: auto; }
.rd__img.fit-original { width: auto; height: auto; max-width: none; }

/* two-page spread: both pages share one container; RTL flips reading order */
.rd__spread { display: flex; align-items: flex-start; }
.rd__spread.is-rtl { flex-direction: row-reverse; }
.rd__spread.fit-width { width: 100%; max-width: 112.5rem; }
.rd__spread.fit-width .rd__simg { width: 50%; height: auto; }
.rd__spread.fit-height { height: 100vh; }
.rd__spread.fit-height .rd__simg { height: 100vh; width: auto; }
.rd__spread.fit-original .rd__simg { width: auto; height: auto; }
.rd__simg { display: block; user-select: none; }

/* soft fade-in on page turn (preloaded image is already cached → no white flash) */
.rd__fade { animation: pageFade .18s var(--ease-silk); }
@keyframes pageFade { from { opacity: .4 } to { opacity: 1 } }

.rd__ghost { position: absolute; top: 0; bottom: 0; width: 5rem; z-index: 4; display: flex; align-items: center; opacity: 0; transition: opacity var(--t-fast); border: none; background: linear-gradient(90deg, rgba(77,141,255,.18), transparent); }
.rd__ghost--prev { left: 0; justify-content: flex-start; padding-left: var(--s-3); }
.rd__ghost--next { right: 0; justify-content: flex-end; padding-right: var(--s-3); background: linear-gradient(270deg, rgba(77,141,255,.18), transparent); }
.rd__paged:hover .rd__ghost { opacity: 1; }
.rd__ghost span { font-size: var(--fs-xs); color: var(--ice); font-weight: 600; text-shadow: 0 1px 4px #000; }

.rd__cmp { position: relative; }
/* Fit behaviour goes on the wrapper; both images fill it at identical dimensions. */
.rd__cmp.fit-width  { width: 100%; max-width: 62.5rem; }
.rd__cmp.fit-height { height: 100vh; }
.rd__cmp.fit-original { width: auto; height: auto; }
.rd__cmp-img { display: block; width: 100%; height: auto; }
.rd__cmp.fit-height .rd__cmp-img { width: auto; height: 100%; }
/* La capa superpuesta (A) se ESTIRA a la MISMA caja que la base (B) — no se recorta —
   para que un punto de pantalla sea el MISMO contenido en ambos lados. Con `cover` se
   recortaba/centraba y, al diferir las dimensiones de cada scan, el divisor revelaba zonas
   desalineadas (parecía que la imagen "se movía"). Son la misma página (emparejada por
   dHash), así que el estiramiento por la pequeña diferencia de aspecto es imperceptible. */
.rd__cmp-orig { position: absolute; inset: 0; width: 100% !important; height: 100% !important; object-fit: fill; }
/* 24px-wide invisible grab zone centred on a 2px visible line — much easier to drag */
.rd__divider { position: absolute; top: 0; bottom: 0; width: 1.5rem; transform: translateX(-50%); cursor: col-resize; z-index: 5; display: grid; place-items: center; }
.rd__divider::before { content: ''; position: absolute; top: 0; bottom: 0; width: 2px; background: var(--azure); box-shadow: 0 0 12px var(--azure-glow); }
.rd__handle { position: relative; width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: 50%; background: var(--azure); color: #fff; font-size: 0.875rem; box-shadow: var(--glow-azure); }
.rd__clabel { position: absolute; top: var(--s-3); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 0.5rem; border-radius: var(--r-xs); background: rgba(7,10,18,.7); }
.rd__clabel--l { left: var(--s-3); color: var(--ink-soft); }
.rd__clabel--r { right: var(--s-3); color: var(--cyan); }

.rd__counter { position: absolute; bottom: var(--s-4); left: 50%; transform: translateX(-50%); z-index: 8; font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink); padding: 4px 0.75rem; border-radius: var(--r-pill); background: rgba(7,10,18,.72); backdrop-filter: blur(6px); transition: bottom var(--t-base) var(--ease-silk); pointer-events: none; }
/* Sube por encima de la barra inferior (miniaturas + scrub) cuando está desplegada. */
.rd__counter--up { bottom: 6.5rem; }
.rd__counter.is-hidden { opacity: 0; }
/* Contador webtoon: fijo abajo-derecha, no estorba la lectura de la tira. */
.rd__wcount { position: fixed; bottom: var(--s-4); right: var(--s-4); z-index: 7; font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink); padding: 0.3125rem 0.75rem; border-radius: var(--r-pill); background: rgba(7,10,18,.72); backdrop-filter: blur(6px); pointer-events: none; }
.rd__wcount span { color: var(--ink-ghost); }

.rd__webtoon { flex: 1; overflow-y: auto; display: flex; flex-direction: column; align-items: center; padding-top: 3.25rem; }
.rd__wimg { width: 100%; height: auto; display: block; }

.rd__bottom { position: absolute; bottom: 0; left: 0; right: 0; z-index: 6; padding: var(--s-3) var(--s-4); background: linear-gradient(0deg, rgba(7,10,18,.96), transparent); transition: transform var(--t-base) var(--ease-silk); }
.rd__bottom.is-hidden { transform: translateY(100%); }
.rd__chnav { display: flex; align-items: center; gap: var(--s-3); }
.rd__chbtn { padding: 0.375rem 0.75rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__chbtn:hover:not(:disabled) { color: var(--ink); border-color: var(--azure); }
.rd__chbtn:disabled { opacity: .35; }
.rd__scrub { flex: 1; accent-color: var(--azure); }
.rd__thumbs { display: flex; gap: var(--s-2); overflow-x: auto; padding-top: var(--s-2); }
.rd__thumb { flex-shrink: 0; width: 2.75rem; height: 3.875rem; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; opacity: .5; transition: all var(--t-fast); }
.rd__thumb img { width: 100%; height: 100%; object-fit: cover; }
.rd__thumb:hover { opacity: .85; }
.rd__thumb.is-active { opacity: 1; border-color: var(--azure); }

/* ── Chapter-end screen (paged takeover + webtoon inline card) ─────────── */
.rd__end {
  position: absolute; inset: 0; z-index: 7; overflow: hidden;
  display: grid; place-items: center;
}
.rd__end-bg {
  position: absolute; inset: 0; background-size: cover; background-position: center;
  filter: blur(40px) saturate(1.1) brightness(.5); transform: scale(1.2);
}
.rd__end-shade {
  position: absolute; inset: 0;
  background: radial-gradient(70% 70% at 50% 45%, rgba(7,10,18,.55), rgba(5,7,13,.94) 100%);
}
.rd__end-card {
  position: relative; z-index: 1; text-align: center;
  display: flex; flex-direction: column; align-items: center;
  padding: var(--s-6); max-width: 30rem;
  animation: endRise .5s var(--ease-silk) both;
}
@keyframes endRise { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
.rd__end-check {
  width: 3.25rem; height: 3.25rem; display: grid; place-items: center; border-radius: 50%;
  color: #fff; background: color-mix(in srgb, var(--jade) 30%, transparent);
  border: 1px solid color-mix(in srgb, var(--jade) 55%, transparent);
  box-shadow: 0 0 24px color-mix(in srgb, var(--jade) 35%, transparent); margin-bottom: var(--s-4);
}
.rd__end-eyebrow {
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps);
  color: var(--cyan); margin-bottom: var(--s-2);
}
.rd__end-title { font-family: var(--font-display); font-weight: 700; color: #fff; font-size: var(--fs-2xl); line-height: var(--lh-tight); }
.rd__end-stats {
  display: flex; align-items: center; gap: var(--s-2);
  margin-top: var(--s-2); font-size: var(--fs-sm); color: var(--ink-soft);
}
.rd__end-dot { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-faint); }
.rd__end-btns { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); margin-top: var(--s-5); width: 100%; }
.rd__end-primary {
  display: inline-flex; align-items: center; justify-content: center; gap: var(--s-2);
  padding: var(--s-3) var(--s-6); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: #0b0f1a; background: #fff;
  box-shadow: var(--shadow-md); transition: box-shadow var(--t-fast) var(--ease-silk), transform var(--t-fast) var(--ease-silk);
}
.rd__end-primary:hover { box-shadow: var(--glow-azure); transform: translateY(-1px); }
.rd__end-done { font-size: var(--fs-sm); color: var(--ink-faint); }
.rd__end-sub { display: flex; gap: var(--s-2); }
.rd__end-sbtn {
  display: inline-flex; align-items: center; gap: 0.375rem;
  padding: var(--s-2) var(--s-4); border-radius: var(--r-md);
  font-size: var(--fs-xs); font-weight: 500; color: var(--ink-soft);
  background: rgba(255,255,255,.08); border: 1px solid var(--line-2);
  backdrop-filter: blur(8px); transition: all var(--t-fast) var(--ease-silk);
}
.rd__end-sbtn:hover { color: var(--ink); border-color: var(--line-strong); background: rgba(255,255,255,.14); }

/* Webtoon inline end card — same language, sits in the scroll flow */
.rd__wend {
  width: 100%; display: flex; flex-direction: column; align-items: center;
  gap: var(--s-2); padding: var(--s-8) var(--s-5) var(--s-9);
  background: linear-gradient(0deg, var(--void), transparent);
}
.rd__wend .rd__end-primary { margin-top: var(--s-3); }
.rd__wend .rd__end-sub { margin-top: var(--s-3); }

.rd-end-enter-active { transition: opacity var(--t-base) var(--ease-silk); }
.rd-end-leave-active { transition: opacity var(--t-fast) var(--ease-silk); }
.rd-end-enter-from, .rd-end-leave-to { opacity: 0; }

.reader-enter-active, .reader-leave-active { transition: opacity var(--t-base); }
.reader-enter-from, .reader-leave-to { opacity: 0; }
</style>
