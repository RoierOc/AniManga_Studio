<script setup>
/* Lector de NOVELAS (modo texto). Componente propio en vez de un modo más de Reader.vue:
 * comparten el MARCO (overlay, barra que se esconde, panel ⚙️, teclado) pero nada de la
 * maquinaria de imagen — aquí no hay fit, doble página, zoom, precarga de vecinos, comparar
 * 4K ni traducir. Lo que manda es la tipografía.
 *
 * Progreso = capítulo + % de scroll dentro de él (un texto no tiene "páginas"). */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useNovelsStore } from '@/stores/novels'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { smoothBehavior } from '@/lib/motion'

const novels = useNovelsStore()
const open = computed(() => !!novels.reader)
const chapters = computed(() => novels.novel?.chapters || [])
const index = computed(() => novels.reader?.chapterIndex ?? 0)
const hasPrev = computed(() => index.value > 0)
const hasNext = computed(() => index.value < chapters.value.length - 1)

const scroller = ref(null)
const tocScroll = ref(null)
const tocInput = ref(null)
const tocQuery = ref('')

// Índice filtrable: con 1399 capítulos, arrastrar la barra a ojo para llegar al 500 no es
// navegar. Se busca por texto o por número.
const tocList = computed(() => {
  const all = chapters.value.map((c, i) => ({ ...c, _i: i }))
  const q = tocQuery.value.trim().toLowerCase()
  if (!q) return all
  const n = parseInt(q, 10)
  return all.filter(c => (c.name || '').toLowerCase().includes(q) ||
                         (!isNaN(n) && String(c._i + 1).startsWith(String(n))))
})

// Abrir el índice YA POSICIONADO donde estás leyendo (antes abría siempre por el capítulo 1).
async function openToc() {
  tocOpen.value = !tocOpen.value
  setOpen.value = false
  if (!tocOpen.value) return
  tocQuery.value = ''
  await nextTick()
  tocInput.value?.focus()
  const el = tocScroll.value?.querySelector(`[data-i="${index.value}"]`)
  if (el) el.scrollIntoView({ block: 'center' })
}
const setOpen = ref(false)
const tocOpen = ref(false)
const barsHidden = ref(false)
const pct = ref(0)
let pokeTimer = null
let saveTimer = null

function poke() {
  barsHidden.value = false
  clearTimeout(pokeTimer)
  pokeTimer = setTimeout(() => { barsHidden.value = true }, 2200)
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  const max = el.scrollHeight - el.clientHeight
  pct.value = max > 0 ? Math.min(100, Math.round((el.scrollTop / max) * 100)) : 0
  // El guardado se agrupa: escribir en localStorage en cada evento de scroll sería absurdo.
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => novels.saveProgress(pct.value), 400)
}

async function go(i) {
  tocOpen.value = false
  await novels.goChapter(i, 0)
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = 0
  pct.value = 0
}

// Al cargar un capítulo, restaurar la posición guardada (si volvemos a donde lo dejamos).
watch(() => novels.chapterHtml, async (html) => {
  if (!html) return
  await nextTick()
  const saved = novels.progress[novels.reader?.novelId]?.scroll || 0
  const el = scroller.value
  if (el && saved > 0 && saved < 100 && el.scrollTop === 0) {
    el.scrollTop = ((el.scrollHeight - el.clientHeight) * saved) / 100
  }
  onScroll()
})

function onKey(e) {
  if (!open.value) return
  if (e.key === 'Escape') { setOpen.value ? (setOpen.value = false) : tocOpen.value ? (tocOpen.value = false) : novels.closeReader() }
  else if (e.key === 'ArrowRight' && hasNext.value) go(index.value + 1)
  else if (e.key === 'ArrowLeft' && hasPrev.value) go(index.value - 1)
  else if (e.key === ' ') { e.preventDefault(); scroller.value?.scrollBy({ top: scroller.value.clientHeight * 0.85, behavior: smoothBehavior() }) }
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => { window.removeEventListener('keydown', onKey); clearTimeout(pokeTimer); clearTimeout(saveTimer) })

