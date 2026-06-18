<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { pageUrl } from '@/lib/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangaStore()
const open = computed(() => !!store.reader)
const isManga = computed(() => store.reader?.kind !== 'cbz')
const isUpscaled = computed(() => store.reader?.source === 'upscaled')
const isRTL = computed(() => store.dir === 'rtl')

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
function preloadNeighbors() {
  if (!store.pages.length) return
  const step = store.spreadActive ? 2 : 1
  // Remote (online/MangaDex/source) pages have higher per-request latency than
  // local /uploads — read further ahead so the network has a head start.
  const ahead = store.reader?.source === 'online' ? 3 : 2
  const offsets = [store.page - step]
  for (let n = 1; n <= ahead; n++) offsets.push(store.page + step * n)
  for (const i of offsets) {
    if (i < 0 || i >= store.pages.length) continue
    const u = pageUrl(store.pages[i])
    if (preloaded.has(u)) continue
    preloaded.add(u)
    const img = new Image(); img.src = u
  }
}
// New chapter/file → reset the cache-key set, then warm the neighbours.
watch(() => store.pages, () => { preloaded = new Set(); preloadNeighbors() })
watch(() => store.page, () => preloadNeighbors())

const wrap = ref(null)
let barsTimer = null

// ── transform for paged/compare layers ──
const transform = computed(() => `translate(${store.panX}px, ${store.panY}px) scale(${store.zoom})`)

// ── drag-to-pan + click navigation ──
let drag = { active: false, moved: false, ox: 0, oy: 0, px: 0, py: 0 }
function onDown(e) {
  if (store.compareMode) return
  // Always register the press so a plain click navigates; panning only kicks in when zoomed.
  drag = { active: true, moved: false, ox: e.clientX, oy: e.clientY, px: store.panX, py: store.panY }
}
function onMove(e) {
  // Pan only while dragging AND zoomed in — never on a plain cursor move.
  if (!drag.active || store.zoom <= 1.01 || store.compareMode) return
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
  goNext ? store.nextPage() : store.prevPage()
}
// Leaving the area must NOT navigate — only cancel an in-progress drag.
function endDrag() { drag.active = false }

// Click handler on the page image itself — this is the element that receives mouse events.
function pageClick(e) {
  if (store.mode !== 'paged' || store.compareMode || store.zoom > 1.01) return
  if (drag.moved) { drag.moved = false; return }
  const x = e.clientX / window.innerWidth
  const goNext = isRTL.value ? x < 0.5 : x > 0.5
  goNext ? store.nextPage() : store.prevPage()
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
}

// ── auto-hide bars ──
function poke() {
  store.barsHidden = false
  clearTimeout(barsTimer)
  if (store.mode === 'paged') barsTimer = setTimeout(() => { store.barsHidden = true }, 1600)
}

function toggleFullscreen() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {})
  else document.exitFullscreen().catch(() => {})
}

