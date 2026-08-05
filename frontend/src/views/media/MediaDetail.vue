<script setup>
/* Detalle de una serie o película, con la MISMA anatomía que `views/anime/AnimeDetail.vue`:
   hero con arte de fondo, póster con resplandor de su color dominante, barra de progreso,
   fila de acciones y pestañas (Episodios / Detalles).

   Lo que no aplica, no se copia: aquí no hay estado de seguimiento (Sonarr no lo tiene),
   ni franquicia, ni recomendaciones. Una película no tiene lista de episodios: enseña su
   ficha y un botón de reproducir.

   El reproductor es el NATIVO, reusado entero desde el store de anime; lo único propio es
   `progressKey` (dónde se guarda la posición). */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { formatBytes, mediaStatusLabel } from '@/lib/format'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import { useSubBatchStore } from '@/stores/subbatch'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import ReleasePicker from './ReleasePicker.vue'
import MediaCard from '@/components/media/MediaCard.vue'
import { imgProxy } from '@/lib/img'
import { coverRGB, vivid } from '@/lib/coverColor'
import { generos } from '@/lib/etiquetas'

const props = defineProps({ item: { type: Object, required: true } })
defineEmits(['back'])

const store = useMediaStore()
const ui = useUiStore()
const subbatch = useSubBatchStore()

// Traducción/búsqueda de subtítulos ES por LOTES (series). La ruta de Sonarr ya es /mnt/… (el
// stack servarr corre en WSL), así que ffprobe la abre directa: se pasa como `path`, sin mapeo.
function openSubBatch() {
  const item = props.item
  const titles = [item.title, item.original_title].filter(Boolean)
  const items = episodes.value
    .filter(e => e.has_file && e.path && e.season > 0)
    .map(e => ({ episode: e.num, season: e.season, path: e.path, title: e.title, titles }))
  if (!items.length) { ui.toast('No hay episodios descargados', 'info'); return }
  subbatch.openFor({ title: item.title, items })
}
const loading = ref(true)
const error = ref('')
const episodes = ref([])
const season = ref(null)
const tab = ref('eps')
const picker = ref(null)
const posterFailed = ref(false)
const artFailed = ref(false)

const isMovie = computed(() => props.item.kind === 'movie')

// «Vista» sale del progreso EN VIVO si existe (acabas de marcarla) y si no, del historial ya
// cargado: la ficha no puede contradecir a la rejilla que tienes detrás.
const pelicVista = computed(() => {
  const vivo = store.progressByKey[`movie:${props.item.id}`]
  if (vivo) return !!vivo.watched
  return store.watchState[`movie:${props.item.id}`] === 'seen'
})

/* Saga y recomendaciones — lo que en la ficha de anime hace el orden de franquicia.
   Se piden aparte de los episodios y en cuanto se abre la ficha: son TMDB (~375 ms medidos) y no
   deben retrasar la lista de episodios, que es a lo que has venido. */
const related = ref({ collection: null, recommendations: [] })
const relatedLoading = ref(false)
watch(() => props.item.tmdb_id, async (id) => {
  related.value = { collection: null, recommendations: [] }
  if (!id) return          // sin id de TMDB no hay nada que pedir: no es un fallo, es que Sonarr
  relatedLoading.value = true      // no lo tiene emparejado
  try {
    related.value = await api.get(`/api/media/related?kind=${props.item.kind}&tmdb_id=${id}`)
  } catch { /* la ficha vive sin esto: no se anuncia un fallo por un bloque accesorio */ }
  finally { relatedLoading.value = false }
}, { immediate: true })

// La obra que estás mirando también sale en su propia saga: se marca en vez de esconderla, que es
// lo que deja ver DÓNDE encaja («la segunda de tres»).
const esEsta = (p) => p.tmdb_id === props.item.tmdb_id

async function añadir(p) {
  if (p.already || esEsta(p)) return
  if (await store.addFromTmdb(p, p.kind)) p.already = true
}

