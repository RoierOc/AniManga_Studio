<script setup>
/* Vista "Descubrir" — explorador estilo AniList del Manga Hub. Rejilla de OBRAS populares
 * gobernada por filtros (texto + tipo + géneros + orden), deduplicadas del meta-source.
 * Clic → ficha (WorkInfoModal). Estética Midnight Atelier. */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useDiscoveryStore, TYPE_FILTERS, SORT_OPTIONS } from '@/stores/discovery'
import WorkCard from '@/components/manga/WorkCard.vue'
import WorkInfoModal from '@/components/manga/WorkInfoModal.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useDiscoveryStore()

const genreDd = ref(null)
const sortDd = ref(null)
const genreOpen = ref(false)
const sortOpen = ref(false)

const sortLabel = computed(() => SORT_OPTIONS.find(s => s.id === store.sort)?.label || 'Ordenar')

function onDocClick(e) {
  if (genreOpen.value && genreDd.value && !genreDd.value.contains(e.target)) genreOpen.value = false
  if (sortOpen.value && sortDd.value && !sortDd.value.contains(e.target)) sortOpen.value = false
}
onMounted(() => { store.init(); document.addEventListener('click', onDocClick) })
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div class="disco">
    <header class="disco__head">
      <p class="eyebrow"><span class="tick" /> DESCUBRE SIN LÍMITES</p>
      <h1>Explorar manga, manhwa y novelas</h1>
      <p class="disco__sub">Lo más popular de todas las bases, en un solo lugar. Filtra por tipo y género, y abre cualquier obra para elegir la mejor versión.</p>
    </header>

    <!-- Barra de filtros -->
    <div class="filters">
      <label class="fsearch">
        <Icon name="search" :size="18" />
        <input v-model="store.query" @input="store.onQueryInput()" @keyup.enter="store.submitQuery()"
               placeholder="Buscar por título…" aria-label="Buscar por título" />
        <button v-if="store.query" class="fsearch__x" aria-label="Limpiar búsqueda"
                @click="store.query = ''; store.submitQuery()"><Icon name="close" :size="14" /></button>
      </label>

      <div class="filters__row">
        <div class="segs" role="tablist" aria-label="Tipo">
          <button v-for="t in TYPE_FILTERS" :key="t.id" class="seg" :class="{ 'is-on': store.type === t.id }"
                  role="tab" :aria-selected="store.type === t.id" @click="store.setType(t.id)">{{ t.label }}</button>
        </div>

        <div class="filters__spacer" />

        <!-- Género (multi-select) -->
        <div class="dd" ref="genreDd">
          <button class="dd__btn" :class="{ 'is-on': store.genres.length || genreOpen }" @click="genreOpen = !genreOpen" aria-haspopup="true" :aria-expanded="genreOpen">
            <Icon name="grid" :size="15" /> Género
            <span v-if="store.genres.length" class="dd__count">{{ store.genres.length }}</span>
            <Icon name="chevron" :size="14" class="dd__caret" :class="{ 'is-open': genreOpen }" />
          </button>
          <div v-if="genreOpen" class="dd__pop">
            <div class="dd__pop-head">
              <span>Filtrar por género</span>
              <button v-if="store.genres.length" class="dd__clear" @click="store.clearGenres()">Limpiar ({{ store.genres.length }})</button>
            </div>
            <div class="dd__genres">
              <button v-for="g in store.genreCatalog" :key="g.value" class="gchip"
                      :class="{ 'is-on': store.genres.includes(g.value) }" @click="store.toggleGenre(g.value)">
                {{ g.label }}
              </button>
            </div>
          </div>
        </div>

        <!-- Orden -->
        <div class="dd" ref="sortDd">
          <button class="dd__btn" :class="{ 'is-on': sortOpen }" @click="sortOpen = !sortOpen" aria-haspopup="true" :aria-expanded="sortOpen">
            <Icon name="spark" :size="14" /> {{ sortLabel }}
            <Icon name="chevron" :size="14" class="dd__caret" :class="{ 'is-open': sortOpen }" />
          </button>
          <div v-if="sortOpen" class="dd__pop dd__pop--sort">
            <button v-for="s in SORT_OPTIONS" :key="s.id" class="dd__opt" :class="{ 'is-on': store.sort === s.id }"
                    @click="store.setSort(s.id); sortOpen = false">
              <Icon v-if="store.sort === s.id" name="check" :size="13" /><span v-else class="dd__opt-dot" /> {{ s.label }}
            </button>
          </div>
        </div>
      </div>

      <!-- Géneros activos como chips removibles -->
      <div v-if="store.genreLabels.length" class="chips">
        <button v-for="g in store.genreLabels" :key="g.value" class="chip" @click="store.toggleGenre(g.value)">
          {{ g.label }} <Icon name="close" :size="11" />
        </button>
      </div>
    </div>

    <!-- Rejilla -->
    <div v-if="store.loading" class="grid">
      <div v-for="n in 18" :key="n" class="skeleton" />
    </div>

    <div v-else-if="store.error" class="state">
      <Icon name="spark" :size="30" />
      <p>{{ store.error }}</p>
      <button class="state__btn" @click="store.browse()">Reintentar</button>
    </div>

    <template v-else-if="store.items.length">
      <div class="grid">
        <WorkCard v-for="w in store.items" :key="w.id" :work="w" @select="store.openWork($event)" />
      </div>
      <div class="more">
        <button v-if="store.hasMore" class="more__btn" :disabled="store.loadingMore" @click="store.loadMore()">
          <span v-if="store.loadingMore" class="more__spin" /> {{ store.loadingMore ? 'Cargando…' : 'Cargar más' }}
        </button>
      </div>
    </template>

    <div v-else class="state">
      <Icon name="search" :size="30" />
      <p>Sin resultados con estos filtros.</p>
      <button v-if="store.hasFilters" class="state__btn" @click="store.clearFilters()">Quitar filtros</button>
    </div>

    <WorkInfoModal />
  </div>
