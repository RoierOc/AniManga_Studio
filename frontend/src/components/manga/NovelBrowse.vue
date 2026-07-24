<script setup>
/* Catálogo de SkyNovels — navegar la fuente ES fiable (API oficial, 476 novelas) y añadir a la
 * biblioteca. Las novelas de esta fuente se leen con el proveedor NATIVO del sidecar
 * (`skynovels.cjs`), no con el plugin scraper. Modal, mismo marco que el resto (useModal). */
import { onBeforeUnmount, ref, watch } from 'vue'
import { useNovelsStore } from '@/stores/novels'
import { imgProxy } from '@/lib/img'
import { useModal } from '@/lib/useModal'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

const store = useNovelsStore()
const boxEl = ref(null)
const sentinel = ref(null)
let io = null

const SORTS = [
  { id: 'views', label: 'Populares' },
  { id: 'rating', label: 'Mejor valoradas' },
  { id: 'chapters', label: 'Más capítulos' },
  { id: 'title', label: 'A–Z' },
]

// Scroll infinito dentro del cuerpo del modal (mismo patrón que el resto de rejillas).
watch(sentinel, (el, prev) => {
  if (!('IntersectionObserver' in window)) return
  if (prev && io) io.unobserve(prev)
  if (!el) return
  io = io || new IntersectionObserver((e) => { if (e[0].isIntersecting) store.loadMoreBrowse() }, { rootMargin: '500px' })
  io.observe(el)
})
onBeforeUnmount(() => io?.disconnect())

useModal(() => store.browseOpen, () => store.closeBrowse(), boxEl)

function add(n) { store.addToLibrary(n, n.name) }
async function read(n) { await store.addToLibrary(n, n.name); store.openReader({ id: `novel:skynovels:${n.path}`, title: n.name, cover: n.cover, novel: { pluginId: 'skynovels', path: n.path } }) }
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="store.browseOpen" class="nb__ov" @click.self="store.closeBrowse()">
        <div ref="boxEl" class="nb" role="dialog" aria-label="Catálogo de SkyNovels">
          <header class="nb__head">
            <div class="nb__title">
              <span class="nb__brand">SkyNovels</span>
              <span class="nb__count">{{ store.browseTotal }} novelas en español</span>
            </div>
            <button class="nb__x" @click="store.closeBrowse()"><Icon name="close" :size="18" /></button>
          </header>

          <div class="nb__bar">
            <label class="nb__search">
              <Icon name="search" :size="15" />
              <input v-model="store.browseSearch" type="search" placeholder="Buscar en el catálogo…"
                     enterkeyhint="search" @keyup.enter="store.runBrowseSearch()" />
              <button v-if="store.browseSearch" class="nb__clear" aria-label="Limpiar"
                      @click="store.browseSearch = ''; store.runBrowseSearch()"><Icon name="close" :size="13" /></button>
            </label>
            <div class="nb__sorts" :class="{ 'is-dim': !!store.browseSearch }">
              <button v-for="s in SORTS" :key="s.id" class="nb__pill" :class="{ 'is-active': store.browseSort === s.id }"
                      :disabled="!!store.browseSearch" @click="store.setBrowseSort(s.id)">{{ s.label }}</button>
            </div>
          </div>

          <div class="nb__body">
            <div v-if="store.browseLoading" class="nb__grid">
              <Skeleton v-for="n in 12" :key="n" variant="poster" />
            </div>

            <p v-else-if="!store.browseItems.length" class="nb__empty">
              {{ store.browseSearch ? 'Nada coincide con esa búsqueda.' : 'No se pudo cargar el catálogo.' }}
            </p>

            <template v-else>
              <div class="nb__grid">
                <article v-for="n in store.browseItems" :key="n.path" class="nc">
                  <div class="nc__cover">
                    <img v-if="n.cover" :src="imgProxy(n.cover, 200)" :alt="n.name" loading="lazy" />
                    <div v-else class="nc__nocover"><Icon name="book" :size="24" /></div>
                    <span v-if="n.rating" class="nc__rating">★ {{ n.rating }}</span>
                    <div class="nc__actions">
                      <button class="nc__btn nc__btn--primary" :disabled="store.isAdding(n)"
                              @click="read(n)"><Icon name="play" :size="14" /> Leer</button>
                      <button class="nc__btn" :disabled="store.inLibrary(n) || store.isAdding(n)" @click="add(n)">
                        <Icon :name="store.inLibrary(n) ? 'check' : 'plus'" :size="14" />
                        {{ store.inLibrary(n) ? 'En biblioteca' : 'Añadir' }}
                      </button>
                    </div>
                  </div>
                  <p class="nc__name" :title="n.name">{{ n.name }}</p>
                  <p class="nc__meta">{{ n.chapters }} cap.<span v-if="n.status"> · {{ n.status === 'Active' ? 'En curso' : n.status }}</span></p>
                </article>
              </div>
              <div ref="sentinel" class="nb__more">
                <Spinner v-if="store.browseMoreLoading" :size="18" />
              </div>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.nb__ov { position: fixed; inset: 0; z-index: var(--z-modal); background: rgba(4,6,12,.62);
  backdrop-filter: blur(6px); display: flex; justify-content: center; align-items: flex-start;
  padding: 6vh var(--s-4) var(--s-4); }