/* Resplandor del póster en su color dominante — decorativo y sólo detrás del póster,
   nunca sobre texto ni controles (ver [[project_cover_ambient]]). */
const posterGlow = ref({})
watch(() => props.item.poster, async (cover) => {
  posterGlow.value = {}
  if (!cover) return
  const rgb = await coverRGB(imgProxy(cover))
  if (rgb) { const v = vivid(rgb); posterGlow.value = { '--pglow': `0 10px 44px rgba(${v.r}, ${v.g}, ${v.b}, .5)` } }
}, { immediate: true })

const seasons = computed(() => [...new Set(episodes.value.map(e => e.season))].sort((a, b) => a - b))
const shown = computed(() => episodes.value.filter(e => e.season === season.value))
const pct = computed(() => props.item.total ? Math.min(100, props.item.have / props.item.total * 100) : 0)

/* "Continuar viendo": el episodio a medias, y si no hay, el primero sin ver que esté en disco.
   Mismo criterio que anime — un episodio a medias manda sobre el siguiente sin empezar. */
const resumeEp = computed(() => {
  const eps = episodes.value.filter(e => e.has_file && e.season > 0)
  return eps.find(e => e.pos > 0 && !e.watched) || eps.find(e => !e.watched) || null
})

const meta = computed(() => [
  props.item.year && String(props.item.year),
  props.item.rating && `★ ${props.item.rating}`,
  props.item.certification,
  props.item.runtime && `${props.item.runtime} min`,
  props.item.network || props.item.studio,
].filter(Boolean))

async function load() {
  if (isMovie.value) { loading.value = false; return }
  loading.value = true; error.value = ''
  try {
    const d = await api.get(`/api/media/series/${props.item.id}/episodes`)
    episodes.value = d.episodes || []
    // Arrancar en la primera temporada REAL (la 0 son especiales) que tenga algo en disco.
    const withFile = episodes.value.filter(e => e.has_file && e.season > 0)
    season.value = (withFile[0] || episodes.value.find(e => e.season > 0) || episodes.value[0])?.season ?? 1
  } catch (e) {
    error.value = e?.body || e?.message || 'no se pudieron cargar los episodios'
  } finally { loading.value = false }
}
onMounted(load)

const epLabel = (ep) => `${ep.season}x${String(ep.num).padStart(2, '0')}`

// Abre el SELECTOR de torrents; nunca lanza una búsqueda automática (eso es cosa tuya).
function pickEpisode(ep) {
  picker.value = {
    kind: 'episode', id: ep.id, label: `${props.item.title} · ${epLabel(ep)}`,
    // Respaldo por si Sonarr no devuelve nada: término para el buscador crudo de Prowlarr.
    query: `${props.item.title} S${String(ep.season).padStart(2, '0')}E${String(ep.num).padStart(2, '0')}`,
  }
}

function pickSeason() {
  picker.value = {
    kind: 'season', id: props.item.id, season: season.value,
    label: `${props.item.title} · Temporada ${season.value}`,
    query: `${props.item.title} S${String(season.value).padStart(2, '0')}`,
  }
}

function pickMovie() {
  picker.value = { kind: 'movie', id: props.item.id, label: props.item.title,
                   query: `${props.item.title} ${props.item.year || ''}`.trim() }
}

// Dos preguntas separadas a propósito: quitar de la biblioteca y borrar 40 GB del disco no son
// la misma decisión, y sólo una de ellas es reversible.
async function remove() {
  if (!await ui.confirm({ title: 'Quitar de la biblioteca', body: `¿Quitar «${props.item.title}» de la biblioteca?`, confirmLabel: 'Quitar' })) return
  const withFiles = await ui.confirm({
    title: 'Archivos del disco', danger: true,
    body: '¿Borrar también los archivos del disco?\nBorrarlos no se puede deshacer; conservarlos deja el vídeo en su carpeta.',
    confirmLabel: 'Borrar archivos', cancelLabel: 'Conservarlos',
  })
  store.removeFromLibrary(props.item, { deleteFiles: withFiles })
}

