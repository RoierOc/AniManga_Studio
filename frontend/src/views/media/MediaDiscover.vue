<script setup>
/* Descubrir (TMDB). Añadir desde aquí traduce el id de TMDB al de Sonarr/Radarr vía `/resolve`:
   si la traducción no es inequívoca NO se elige por el usuario, se le enseñan los candidatos. */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import MediaCard from '@/components/media/MediaCard.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useMediaStore()
const ui = useUiStore()
const candidates = ref(null)

const LISTS = [
  { id: 'trending', label: 'Tendencias' },
  { id: 'popular', label: 'Populares' },
  { id: 'top', label: 'Mejor valoradas' },
]

onMounted(() => { if (!store.discover.length) store.loadDiscover() })

function pick(list) { store.discoverList = list; store.loadDiscover() }
function pickKind(k) {
  if (store.discoverKind === k) return
  // Los ids de género difieren entre tv y movie: al cambiar de tipo el género elegido deja de
  // ser válido, así que se resetea a "Todos".
  store.discoverKind = k; store.discoverGenre = ''; store.loadDiscover()
}
function pickGenre(id) { store.discoverGenre = store.discoverGenre === id ? '' : id; store.loadDiscover() }

/* Scroll infinito: descubrir no debería toparse con un muro a las 20 primeras. `rootMargin`
 * dispara la carga antes del borde, así la rejilla crece sin que llegues a ver el final.
 * Mismo patrón que el Descubrir de manga. */
const sentinel = ref(null)
let io = null
watch(sentinel, (el, prev) => {
  if (!('IntersectionObserver' in window)) return   // sin soporte queda el botón
  if (prev && io) io.unobserve(prev)
  if (!el) return
  io = io || new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) store.loadMoreDiscover()
  }, { rootMargin: '600px' })
  io.observe(el)
})
onBeforeUnmount(() => io?.disconnect())

async function add(it) {
  store.adding = it.tmdb_id
  try {
    const t = it.title_original || it.title
    const r = await api.get(`/api/media/resolve?kind=${store.discoverKind}` +
      `&title=${encodeURIComponent(t)}&year=${it.year || ''}`)
    if (!r.match) {
      candidates.value = { term: t, list: r.candidates || [] }
      ui.toast('Varias coincidencias: elige cuál es', 'info')
      return
    }
    if (r.match.already) { ui.toast('Ya la tienes', 'info'); it.already = true; return }
    const ok = await store.add(r.match.ext_id, store.discoverKind)
    if (ok) it.already = true
  } catch (e) {
    ui.toast(`No se pudo añadir: ${e?.body || e?.message || ''}`, 'error')
  } finally { store.adding = '' }
}
</script>