const textStyle = computed(() => ({
  fontSize: `${novels.fontSize}rem`,
  lineHeight: novels.lineHeight,
  maxWidth: `${novels.measure}rem`,
  fontFamily: novels.serif ? 'Georgia, "Iowan Old Style", serif' : 'var(--font-body)',
}))
</script>

<template>
  <Teleport to="body">
    <Transition name="reader">
      <div v-if="open" class="nr" :class="`nr--${novels.theme}`" @mousemove="poke">
        <!-- Barra superior -->
        <header class="nr__bar" :class="{ 'is-hidden': barsHidden }">
          <button class="nr__btn" title="Cerrar (Esc)" @click="novels.closeReader()">
            <Icon name="chevron" :size="18" :style="{ transform: 'rotate(180deg)' }" />
          </button>
          <div class="nr__meta">
            <span class="nr__title">{{ novels.reader.title }}</span>
            <span class="nr__ch">{{ novels.reader.chapterName }}</span>
          </div>
          <div class="nr__tools">
            <button class="nr__btn nr__btn--wide" :class="{ 'is-on': tocOpen }" title="Índice de capítulos"
                    @click.stop="openToc()"><Icon name="library" :size="16" /> Capítulos</button>
            <div class="nr__setwrap">
              <button class="nr__btn" :class="{ 'is-on': setOpen }" title="Ajustes de lectura"
                      @click.stop="setOpen = !setOpen; tocOpen = false"><Icon name="settings" :size="16" /></button>
              <!-- Todo etiquetado en texto, como el panel del lector de manga -->
              <div v-if="setOpen" class="nr__set" @click.stop>
                <div class="nr__set-group">
                  <span class="nr__set-lbl">Tema</span>
                  <div class="nr__seg">
                    <button :class="{ 'is-on': novels.theme === 'night' }" @click="novels.setSetting('theme', 'night')">Noche</button>
                    <button :class="{ 'is-on': novels.theme === 'sepia' }" @click="novels.setSetting('theme', 'sepia')">Sepia</button>
                    <button :class="{ 'is-on': novels.theme === 'light' }" @click="novels.setSetting('theme', 'light')">Claro</button>
                  </div>
                </div>
                <div class="nr__set-group">
                  <span class="nr__set-lbl">Tipografía</span>
                  <div class="nr__seg">
                    <button :class="{ 'is-on': novels.serif }" @click="novels.setSetting('serif', true)">Serif</button>
                    <button :class="{ 'is-on': !novels.serif }" @click="novels.setSetting('serif', false)">Sans</button>
                  </div>
                </div>
                <div class="nr__set-group">
                  <span class="nr__set-lbl">Tamaño de letra <b>{{ novels.fontSize.toFixed(2) }}</b></span>
                  <input type="range" min="0.85" max="1.8" step="0.05" :value="novels.fontSize"
                         @input="novels.setSetting('fontSize', parseFloat($event.target.value))" />
                </div>
                <div class="nr__set-group">
                  <span class="nr__set-lbl">Interlineado <b>{{ novels.lineHeight.toFixed(1) }}</b></span>
                  <input type="range" min="1.3" max="2.4" step="0.1" :value="novels.lineHeight"
                         @input="novels.setSetting('lineHeight', parseFloat($event.target.value))" />
                </div>
                <div class="nr__set-group">
                  <span class="nr__set-lbl">Ancho de línea <b>{{ novels.measure }}</b></span>
                  <input type="range" min="26" max="60" step="2" :value="novels.measure"
                         @input="novels.setSetting('measure', parseInt($event.target.value, 10))" />
                </div>
              </div>
            </div>
          </div>
        </header>

        <!-- Índice de capítulos -->
        <aside v-if="tocOpen" class="nr__toc" @click.stop>
          <div class="nr__toc-head">
            <input ref="tocInput" v-model="tocQuery" class="nr__toc-search" type="search"
                   placeholder="Buscar capítulo o nº…" />
            <span class="nr__toc-count">{{ tocList.length }} / {{ chapters.length }}</span>
          </div>
          <div ref="tocScroll" class="nr__toc-list">
            <button v-for="c in tocList" :key="c.path" class="nr__toc-item"
                    :class="{ 'is-on': c._i === index }" :data-i="c._i" @click="go(c._i)">
              <span class="nr__toc-n">{{ c._i + 1 }}</span>
              <span class="nr__toc-name">{{ c.name || `Capítulo ${c._i + 1}` }}</span>
            </button>
          </div>
        </aside>

        <!-- Texto -->
        <div ref="scroller" class="nr__scroll" @scroll="onScroll" @click="setOpen = false; tocOpen = false">
          <div v-if="novels.chapterLoading" class="nr__loading"><Spinner :size="22" /> Cargando capítulo…</div>
          <template v-else>
            <article class="nr__text" :style="textStyle" v-html="novels.chapterHtml"></article>
            <div class="nr__end">
              <p class="nr__end-lbl">Fin de {{ novels.reader.chapterName }}</p>
              <button v-if="hasNext" class="nr__end-cta" @click="go(index + 1)">
                Siguiente capítulo <Icon name="chevron" :size="16" />
              </button>
              <p v-else class="nr__end-lbl">No hay más capítulos publicados.</p>
            </div>
          </template>
        </div>

        <!-- Barra inferior: navegación + progreso -->
        <footer class="nr__foot" :class="{ 'is-hidden': barsHidden }">
          <button class="nr__nav" :disabled="!hasPrev" @click="go(index - 1)">
            <Icon name="chevron" :size="15" :style="{ transform: 'rotate(180deg)' }" /> Anterior
          </button>
          <div class="nr__prog">
            <div class="nr__prog-bar"><span :style="{ width: pct + '%' }"></span></div>
            <span class="nr__prog-lbl">{{ index + 1 }} / {{ chapters.length }} · {{ pct }}% · {{ novels.chapterMinutes }} min</span>
          </div>
          <button class="nr__nav" :disabled="!hasNext" @click="go(index + 1)">
            Siguiente <Icon name="chevron" :size="15" />
          </button>
        </footer>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.nr { position: fixed; inset: 0; z-index: 130; display: flex; flex-direction: column;
  background: var(--void); color: var(--ink); }