/* Liberar espacio: se ofrece primero la temporada que estás mirando, que es lo que uno suele
   querer soltar. Aviso explícito del seeding — borrar el fichero rompe el torrent que lo siembra. */
async function freeSpace() {
  // En una película no hay temporadas que elegir: es un solo fichero.
  const soloTemp = !isMovie.value && seasons.value.length > 1 && await ui.confirm({
    title: 'Liberar espacio',
    body: `¿Borrar sólo los archivos de la temporada ${season.value}?`,
    confirmLabel: `Sólo la T${season.value}`, cancelLabel: 'La serie entera',
  })
  const alcance = isMovie.value
    ? 'el archivo'
    : (soloTemp ? `los archivos de la temporada ${season.value}` : 'los archivos de TODAS las temporadas')
  if (!await ui.confirm({
    title: 'Borrar archivos', danger: true,
    body: `Se borrará ${alcance} de «${props.item.title}».\n` +
          `${isMovie.value ? 'La película' : 'La serie'} y tu progreso se conservan; sólo se libera disco.\n` +
          'También se quita el torrent: si no, qBittorrent sigue sembrando esos mismos bytes y ' +
          'no se libera nada. Dejarás de sembrarlo.',
    confirmLabel: 'Borrar',
  })) return
  const d = await store.freeSpace(props.item, { season: soloTemp ? season.value : null })
  if (d?.deleted) load()
}

function play(ep) {
  if (!ep.has_file) return
  store.playEpisode(props.item, ep, episodes.value)
}

// El reproductor escribe el minuto en el store MIENTRAS reproduce (`notePlayback`), así que la
// fila refleja dónde te saliste en cuanto cierras — antes se releía con un setTimeout a ciegas.
function live(ep) {
  return store.progressByKey[`series:${props.item.id}:${ep.id}`] || null
}
function epPos(ep) { const l = live(ep); return l ? l.pos : (ep.pos || 0) }
function epWatched(ep) { const l = live(ep); return l ? l.watched : !!ep.watched }
</script>