<template>
  <div class="mdisc">
    <header class="mdisc__head">
      <p class="eyebrow"><span class="tick" /> QUÉ VER</p>
    </header>

    <div class="toolbar">
      <div class="filters">
        <button v-for="l in LISTS" :key="l.id" class="pill" :class="{ 'is-active': store.discoverList === l.id }"
                @click="pick(l.id)">{{ l.label }}</button>
      </div>
      <div class="toolbar__right">
        <div class="sorts">
          <button class="sort" :class="{ 'is-active': store.discoverKind === 'series' }" @click="pickKind('series')">Series</button>
          <button class="sort" :class="{ 'is-active': store.discoverKind === 'movie' }" @click="pickKind('movie')">Películas</button>
        </div>
      </div>
    </div>

    <!-- Géneros: la segunda dimensión de descubrimiento. "Todos" respeta la lista de arriba;
         un género la convierte en un /discover ordenado por ese mismo criterio. -->
    <div v-if="store.genres[store.discoverKind]?.length" class="mdisc__genres">
      <button class="gpill" :class="{ 'is-active': !store.discoverGenre }" @click="pickGenre('')">Todos</button>
      <button v-for="g in store.genres[store.discoverKind]" :key="g.id"
              class="gpill" :class="{ 'is-active': store.discoverGenre === String(g.id) }"
              @click="pickGenre(String(g.id))">{{ g.name }}</button>
    </div>

    <!-- Candidatos: aparece cuando el título de TMDB no casa con uno solo de Sonarr/Radarr. -->
    <section v-if="candidates" class="mdisc__cands">
      <p class="mdisc__candsh">
        Varias coincidencias para <b>{{ candidates.term }}</b> — elige la correcta:
        <button class="mdisc__close" @click="candidates = null">Cerrar</button>
      </p>
      <ul>
        <li v-for="c in candidates.list.slice(0, 8)" :key="c.ext_id">
          <span>{{ c.title }} <em>{{ c.year || '—' }}</em></span>
          <button :disabled="c.already" @click="store.add(c.ext_id, store.discoverKind).then(() => candidates = null)">
            {{ c.already ? 'Ya la tienes' : 'Añadir' }}
          </button>
        </li>
      </ul>
    </section>

    <p v-if="store.discoverSkipped" class="mdisc__note">
      {{ store.discoverSkipped }} anime{{ store.discoverSkipped > 1 ? 's' : '' }} fuera de la lista —
      tienen su propia sección en <b>Anime</b>.
    </p>

    <Spinner v-if="store.discoverLoading" />
    <EmptyState v-else-if="store.discoverErr" icon="alert" title="No se pudo cargar TMDB" :hint="store.discoverErr" />

    <template v-else>
      <div class="mdisc__grid stagger">
        <MediaCard v-for="d in store.discover" :key="d.tmdb_id"
                   :cover="d.poster" :title="d.title"
                   :kind-label="store.discoverKind === 'movie' ? 'PELÍCULA' : 'SERIE'"
                   :status="d.score ? { label: `★ ${d.score}`, color: 'var(--cyan)' } : null"
                   :tags="[d.year ? String(d.year) : ''].filter(Boolean)"
                   :play-label="d.already ? 'Añadida' : (store.adding === d.tmdb_id ? 'Añadiendo…' : 'Añadir')"
                   :play-icon="d.already ? 'check' : (store.adding === d.tmdb_id ? 'refresh' : 'plus')"
                   :play-done="!!d.already" :play-busy="store.adding === d.tmdb_id"
                   alt-label=""
                   @open="add(d)" @play="add(d)" />
      </div>
      <!-- Centinela: al asomar por el borde, pide la página siguiente sola. El botón queda de
           respaldo si no hay IntersectionObserver o si una página falló. -->
      <div ref="sentinel" class="mdisc__more">
        <Spinner v-if="store.discoverLoadingMore" :size="18" />
        <button v-else-if="store.discoverHasMore" class="mdisc__morebtn" @click="store.loadMoreDiscover()">
          Cargar más
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.mdisc { display: block; padding: var(--s-5) var(--s-6) var(--s-8); }
.mdisc__head { margin-bottom: var(--s-4); }
.mdisc__note { font-size: var(--fs-xs); color: var(--ink-faint); margin: 0 0 var(--s-3); }
.mdisc__genres { display: flex; flex-wrap: wrap; gap: var(--s-2); margin: 0 0 var(--s-4); }
.gpill { padding: var(--s-1) var(--s-3); border-radius: var(--r-pill); font-size: var(--fs-xs);
  font-weight: 600; color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line);
  cursor: pointer; transition: all var(--t-fast); }
.gpill:hover { color: var(--ink); border-color: var(--line-strong); }
.gpill.is-active { color: #fff; background: var(--azure); border-color: transparent; }
.mdisc__grid { display: grid; gap: var(--s-5); grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); }
.mdisc__more { display: grid; place-items: center; min-height: 3.5rem; margin-top: var(--s-5); }
.mdisc__morebtn { padding: var(--s-3) var(--s-6); border-radius: var(--r-pill); font-size: var(--fs-sm);
  font-weight: 600; color: var(--ink); background: var(--surface); border: 1px solid var(--line);
  cursor: pointer; transition: all var(--t-fast); }
.mdisc__morebtn:hover { color: #fff; border-color: var(--azure); background: var(--azure-haze); }
.mdisc__add { width: 100%; margin-top: var(--s-2); padding: var(--s-2); border-radius: var(--r-sm);
  background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); font: inherit;
  font-size: var(--fs-xs); font-weight: 600; cursor: pointer; transition: all var(--t-fast); }
.mdisc__add:hover:not(:disabled) { border-color: var(--azure); color: var(--azure-bright); }
.mdisc__add:disabled { opacity: .55; cursor: default; }
.mdisc__cands { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  padding: var(--s-3); margin-bottom: var(--s-5); }
.mdisc__candsh { margin: 0 0 var(--s-2); font-size: var(--fs-sm); color: var(--ink-soft); }
.mdisc__close { margin-left: var(--s-3); background: none; border: 0; color: var(--ink-faint);
  cursor: pointer; font: inherit; font-size: var(--fs-xs); text-decoration: underline; }
.mdisc__cands ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.mdisc__cands li { display: flex; justify-content: space-between; align-items: center; gap: var(--s-3);
  padding: var(--s-2); background: var(--surface-2); border-radius: var(--r-sm); font-size: var(--fs-sm); }
.mdisc__cands em { color: var(--ink-faint); font-style: normal; }
.mdisc__cands button { background: var(--azure); color: #fff; border: 0; border-radius: var(--r-sm);
  padding: 4px 0.625rem; font: inherit; font-size: var(--fs-xs); cursor: pointer; }
.mdisc__cands button:disabled { background: var(--surface-3); color: var(--ink-faint); cursor: default; }
</style>