.nr--sepia { background: #f4ecd8; color: #4a3f35; }
.nr--light { background: #fdfdfd; color: #1b1b1f; }

.nr__bar, .nr__foot { position: absolute; left: 0; right: 0; z-index: 3; display: flex; align-items: center;
  gap: var(--s-3); padding: var(--s-3) var(--s-4); background: rgba(7, 10, 18, .82);
  backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); transition: opacity var(--t-med), transform var(--t-med); }
.nr__bar { top: 0; }
.nr__foot { bottom: 0; top: auto; border-bottom: none; border-top: 1px solid var(--line); }
.nr--sepia .nr__bar, .nr--sepia .nr__foot { background: rgba(244, 236, 216, .92); border-color: rgba(74,63,53,.18); color: #4a3f35; }
.nr--light .nr__bar, .nr--light .nr__foot { background: rgba(253, 253, 253, .92); border-color: rgba(0,0,0,.1); color: #1b1b1f; }
.nr__bar.is-hidden { opacity: 0; transform: translateY(-100%); pointer-events: none; }
.nr__foot.is-hidden { opacity: 0; transform: translateY(100%); pointer-events: none; }

.nr__btn { width: 2.125rem; height: 2.125rem; flex: none; display: grid; place-items: center; border-radius: var(--r-sm);
  color: inherit; opacity: .75; transition: all var(--t-fast); }
.nr__btn:hover, .nr__btn.is-on { opacity: 1; background: var(--azure-haze); color: var(--azure-bright); }
.nr__meta { min-width: 0; flex: 1; display: flex; flex-direction: column; }
.nr__title { font-size: var(--fs-sm); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nr__ch { font-size: var(--fs-2xs); opacity: .6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nr__tools { display: flex; align-items: center; gap: var(--s-1); }

.nr__setwrap { position: relative; }
.nr__set { position: absolute; top: calc(100% + var(--s-2)); right: 0; width: 17rem; display: flex;
  flex-direction: column; gap: var(--s-4); padding: var(--s-4); border-radius: var(--r-md);
  background: rgba(12, 16, 26, .96); border: 1px solid var(--line); box-shadow: var(--shadow-lg); color: var(--ink); }
.nr__set-group { display: flex; flex-direction: column; gap: var(--s-2); }
.nr__set-lbl { font-size: var(--fs-2xs); text-transform: uppercase; letter-spacing: .05em; color: var(--ink-faint); }
.nr__set-lbl b { color: var(--azure-bright); font-family: var(--font-mono); }
.nr__seg { display: flex; gap: 2px; padding: 2px; border-radius: var(--r-sm); background: var(--void); border: 1px solid var(--line); }
.nr__seg button { flex: 1; padding: 0.3125rem; border-radius: 0.3125rem; font-size: var(--fs-2xs); color: var(--ink-soft); transition: all var(--t-fast); }
.nr__seg button.is-on { background: var(--azure-haze); color: var(--azure-bright); font-weight: 600; }
.nr__set input[type=range] { width: 100%; accent-color: var(--azure-bright); }

.nr__btn--wide { width: auto; gap: var(--s-2); padding: 0 var(--s-3); display: inline-flex; align-items: center;
  font-size: var(--fs-xs); font-weight: 600; }

.nr__toc { position: absolute; top: 3.6rem; right: var(--s-4); z-index: 4; width: 24rem; max-height: 70vh;
  display: flex; flex-direction: column; padding: var(--s-2); border-radius: var(--r-md);
  background: rgba(12, 16, 26, .96); border: 1px solid var(--line); box-shadow: var(--shadow-lg); color: var(--ink); }
.nr__toc-head { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-1) var(--s-1) var(--s-2); }
.nr__toc-search { flex: 1; min-width: 0; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm);
  background: var(--void); border: 1px solid var(--line); color: var(--ink); font-size: var(--fs-xs); }
.nr__toc-count { flex: none; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.nr__toc-list { overflow-y: auto; display: flex; flex-direction: column; }
.nr__toc-item { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); text-align: left; font-size: var(--fs-xs); color: var(--ink-soft); }
.nr__toc-n { flex: none; min-width: 2.4rem; font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .5; }
.nr__toc-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nr__toc-item:hover { background: var(--surface); color: var(--ink); }
.nr__toc-item.is-on { background: var(--azure-haze); color: var(--azure-bright); font-weight: 600; }
.nr__toc-item.is-on .nr__toc-n { opacity: 1; }

.nr__scroll { flex: 1; overflow-y: auto; padding: 6rem var(--s-5) 8rem; }
.nr__loading { display: flex; align-items: center; justify-content: center; gap: var(--s-3); height: 60vh; opacity: .7; }
.nr__text { margin: 0 auto; }
.nr__text :deep(p) { margin: 0 0 1em; }
.nr__text :deep(img) { max-width: 100%; height: auto; border-radius: var(--r-sm); }
.nr__text :deep(h1), .nr__text :deep(h2), .nr__text :deep(h3) { margin: 1.4em 0 .6em; font-weight: 700; }
.nr__text :deep(hr) { margin: 2em 0; border: none; border-top: 1px solid currentColor; opacity: .18; }

.nr__end { max-width: 38rem; margin: 3rem auto 0; padding-top: var(--s-5); border-top: 1px solid currentColor;
  border-color: color-mix(in srgb, currentColor 18%, transparent); text-align: center; }
.nr__end-lbl { font-size: var(--fs-sm); opacity: .6; margin-bottom: var(--s-3); }
.nr__end-cta { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5);
  border-radius: var(--r-pill); font-size: var(--fs-sm); font-weight: 600;
  color: var(--azure-bright); background: var(--azure-haze); border: 1px solid var(--line); }

.nr__nav { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); font-size: var(--fs-xs); color: inherit; opacity: .8; transition: all var(--t-fast); }
.nr__nav:hover:not(:disabled) { opacity: 1; background: var(--azure-haze); color: var(--azure-bright); }
.nr__nav:disabled { opacity: .3; cursor: default; }
.nr__prog { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.nr__prog-bar { width: min(28rem, 100%); height: 3px; border-radius: 2px; background: color-mix(in srgb, currentColor 18%, transparent); overflow: hidden; }
.nr__prog-bar span { display: block; height: 100%; background: var(--azure-bright); transition: width .15s linear; }
.nr__prog-lbl { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; }

.reader-enter-active, .reader-leave-active { transition: opacity var(--t-med); }
.reader-enter-from, .reader-leave-to { opacity: 0; }
</style>