<template>
  <div class="mdet">
    <!-- ── Hero ─────────────────────────────────────────────────────────── -->
    <header class="dhero">
      <div class="dhero__bg">
        <img v-if="item.banner && !artFailed" class="dhero__art" :src="imgProxy(item.banner)" alt=""
             @error="artFailed = true" />
        <div v-else-if="item.poster" class="dhero__art dhero__art--blur"
             :style="{ backgroundImage: `url('${imgProxy(item.poster, 260)}')` }" />
        <div class="dhero__shade" />
      </div>

      <button class="dhero__back" @click="$emit('back')">
        <Icon name="chevron" :size="16" /> Volver
      </button>

      <div class="dhero__inner">
        <img v-if="item.poster && !posterFailed" class="dhero__poster" :src="imgProxy(item.poster, 260)"
             :alt="item.title" :style="posterGlow" @error="posterFailed = true" />

        <div class="dhero__col">
          <span class="dhero__fmt">{{ isMovie ? 'PELÍCULA' : 'SERIE' }}</span>
          <h1 class="dhero__title">{{ item.title }}</h1>

          <p v-if="meta.length" class="dhero__meta">
            <span v-for="(m, i) in meta" :key="i">{{ m }}</span>
          </p>

          <div class="dhero__stats">
            <span class="dhero__count">
              <strong>{{ item.have }}</strong> / {{ item.total || '?' }}
              {{ isMovie ? 'archivo' : 'episodios' }}
              <span v-if="item.size" class="dhero__disk"><Icon name="folder" :size="12" /> {{ formatBytes(item.size) }}</span>
            </span>
            <div class="dhero__bar"><span :style="{ width: pct + '%' }" /></div>
          </div>

          <p v-if="item.overview" class="dhero__ov">{{ item.overview }}</p>

          <div class="dhero__mgmt">
            <button v-if="isMovie && item.have" class="mbtn mbtn--accent" @click="store.playMovie(item)">
              <Icon name="play" :size="14" /> Ver ahora
            </button>
            <button v-else-if="resumeEp" class="mbtn mbtn--accent" @click="play(resumeEp)">
              <Icon name="play" :size="14" />
              {{ resumeEp.pos > 0 ? 'Continuar' : 'Ver' }} {{ epLabel(resumeEp) }}
            </button>

            <!-- En una película el gesto vive aquí, porque no hay lista de episodios donde ponerlo. -->
            <button v-if="isMovie && item.have" class="mbtn" :class="{ 'is-on': pelicVista }"
                    @click="store.setWatched(`movie:${item.id}`, !pelicVista, item.runtime * 60,
                            { title: item.title, cover: item.poster })">
              <Icon name="check" :size="14" /> {{ pelicVista ? 'Vista' : 'Marcar vista' }}
            </button>

            <button class="mbtn" @click="isMovie ? pickMovie() : pickSeason()">
              <Icon name="download" :size="14" />
              {{ isMovie ? 'Elegir torrent' : 'Descargar temporada' }}
            </button>
            <button v-if="isMovie && item.have" class="mbtn" @click="store.movieSubs(item)">
              <Icon name="globe" :size="14" /> Subtítulos ES
            </button>
            <button v-else-if="!isMovie && item.have" class="mbtn" @click="openSubBatch"
                    data-tip="Buscar o traducir subtítulos en español de varios episodios">
              <Icon name="globe" :size="14" /> Subtítulos ES (lote)
            </button>
            <!-- Liberar espacio NO es eliminar: la serie y tu progreso se quedan, sólo se van
                 los archivos. Por eso vive separado del botón rojo. -->
            <button v-if="item.size" class="mbtn" @click="freeSpace">
              <Icon name="folder" :size="14" /> Liberar {{ formatBytes(item.size) }}
            </button>
            <button class="mbtn mbtn--danger" @click="remove">
              <Icon name="trash" :size="14" /> Quitar
            </button>
          </div>
        </div>
      </div>
    </header>

    <!-- Una película no tiene lista de episodios: el hero ya lo dice todo. -->
    <template v-if="!isMovie">
      <nav class="dtabs">
        <button class="dtabs__t" :class="{ 'is-active': tab === 'eps' }" @click="tab = 'eps'">Episodios</button>
        <button class="dtabs__t" :class="{ 'is-active': tab === 'info' }" @click="tab = 'info'">Detalles</button>
      </nav>

      <Spinner v-if="loading" />
      <ErrorState v-else-if="error" title="No se pudieron cargar los episodios."
                  :detail="error" @retry="load()" />

      <template v-else-if="tab === 'eps'">
        <div class="toolbar">
          <div class="filters">
            <button v-for="s in seasons" :key="s" class="pill" :class="{ 'is-active': s === season }"
                    @click="season = s">
              {{ s === 0 ? 'Especiales' : `Temporada ${s}` }}
              <span class="pill__n">{{ episodes.filter(e => e.season === s && e.has_file).length }}/{{ episodes.filter(e => e.season === s).length }}</span>
            </button>
          </div>
        </div>

        <ul class="eps">
          <li v-for="ep in shown" :key="ep.id" class="ep"
              :class="{ 'is-off': !ep.has_file, 'is-cur': resumeEp && ep.id === resumeEp.id }">
            <!-- Miniatura del episodio (screenshot de TVDB). Sin ella el listado era una
                 columna de texto; con ella se reconoce el capítulo de un vistazo, como en anime.
                 Si no hay imagen se cae al número, no a un hueco gris. -->
            <button class="ep__thumb" :disabled="!ep.has_file"
                    :data-tip="ep.has_file ? 'Reproducir' : 'No descargado'" @click="play(ep)">
              <img v-if="ep.still" :src="imgProxy(ep.still)" :alt="ep.title" loading="lazy" decoding="async" />
              <span v-else class="ep__thumbph">{{ epLabel(ep) }}</span>
              <span class="ep__thumbgo"><Icon :name="ep.has_file ? 'play' : 'download'" :size="16" /></span>
              <span v-if="epPos(ep) && ep.duration" class="ep__thumbbar">
                <i :style="{ width: Math.min(100, epPos(ep) / ep.duration * 100) + '%' }" />
              </span>
            </button>

            <div class="ep__main">
              <p class="ep__title">
                <b class="ep__n">{{ epLabel(ep) }}</b>
                {{ ep.title || 'Sin título' }}
                <Icon v-if="epWatched(ep)" name="check" :size="12" class="ep__seen" />
              </p>
              <p v-if="ep.overview" class="ep__ov">{{ ep.overview }}</p>
              <p class="ep__meta">
                <template v-if="ep.has_file">{{ ep.quality }} · {{ formatBytes(ep.size) }}</template>
                <template v-else>No descargado</template>
                <template v-if="epPos(ep)"> · reanudar en {{ Math.floor(epPos(ep) / 60) }} min</template>
              </p>
            </div>

            <div class="ep__acts">
              <!-- Marcar a mano: lo que ves fuera de la app, o el episodio que dejaste al 95 %,
                   no tienen otra forma de quedar bien registrados. Mismo gesto que en Mi Anime. -->
              <button class="ep__icon" :class="{ 'is-on': epWatched(ep) }"
                      :data-tip="epWatched(ep) ? 'Marcar como NO visto' : 'Marcar como visto'"
                      @click="store.setWatched(`series:${item.id}:${ep.id}`, !epWatched(ep), ep.duration,
                                               { title: item.title, cover: item.poster, episode: ep.num })">
                <Icon name="check" :size="13" />
              </button>
              <button v-if="ep.has_file" class="ep__icon" data-tip="Buscar subtítulos en español"
                      @click="store.openSubs(item, ep)">
                <Icon name="globe" :size="13" />
              </button>
              <button class="ep__icon" :data-tip="ep.has_file ? 'Descargar otra versión' : 'Elegir torrent'"
                      @click="pickEpisode(ep)">
                <Icon name="download" :size="13" />
              </button>
            </div>
          </li>
        </ul>
      </template>

      <div v-else class="info">
        <p v-if="item.overview" class="info__ov">{{ item.overview }}</p>
        <dl class="info__grid">
          <div v-if="item.genres?.length"><dt>Géneros</dt><dd>{{ generos(item.genres).join(' · ') }}</dd></div>
          <div v-if="item.network"><dt>Cadena</dt><dd>{{ item.network }}</dd></div>
          <div v-if="item.status"><dt>Estado</dt><dd>{{ mediaStatusLabel(item.status) }}</dd></div>
          <div v-if="item.runtime"><dt>Duración</dt><dd>{{ item.runtime }} min por episodio</dd></div>
          <div v-if="item.seasons"><dt>Temporadas</dt><dd>{{ item.seasons }}</dd></div>
          <div v-if="item.size"><dt>En disco</dt><dd>{{ formatBytes(item.size) }}</dd></div>
          <div v-if="item.path"><dt>Carpeta</dt><dd class="info__path">{{ item.path }}</dd></div>
        </dl>
      </div>
    </template>

    <!-- Fuera del `v-if="!isMovie"` a propósito: la saga es justo lo que una PELÍCULA necesita
         («las tres de Dune, en orden»), y ahí dentro no la vería nunca. -->
    <section v-if="related.collection?.items?.length" class="rel">
      <header class="rel__head">
        <h3 class="rel__title">{{ related.collection.name }}</h3>
        <span class="rel__hint">en orden de estreno</span>
      </header>
      <div class="rel__row">
        <MediaCard v-for="p in related.collection.items" :key="p.tmdb_id"
                   :class="{ 'is-current': esEsta(p) }"
                   :cover="p.poster" :title="p.title" kind-label="PELÍCULA"
                   :flag="esEsta(p) ? { tone: 'live', label: 'ESTÁS AQUÍ' }
                          : p.already ? { tone: 'soft', icon: 'check', label: 'LA TIENES' } : null"
                   :tags="[p.year ? String(p.year) : ''].filter(Boolean)"
                   :play-label="esEsta(p) ? '' : (p.already ? 'Ya la tienes' : 'Añadir')"
                   :play-icon="p.already ? 'check' : 'plus'" :play-done="!!p.already"
                   :play-busy="store.adding === p.tmdb_id" alt-label=""
                   @open="añadir(p)" @play="añadir(p)" />
      </div>
    </section>

    <section v-if="related.recommendations.length" class="rel">
      <header class="rel__head">
        <h3 class="rel__title">Si te gustó esto</h3>
        <span class="rel__hint">según TMDB</span>
      </header>
      <div class="rel__row">
        <MediaCard v-for="p in related.recommendations" :key="p.tmdb_id"
                   :cover="p.poster" :title="p.title"
                   :kind-label="p.kind === 'movie' ? 'PELÍCULA' : 'SERIE'"
                   :status="p.score ? { label: `★ ${p.score}`, color: 'var(--cyan)' } : null"
                   :flag="p.already ? { tone: 'soft', icon: 'check', label: 'LA TIENES' } : null"
                   :tags="[p.year ? String(p.year) : ''].filter(Boolean)"
                   :play-label="p.already ? 'Ya la tienes' : 'Añadir'"
                   :play-icon="p.already ? 'check' : 'plus'" :play-done="!!p.already"
                   :play-busy="store.adding === p.tmdb_id" alt-label=""
                   @open="añadir(p)" @play="añadir(p)" />
      </div>
    </section>

    <ReleasePicker v-if="picker" v-bind="picker" @close="picker = null" @grabbed="load" />
  </div>