.nb { width: min(64rem, 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line-strong); border-radius: var(--r-lg);
  box-shadow: 0 24px 64px -12px rgba(0,0,0,.7); overflow: hidden; }

.nb__head { display: flex; align-items: center; justify-content: space-between;
  padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); }
.nb__title { display: flex; align-items: baseline; gap: var(--s-3); }
.nb__brand { font-family: var(--font-display); font-size: var(--fs-xl); font-weight: 700; color: var(--azure-bright); }
.nb__count { font-size: var(--fs-xs); color: var(--ink-faint); }
.nb__x { width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm);
  color: var(--ink-soft); border: 1px solid var(--line); }
.nb__x:hover { color: var(--ink); border-color: var(--line-strong); }

.nb__bar { display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center;
  padding: var(--s-3) var(--s-5); border-bottom: 1px solid var(--line); }
.nb__search { display: flex; align-items: center; gap: var(--s-2); flex: 1; min-width: 14rem;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-pill); background: var(--base); border: 1px solid var(--line); color: var(--ink-faint); }
.nb__search input { flex: 1; min-width: 0; background: none; border: 0; outline: none; color: var(--ink); font-size: var(--fs-sm); }
.nb__clear { color: var(--ink-faint); display: grid; place-items: center; }
.nb__sorts { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.nb__sorts.is-dim { opacity: .4; }
.nb__pill { padding: var(--s-1) var(--s-3); border-radius: var(--r-pill); font-size: var(--fs-xs); font-weight: 600;
  color: var(--ink-soft); background: var(--base); border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.nb__pill:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.nb__pill.is-active { color: #fff; background: var(--azure); border-color: transparent; }
.nb__pill:disabled { cursor: default; }

.nb__body { overflow-y: auto; padding: var(--s-4) var(--s-5) var(--s-5); }
.nb__grid { display: grid; gap: var(--s-4); grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr)); }
.nb__empty { text-align: center; color: var(--ink-faint); padding: var(--s-8) 0; font-size: var(--fs-sm); }
.nb__more { display: grid; place-items: center; min-height: 2.5rem; margin-top: var(--s-4); }

.nc { display: flex; flex-direction: column; gap: var(--s-1); }
.nc__cover { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); }
.nc__cover img { width: 100%; height: 100%; object-fit: cover; }
.nc__nocover { width: 100%; height: 100%; display: grid; place-items: center; color: var(--ink-faint); }
.nc__rating { position: absolute; top: var(--s-1); left: var(--s-1); font-size: var(--fs-2xs); font-weight: 700;
  color: #fff; background: rgba(4,6,12,.7); padding: 2px 0.4375rem; border-radius: var(--r-pill); backdrop-filter: blur(4px); }
.nc__actions { position: absolute; inset: auto 0 0 0; display: flex; flex-direction: column; gap: 3px; padding: var(--s-2);
  background: linear-gradient(0deg, rgba(4,6,12,.92), transparent); opacity: 0; transition: opacity var(--t-fast); }
.nc__cover:hover .nc__actions { opacity: 1; }
@media (hover: none) { .nc__actions { opacity: 1; } }
.nc__btn { display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 5px; border-radius: var(--r-sm);
  font-size: var(--fs-2xs); font-weight: 600; color: var(--ink); background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.18);
  cursor: pointer; transition: all var(--t-fast); }
.nc__btn:hover:not(:disabled) { background: rgba(255,255,255,.2); }
.nc__btn--primary { background: var(--azure); border-color: transparent; color: #fff; }
.nc__btn--primary:hover:not(:disabled) { background: var(--azure-bright); }
.nc__btn:disabled { opacity: .6; cursor: default; }
.nc__name { font-size: var(--fs-xs); font-weight: 600; color: var(--ink); line-height: 1.25;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.nc__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
</style>