// ── keyboard ──
function onKey(e) {
  if (!open.value) return
  switch (e.key) {
    case 'Escape': store.closeReader(); break
    case 'ArrowRight': e.preventDefault(); isRTL.value ? store.prevPage() : store.nextPage(); break
    case 'ArrowLeft': e.preventDefault(); isRTL.value ? store.nextPage() : store.prevPage(); break
    case ' ': e.preventDefault(); store.nextPage(); break
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
      <div v-if="open" class="rd" @mousemove="poke">
        <!-- Top bar -->
        <header class="rd__bar" :class="{ 'is-hidden': store.barsHidden }">
          <button class="rd__btn" @click="store.closeReader()"><Icon name="chevron" :size="18" :style="{ transform: 'rotate(180deg)' }" /></button>
          <div class="rd__meta">
            <span class="rd__title">{{ isManga ? store.current?.name : store.reader.title }}</span>
            <span v-if="store.reader.chapter" class="rd__ch">{{ isManga ? 'Cap. ' : '' }}{{ store.reader.chapter }}</span>
            <span v-if="isManga" class="rd__src" :class="{ 'is-4k': isUpscaled }">{{ isUpscaled ? '4K' : 'ORIG' }}</span>
          </div>
          <div class="rd__tools">
            <button class="rd__btn" :title="`Ajuste: ${store.fit}`" @click="store.cycleFit()">
              <span class="rd__txt">{{ store.fit === 'width' ? '↔' : store.fit === 'height' ? '↕' : '1:1' }}</span>
            </button>
            <button v-if="isManga && store.mode === 'paged'" class="rd__btn" :title="store.dir.toUpperCase()" @click="store.toggleDir()"><span class="rd__txt">{{ store.dir.toUpperCase() }}</span></button>
            <button v-if="store.mode === 'paged'" class="rd__btn" :class="{ 'is-on': store.spread }" title="Doble página" @click="store.toggleSpread()"><span class="rd__txt">▌▐</span></button>
            <button class="rd__btn" :class="{ 'is-on': store.mode === 'webtoon' }" title="Paginado / Webtoon" @click="store.setMode(store.mode === 'paged' ? 'webtoon' : 'paged')"><Icon :name="store.mode === 'paged' ? 'library' : 'film'" :size="16" /></button>
            <button class="rd__btn rd__zoom" title="Restablecer zoom" @click="store.resetZoom()">{{ Math.round(store.zoom * 100) }}%</button>
            <button v-if="store.canCompare" class="rd__btn" :class="{ 'is-on': store.compareMode }" title="Comparar original / 4K (C)" @click="store.toggleCompare()"><Icon name="spark" :size="15" /></button>
            <button v-if="isManga" class="rd__btn" :class="{ 'is-on': store.isChapterRead(store.reader.chapter) }" title="Marcar leído" @click="store.toggleChapterRead(store.reader.chapter)"><Icon name="check" :size="15" /></button>
            <button class="rd__btn" title="Pantalla completa" @click="toggleFullscreen"><Icon name="spark" :size="15" /></button>
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
            <img :src="store.pageUpUrl" class="rd__cmp-img" alt="" />
            <img :src="store.pageOrigUrl" class="rd__cmp-img rd__cmp-orig"
                 :style="{ clipPath: `inset(0 ${100 - store.compareX}% 0 0)` }" alt="" />
            <div class="rd__divider" :style="{ left: store.compareX + '%' }" @mousedown.stop="cmpStart"><span class="rd__handle">⟷</span></div>
            <span class="rd__clabel rd__clabel--l">{{ store.scanCompareMode ? 'PRINCIPAL' : 'ORIGINAL' }}</span>
            <span class="rd__clabel rd__clabel--r">{{ store.scanCompareMode ? 'VARIANTE' : '4K' }}</span>
          </div>
          <!-- two-page spread -->
          <div v-else-if="store.spreadActive && store.spreadPair.length === 2" :key="'sp' + store.page"
               class="rd__spread rd__fade" :class="[`fit-${store.fit}`, { 'is-rtl': isRTL }]" :style="{ transform }"
               @click.stop.prevent="pageClick">
            <img :src="pageUrl(store.pages[store.spreadPair[0]])" class="rd__simg" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.spreadPair[0] + 1}`" />
            <img :src="pageUrl(store.pages[store.spreadPair[1]])" class="rd__simg" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.spreadPair[1] + 1}`" />
          </div>
          <!-- single page -->
          <img v-else :key="'pg' + store.page" :src="pageUrl(store.pages[store.page])" class="rd__img rd__fade" :class="`fit-${store.fit}`" :style="{ transform }" draggable="false" decoding="async" fetchpriority="high" :alt="`Página ${store.page + 1}`"
               @click.stop.prevent="pageClick" />

          <div class="rd__counter" :class="{ 'is-hidden': store.barsHidden }">{{ counterLabel }}</div>
        </div>

        <!-- Webtoon -->
        <div v-else class="rd__webtoon" @scroll="onScroll">
          <img v-for="(p, i) in store.pages" :key="i" :src="pageUrl(p)" loading="lazy" decoding="async" class="rd__wimg" :style="{ maxWidth: store.fit === 'width' ? '900px' : 'none', transform: `scale(${store.zoom})` }" :alt="`Página ${i + 1}`" />
        </div>

        <!-- Bottom bar (paged) -->
        <footer v-if="store.mode === 'paged' && store.pages.length" class="rd__bottom" :class="{ 'is-hidden': store.barsHidden }">
          <div class="rd__chnav">
            <button class="rd__chbtn" :disabled="!isManga || !store.canPrevChapter" @click="store.goPrevChapter()">‹ Cap.</button>
            <input class="rd__scrub" type="range" min="0" :max="store.pages.length - 1" :value="store.page"
                   :style="{ direction: isRTL ? 'rtl' : 'ltr' }" @input="store.setPage(Number($event.target.value))" />
            <button class="rd__chbtn" :disabled="!isManga || !store.canNextChapter" @click="store.goNextChapter()">Cap. ›</button>
          </div>
          <div class="rd__thumbs">
            <button v-for="(p, i) in store.pages" :key="i" class="rd__thumb" :class="{ 'is-active': i === store.page }" @click="store.setPage(i)">
              <img :src="pageUrl(p)" loading="lazy" alt="" />
            </button>
          </div>
        </footer>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.rd { position: fixed; inset: 0; z-index: 110; background: #05070d; display: flex; flex-direction: column; }

.rd__bar { position: absolute; top: 0; left: 0; right: 0; z-index: 6; display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-4); height: 52px;
  background: linear-gradient(180deg, rgba(7,10,18,.95), rgba(7,10,18,.5)); backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); transition: transform var(--t-base) var(--ease-silk); }