</template>

<style scoped>
.mdet { display: block; }

/* ── Hero ──────────────────────────────────────────────────────────────── */
.dhero { position: relative; margin-bottom: var(--s-5); }
.dhero__bg { position: absolute; inset: 0; overflow: hidden; border-radius: 0 0 var(--r-lg) var(--r-lg); }
.dhero__art { width: 100%; height: 100%; object-fit: cover; object-position: center 22%; }
.dhero__art--blur { background-size: cover; background-position: center; filter: blur(38px) saturate(1.3); transform: scale(1.15); }
.dhero__shade { position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,.25) 0%, color-mix(in srgb, var(--base) 72%, transparent) 55%, var(--base) 100%); }

.dhero__back { position: relative; z-index: 2; display: inline-flex; align-items: center; gap: 4px;
  margin: var(--s-4) 0 0 var(--s-6); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm);
  background: color-mix(in srgb, var(--surface) 80%, transparent); border: 1px solid var(--line);
  color: var(--ink-soft); font-size: var(--fs-sm); cursor: pointer; backdrop-filter: blur(8px);
  transition: all var(--t-fast); }
.dhero__back:hover { color: var(--ink); border-color: var(--line-strong); }
.dhero__back :deep(svg) { transform: rotate(180deg); }

.dhero__inner { position: relative; z-index: 1; display: flex; gap: var(--s-6);
  padding: var(--s-5) var(--s-6) var(--s-6); align-items: flex-end; }
