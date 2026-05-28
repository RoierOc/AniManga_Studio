<script setup>
import { computed, onMounted, onUnmounted, watch, ref } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { pageUrl } from '@/lib/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangaStore()
const scroller = ref(null)

const open = computed(() => !!store.reader)
const isUpscaled = computed(() => store.reader?.source === 'upscaled')

function onKey(e) {
  if (!open.value) return
  if (e.key === 'Escape') return store.closeReader()
  if (store.mode !== 'paged') return
  const fwd = store.dir === 'rtl' ? ['ArrowLeft'] : ['ArrowRight']
  const back = store.dir === 'rtl' ? ['ArrowRight'] : ['ArrowLeft']
  if (fwd.includes(e.key) || e.key === ' ') { e.preventDefault(); store.nextPage() }
  else if (back.includes(e.key)) { e.preventDefault(); store.prevPage() }
}

// click zones in paged mode (respect reading direction)
function clickZone(e) {
  if (store.mode !== 'paged') return
  const x = e.clientX / window.innerWidth
  const goNext = store.dir === 'rtl' ? x < 0.5 : x > 0.5
  goNext ? store.nextPage() : store.prevPage()
}

watch(() => store.mode, (m) => { if (m === 'webtoon' && scroller.value) scroller.value.scrollTop = 0 })

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="reader">
      <div v-if="open" class="rd">
        <!-- Top bar -->
        <header class="rd__bar">
          <button class="rd__btn" @click="store.closeReader()"><Icon name="chevron" :size="18" :style="{ transform: 'rotate(180deg)' }" /></button>
          <div class="rd__meta">
            <span class="rd__title">{{ store.current?.name }}</span>
            <span class="rd__ch">Cap. {{ store.reader.chapter }}</span>
            <span class="rd__src" :class="{ 'is-4k': isUpscaled }">{{ isUpscaled ? '4K' : 'ORIG' }}</span>
          </div>

          <div class="rd__tools">
            <button class="rd__btn" :class="{ 'is-on': store.mode === 'webtoon' }" :title="store.mode === 'paged' ? 'Modo webtoon' : 'Modo paginado'" @click="store.setMode(store.mode === 'paged' ? 'webtoon' : 'paged')">
              <Icon :name="store.mode === 'paged' ? 'library' : 'film'" :size="16" />
            </button>
            <button class="rd__btn" :title="store.fit === 'width' ? 'Ajustar a alto' : 'Ajustar a ancho'" @click="store.setFit(store.fit === 'width' ? 'height' : 'width')">
              <span class="rd__txt">{{ store.fit === 'width' ? '↔' : '↕' }}</span>
            </button>
            <button class="rd__btn" v-if="store.mode === 'paged'" :title="store.dir === 'rtl' ? 'Derecha→Izquierda' : 'Izquierda→Derecha'" @click="store.toggleDir()">
              <span class="rd__txt">{{ store.dir === 'rtl' ? 'RTL' : 'LTR' }}</span>
            </button>
            <button class="rd__btn" :class="{ 'is-on': isUpscaled }" title="Original / 4K" @click="store.read(store.reader.chapter, isUpscaled ? 'original' : 'upscaled')">
              <Icon name="spark" :size="15" />
            </button>
          </div>
        </header>

        <!-- Content -->
        <div v-if="store.readerLoading" class="rd__center"><Spinner :size="32" /></div>
        <div v-else-if="!store.pages.length" class="rd__center rd__empty">Sin páginas.</div>

        <!-- Paged -->
        <div v-else-if="store.mode === 'paged'" class="rd__paged" @click="clickZone">
          <img :src="pageUrl(store.pages[store.page])" class="rd__img" :class="`fit-${store.fit}`" :alt="`Página ${store.page + 1}`" />
          <div class="rd__counter">{{ store.page + 1 }} / {{ store.pages.length }}</div>
        </div>

        <!-- Webtoon -->
        <div v-else ref="scroller" class="rd__webtoon">
          <img v-for="(p, i) in store.pages" :key="i" :src="pageUrl(p)" loading="lazy" class="rd__wimg" :alt="`Página ${i + 1}`" />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.rd { position: fixed; inset: 0; z-index: 110; background: #05070d; display: flex; flex-direction: column; }

.rd__bar { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-4); height: 52px;
  background: linear-gradient(180deg, rgba(7,10,18,.95), rgba(7,10,18,.6)); backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line); z-index: 2; }
.rd__btn { width: 36px; height: 36px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.rd__btn:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface); }
.rd__btn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.rd__txt { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; }
.rd__meta { display: flex; align-items: center; gap: var(--s-3); flex: 1; min-width: 0; }
.rd__title { font-weight: 600; font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rd__ch { color: var(--ink-faint); font-size: var(--fs-xs); white-space: nowrap; }
.rd__src { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 2px 7px; border-radius: var(--r-pill); color: var(--ink-faint); background: var(--surface); }
.rd__src.is-4k { color: var(--cyan); background: var(--cyan-glow); }
.rd__tools { display: flex; gap: var(--s-1); }

.rd__center { flex: 1; display: grid; place-items: center; }
.rd__empty { color: var(--ink-faint); }

.rd__paged { flex: 1; display: grid; place-items: center; overflow: hidden; cursor: pointer; position: relative; }
.rd__img { display: block; }
.rd__img.fit-width { width: 100%; max-width: 1000px; height: auto; }
.rd__img.fit-height { height: calc(100vh - 52px); width: auto; }
.rd__counter { position: absolute; bottom: var(--s-4); left: 50%; transform: translateX(-50%); font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); padding: 4px 12px; border-radius: var(--r-pill); background: rgba(7,10,18,.7); backdrop-filter: blur(6px); }

.rd__webtoon { flex: 1; overflow-y: auto; display: flex; flex-direction: column; align-items: center; }
.rd__wimg { width: 100%; max-width: 900px; height: auto; display: block; }

.reader-enter-active, .reader-leave-active { transition: opacity var(--t-base); }
.reader-enter-from, .reader-leave-to { opacity: 0; }
</style>