.rd__bar.is-hidden { transform: translateY(-100%); }
.rd__btn { min-width: 36px; height: 36px; padding: 0 8px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__btn:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface); }
.rd__btn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.rd__txt, .rd__zoom { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; }
.rd__meta { display: flex; align-items: center; gap: var(--s-3); flex: 1; min-width: 0; }
.rd__title { font-weight: 600; font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rd__ch { color: var(--ink-faint); font-size: var(--fs-xs); white-space: nowrap; }
.rd__src { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-pill); color: var(--ink-faint); background: var(--surface); }
.rd__src.is-4k { color: var(--cyan); background: var(--cyan-glow); }
.rd__tools { display: flex; gap: var(--s-1); }

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
.rd__img { display: block; will-change: transform; user-select: none; }
.rd__img.fit-width { width: 100%; max-width: 1000px; height: auto; }
.rd__img.fit-height { height: 100vh; width: auto; }
.rd__img.fit-original { width: auto; height: auto; max-width: none; }

/* two-page spread: both pages share one container; RTL flips reading order */
.rd__spread { display: flex; align-items: flex-start; will-change: transform; }
.rd__spread.is-rtl { flex-direction: row-reverse; }
.rd__spread.fit-width { width: 100%; max-width: 1800px; }
.rd__spread.fit-width .rd__simg { width: 50%; height: auto; }
.rd__spread.fit-height { height: 100vh; }
.rd__spread.fit-height .rd__simg { height: 100vh; width: auto; }
.rd__spread.fit-original .rd__simg { width: auto; height: auto; }
.rd__simg { display: block; user-select: none; }

/* soft fade-in on page turn (preloaded image is already cached → no white flash) */
.rd__fade { animation: pageFade .18s var(--ease-silk); }
@keyframes pageFade { from { opacity: .4 } to { opacity: 1 } }