/* Destino del vuelo del póster desde la tarjeta (View Transition). Era el TERCER y último sitio
   donde faltaba: `MediaCard` ya marcaba el origen con `vtTag`, igual que en anime y manga. */
.dhero__poster { width: 12rem; flex: none; aspect-ratio: 2/3; object-fit: cover;
  border-radius: var(--r-md); box-shadow: var(--pglow, 0 10px 34px rgba(0,0,0,.55));
  view-transition-name: detail-poster; }
.dhero__col { min-width: 0; flex: 1; }
.dhero__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: .12em;
  color: var(--azure-bright); }
.dhero__title { margin: var(--s-2) 0 0; font-size: var(--fs-2xl); font-weight: 700; line-height: 1.1; }
.dhero__meta { display: flex; flex-wrap: wrap; gap: var(--s-3); margin: var(--s-2) 0 0;
  font-size: var(--fs-sm); color: var(--ink-soft); }
.dhero__meta span + span { position: relative; }
.dhero__meta span + span::before { content: '·'; position: absolute; left: calc(var(--s-3) * -.6); color: var(--ink-ghost); }

.dhero__stats { margin-top: var(--s-4); max-width: 26rem; }
.dhero__count { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-soft); }
.dhero__count strong { color: var(--ink); font-size: var(--fs-base); }
.dhero__disk { display: inline-flex; align-items: center; gap: 4px; margin-left: auto;
  font-size: var(--fs-xs); color: var(--ink-faint); }