</template>

<style scoped>
.disco { padding: 0 var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }
.disco__head { padding: var(--s-5) 0 var(--s-4); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 1.6rem; height: 1px; background: var(--azure); opacity: .7; }
.disco__head h1 { font-family: var(--font-display); font-size: var(--fs-2xl); font-weight: 700; line-height: var(--lh-tight); }
.disco__sub { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: var(--s-2); max-width: 58ch; }

/* ── Barra de filtros ── */
.filters { position: sticky; top: 0; z-index: 8; padding: var(--s-3) 0 var(--s-4);
  background: linear-gradient(180deg, var(--void) 82%, transparent);
  backdrop-filter: blur(6px); margin-bottom: var(--s-2); }
.fsearch { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4);
  border: 1px solid var(--line-strong); border-radius: var(--r-lg, 16px); background: var(--surface);
  color: var(--ink-faint); transition: border-color var(--t-fast); }
.fsearch:focus-within { border-color: var(--azure); }
.fsearch input { flex: 1; min-width: 0; background: none; border: none; outline: none; color: var(--ink); font-size: var(--fs-base); }
.fsearch__x { flex: none; color: var(--ink-faint); padding: 2px; border-radius: 50%; transition: color var(--t-fast); }
.fsearch__x:hover { color: var(--ink); }

.filters__row { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.filters__spacer { flex: 1 1 auto; }

.segs { display: inline-flex; gap: 2px; padding: 3px; border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--surface); }
.seg { font-size: var(--fs-xs); font-weight: 600; padding: var(--s-2) var(--s-3); border-radius: var(--r-pill);
  color: var(--ink-faint); transition: all var(--t-fast); white-space: nowrap; }
.seg:hover { color: var(--ink); }
.seg.is-on { color: var(--azure-bright); background: var(--azure-haze); }

/* Dropdowns */
.dd { position: relative; }
.dd__btn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); color: var(--ink-soft);
  font-size: var(--fs-sm); font-weight: 600; transition: all var(--t-fast); }