.rd__ghost { position: absolute; top: 0; bottom: 0; width: 80px; z-index: 4; display: flex; align-items: center; opacity: 0; transition: opacity var(--t-fast); border: none; background: linear-gradient(90deg, rgba(77,141,255,.18), transparent); }
.rd__ghost--prev { left: 0; justify-content: flex-start; padding-left: var(--s-3); }
.rd__ghost--next { right: 0; justify-content: flex-end; padding-right: var(--s-3); background: linear-gradient(270deg, rgba(77,141,255,.18), transparent); }
.rd__paged:hover .rd__ghost { opacity: 1; }
.rd__ghost span { font-size: var(--fs-xs); color: var(--ice); font-weight: 600; text-shadow: 0 1px 4px #000; }

.rd__cmp { position: relative; will-change: transform; }
/* Fit behaviour goes on the wrapper; both images fill it at identical dimensions. */
.rd__cmp.fit-width  { width: 100%; max-width: 1000px; }
.rd__cmp.fit-height { height: 100vh; }
.rd__cmp.fit-original { width: auto; height: auto; }
.rd__cmp-img { display: block; width: 100%; height: auto; }
.rd__cmp.fit-height .rd__cmp-img { width: auto; height: 100%; }
.rd__cmp-orig { position: absolute; inset: 0; width: 100% !important; height: 100% !important; object-fit: cover; }
/* 24px-wide invisible grab zone centred on a 2px visible line — much easier to drag */
.rd__divider { position: absolute; top: 0; bottom: 0; width: 24px; transform: translateX(-50%); cursor: col-resize; z-index: 5; display: grid; place-items: center; }
.rd__divider::before { content: ''; position: absolute; top: 0; bottom: 0; width: 2px; background: var(--azure); box-shadow: 0 0 12px var(--azure-glow); }
.rd__handle { position: relative; width: 34px; height: 34px; display: grid; place-items: center; border-radius: 50%; background: var(--azure); color: #fff; font-size: 14px; box-shadow: var(--glow-azure); }
.rd__clabel { position: absolute; top: var(--s-3); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 8px; border-radius: var(--r-xs); background: rgba(7,10,18,.7); }
.rd__clabel--l { left: var(--s-3); color: var(--ink-soft); }
.rd__clabel--r { right: var(--s-3); color: var(--cyan); }

.rd__counter { position: absolute; bottom: var(--s-4); left: 50%; transform: translateX(-50%); z-index: 6; font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); padding: 4px 12px; border-radius: var(--r-pill); background: rgba(7,10,18,.7); backdrop-filter: blur(6px); transition: opacity var(--t-fast); }
.rd__counter.is-hidden { opacity: 0; }

.rd__webtoon { flex: 1; overflow-y: auto; display: flex; flex-direction: column; align-items: center; padding-top: 52px; }
.rd__wimg { width: 100%; height: auto; display: block; }

.rd__bottom { position: absolute; bottom: 0; left: 0; right: 0; z-index: 6; padding: var(--s-3) var(--s-4); background: linear-gradient(0deg, rgba(7,10,18,.96), transparent); transition: transform var(--t-base) var(--ease-silk); }
.rd__bottom.is-hidden { transform: translateY(100%); }
.rd__chnav { display: flex; align-items: center; gap: var(--s-3); }
.rd__chbtn { padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__chbtn:hover:not(:disabled) { color: var(--ink); border-color: var(--azure); }
.rd__chbtn:disabled { opacity: .35; }
.rd__scrub { flex: 1; accent-color: var(--azure); }
.rd__thumbs { display: flex; gap: var(--s-2); overflow-x: auto; padding-top: var(--s-2); }
.rd__thumb { flex-shrink: 0; width: 44px; height: 62px; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; opacity: .5; transition: all var(--t-fast); }
.rd__thumb img { width: 100%; height: 100%; object-fit: cover; }
.rd__thumb:hover { opacity: .85; }
.rd__thumb.is-active { opacity: 1; border-color: var(--azure); }

.reader-enter-active, .reader-leave-active { transition: opacity var(--t-base); }
.reader-enter-from, .reader-leave-to { opacity: 0; }
</style>