.dhero__bar { height: 3px; margin-top: var(--s-2); border-radius: 2px; background: var(--surface-3); overflow: hidden; }
.dhero__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow);
  transition: width .5s var(--ease-silk); }

.dhero__ov { margin: var(--s-4) 0 0; max-width: 52rem; font-size: var(--fs-sm); line-height: 1.6;
  color: var(--ink-soft); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }

.dhero__mgmt { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-4); }
.mbtn { display: inline-flex; align-items: center; gap: 0.3125rem; padding: var(--s-2) var(--s-4);
  border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line);
  color: var(--ink-soft); font-size: var(--fs-sm); cursor: pointer; transition: all var(--t-fast); }
.mbtn:hover { color: var(--ink); border-color: var(--line-strong); transform: translateY(-1px); }
.mbtn--accent { background: var(--azure); border-color: transparent; color: #fff; font-weight: 600; }
.mbtn--accent:hover { background: var(--azure-bright); box-shadow: var(--glow-azure); color: #fff; }
.mbtn--danger:hover { color: var(--danger); border-color: var(--danger); }

/* ── Pestañas ──────────────────────────────────────────────────────────── */
.dtabs { display: flex; gap: var(--s-5); padding: 0 var(--s-6); margin-bottom: var(--s-4);
  border-bottom: 1px solid var(--line); }
.dtabs__t { position: relative; padding: var(--s-3) 0; background: none; border: 0; cursor: pointer;
  color: var(--ink-faint); font-size: var(--fs-sm); font-weight: 600; transition: color var(--t-fast); }
.dtabs__t:hover { color: var(--ink-soft); }
.dtabs__t.is-active { color: var(--ink); }
.dtabs__t.is-active::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px;
  background: var(--azure-bright); border-radius: 2px; }

.toolbar { padding: 0 var(--s-6); }

/* ── Episodios ─────────────────────────────────────────────────────────── */
.eps { list-style: none; margin: 0; padding: 0 var(--s-6) var(--s-8);
  display: flex; flex-direction: column; gap: var(--s-2); }
.ep { display: flex; align-items: flex-start; gap: var(--s-3); padding: var(--s-3) var(--s-4);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  transition: border-color var(--t-fast), transform var(--t-fast); }
.ep:hover { border-color: var(--line-strong); transform: translateX(2px); }
.ep.is-off { opacity: .62; }
/* El que retomarías se distingue por un filo azul, no por un texto más: se ve de un vistazo. */
.ep.is-cur { border-color: color-mix(in srgb, var(--azure) 55%, transparent);
  box-shadow: inset 3px 0 0 var(--azure-bright); }

/* Miniatura 16:9: es el ancla visual de la fila y a la vez el botón de reproducir. */
.ep__thumb { position: relative; flex: none; width: 9.5rem; aspect-ratio: 16/9; padding: 0;
  border: 1px solid var(--line); border-radius: var(--r-sm); overflow: hidden;
  background: var(--surface-2); cursor: pointer; transition: all var(--t-fast); }
.ep__thumb img { width: 100%; height: 100%; object-fit: cover; display: block;
  transition: transform var(--t-base) var(--ease-silk); }
.ep__thumb:hover:not(:disabled) { border-color: var(--azure); }
.ep__thumb:hover:not(:disabled) img { transform: scale(1.06); }
.ep__thumb:disabled { cursor: default; opacity: .8; }
.ep__thumbph { display: grid; place-items: center; height: 100%; font-family: var(--font-mono);
  font-size: var(--fs-sm); color: var(--ink-ghost); }
.ep__thumbgo { position: absolute; inset: 0; display: grid; place-items: center; color: #fff;
  background: rgba(0,0,0,.42); opacity: 0; transition: opacity var(--t-fast); }
.ep__thumb:hover .ep__thumbgo, .ep__thumb:disabled .ep__thumbgo { opacity: 1; }
.ep__thumb:disabled .ep__thumbgo { background: rgba(0,0,0,.55); color: var(--ink-faint); }
.ep__thumbbar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.5); }
.ep__thumbbar i { display: block; height: 100%; background: var(--azure-bright); }