.dd__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.dd__btn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.dd__count { display: inline-grid; place-items: center; min-width: 1.15rem; height: 1.15rem; padding: 0 4px;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; color: #0b0f1a; background: var(--azure); border-radius: 999px; }
.dd__caret { transition: transform var(--t-fast); }
.dd__caret.is-open { transform: rotate(90deg); }
.dd__pop { position: absolute; top: calc(100% + 6px); right: 0; z-index: 20; width: min(30rem, 82vw);
  padding: var(--s-3); border: 1px solid var(--line-2, var(--line-strong)); border-radius: var(--r-md);
  background: var(--glass-strong, var(--surface)); backdrop-filter: blur(16px); box-shadow: var(--shadow-lg); }
.dd__pop--sort { width: 13rem; padding: var(--s-2); }
.dd__pop-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--s-2);
  font-size: var(--fs-xs); color: var(--ink-soft); }
.dd__clear { font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); }
.dd__clear:hover { text-decoration: underline; }
.dd__genres { display: flex; flex-wrap: wrap; gap: var(--s-1); max-height: 15rem; overflow-y: auto; scrollbar-width: thin; }
.gchip { font-size: var(--fs-2xs); font-weight: 600; padding: 5px 10px; border-radius: var(--r-pill);
  color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.gchip:hover { color: var(--ink); border-color: var(--line-strong); }
.gchip.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure); }
.dd__opt { display: flex; align-items: center; gap: var(--s-2); width: 100%; padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); transition: all var(--t-fast); }
.dd__opt:hover { background: var(--surface-2, var(--surface)); color: var(--ink); }
.dd__opt.is-on { color: var(--azure-bright); }
.dd__opt-dot { width: 13px; height: 13px; }

.chips { display: flex; flex-wrap: wrap; gap: var(--s-1); margin-top: var(--s-3); }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: var(--fs-2xs); font-weight: 600;
  padding: 4px 10px; border-radius: var(--r-pill); color: var(--azure-bright); background: var(--azure-haze);
  border: 1px solid var(--azure); transition: all var(--t-fast); }
.chip:hover { background: color-mix(in srgb, var(--azure) 22%, transparent); }

/* ── Rejilla ── */
/* 6 por fila como las vistas principales (Biblioteca); degrada por breakpoints en pantallas menores. */
.grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: var(--s-4); }
.skeleton { aspect-ratio: 3 / 4.3; border-radius: var(--r-md); background: var(--surface); animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: .5 } 50% { opacity: .85 } }

.more { display: flex; justify-content: center; padding: var(--s-6) 0 var(--s-2); }
.more__btn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-6); border-radius: var(--r-pill);
  font-size: var(--fs-sm); font-weight: 650; color: var(--ink); border: 1px solid var(--line-strong); background: var(--surface);
  transition: all var(--t-fast); }
.more__btn:hover:not(:disabled) { border-color: var(--azure); color: var(--azure-bright); }
.more__btn:disabled { opacity: .6; }
.more__spin { width: 14px; height: 14px; border: 2px solid var(--line-strong); border-top-color: var(--azure); border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg) } }

.state { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) var(--s-4);
  color: var(--ink-faint); text-align: center; }
.state__btn { margin-top: var(--s-1); padding: var(--s-2) var(--s-4); border-radius: var(--r-md); font-size: var(--fs-sm);
  font-weight: 600; color: var(--ink); border: 1px solid var(--line-strong); transition: all var(--t-fast); }
.state__btn:hover { border-color: var(--azure); color: var(--azure-bright); }

@media (max-width: 1100px) { .grid { grid-template-columns: repeat(5, 1fr); } }
@media (max-width: 900px)  { .grid { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 720px)  { .grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 640px) {
  .disco { padding: 0 var(--s-4) var(--s-8); }
  .grid { grid-template-columns: repeat(2, 1fr); }
  .filters__spacer { display: none; }
  .dd__pop { right: auto; left: 0; }
}
@media (prefers-reduced-motion: reduce) { .dd__caret, .more__spin { transition: none; animation: none; } }
</style>