.ep__main { min-width: 0; flex: 1; }
.ep__title { margin: 0; font-size: var(--fs-sm); font-weight: 600; display: flex; align-items: center; gap: var(--s-2); }
.ep__n { flex: none; font-family: var(--font-mono); color: var(--azure-bright); font-weight: 700; }
.ep__seen { color: var(--jade); flex: none; }
.ep__ov { margin: .25rem 0 0; font-size: var(--fs-xs); line-height: 1.5; color: var(--ink-faint);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ep__meta { margin: .3rem 0 0; font-size: var(--fs-xs); color: var(--ink-faint); }

.ep__acts { display: flex; gap: var(--s-2); flex: none; }
.ep__icon { display: grid; place-items: center; width: 2.1rem; height: 2.1rem; border-radius: var(--r-sm);
  background: var(--surface-2); border: 1px solid var(--line); color: var(--ink-faint);
  cursor: pointer; transition: all var(--t-fast); }
.ep__icon:hover { color: var(--azure-bright); border-color: var(--azure); }
/* Marcado como visto: el botón ES el estado, así que se queda encendido en vez de cambiar de
   icono. En jade, el mismo color con el que la fila ya marca lo terminado. */
.ep__icon.is-on { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 50%, transparent); }
.mbtn.is-on { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 50%, transparent); }

/* ── Saga y recomendaciones ────────────────────────────────────────────── */
.rel { padding: 0 var(--s-6) var(--s-7); }
.rel__head { display: flex; align-items: baseline; gap: var(--s-3); margin-bottom: var(--s-4); }
.rel__title { font-family: var(--font-display); font-size: var(--fs-lg); }
.rel__hint { font-size: var(--fs-xs); color: var(--ink-faint); }
.rel__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-3); }
.rel__row > * { flex: 0 0 10rem; }
/* La de la ficha se distingue del resto de la saga por un filo, no por quitarla de la fila:
   verla en su sitio es lo que dice si vas por la primera o por la última. */
.rel__row > .is-current { outline: 1px solid var(--azure); outline-offset: 3px; border-radius: var(--r-md); }

/* ── Detalles ──────────────────────────────────────────────────────────── */
.info { padding: 0 var(--s-6) var(--s-8); max-width: 60rem; }
.info__ov { margin: 0 0 var(--s-5); font-size: var(--fs-sm); line-height: 1.7; color: var(--ink-soft); }
.info__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: var(--s-4); margin: 0; }
.info__grid dt { font-size: var(--fs-2xs); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-faint); }
.info__grid dd { margin: .25rem 0 0; font-size: var(--fs-sm); color: var(--ink); }
.info__path { font-family: var(--font-mono); font-size: var(--fs-xs); word-break: break-all; color: var(--ink-soft); }

@media (max-width: 780px) {
  .dhero__inner { flex-direction: column; align-items: flex-start; }
  .dhero__poster { width: 8rem; }
}

/* La fila de episodio era rígida: miniatura de 9.5rem + texto + acciones no caben en ventana
   estrecha y la lista se desbordaba en horizontal. La miniatura encoge y las acciones bajan. */
@media (max-width: 640px) {
  .eps, .info, .toolbar { padding-left: var(--s-4); padding-right: var(--s-4); }
  .ep { flex-wrap: wrap; }
  .ep__thumb { width: 6.5rem; }
  .ep__acts { width: 100%; justify-content: flex-end; }
}
</style>
