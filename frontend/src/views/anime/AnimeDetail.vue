<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { useUiStore } from '@/stores/ui'
import { api } from '@/lib/api'
import { ANIME_STATUS, animeFormatLabel, animeEpLabel, batchInfo, fmtCountdown, nextUnwatchedEp, animeEpisodeKey } from '@/lib/anime'
import { imgProxy, imgThumb } from '@/lib/img'
import { coverRGB, vivid } from '@/lib/coverColor'
import { formatBytes } from '@/lib/format'
import { useMultiSelect } from '@/lib/useMultiSelect'
import CounterpartRow from '@/components/media/CounterpartRow.vue'
import EpisodeCard from '@/components/anime/EpisodeCard.vue'
import EpisodeRow from '@/components/anime/EpisodeRow.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import FolderPicker from '@/components/ui/FolderPicker.vue'

import { useSubBatchStore } from '@/stores/subbatch'
import { useAnimeUpscaleStore } from '@/stores/animeUpscale'
import { generos } from '@/lib/etiquetas'

const store = useAnimeStore()
const ui = useUiStore()
const subbatch = useSubBatchStore()
const anime = computed(() => store.detail)

// Una ficha abierta desde AniList conserva el preview al añadirse. Cuando el backfill ya haya
// resuelto TMDB en la entrada de biblioteca, carga sus stills bajo el mismo id del preview.
const libraryEpisodeMeta = computed(() => {
  const a = anime.value
  return a?.al_id == null ? null : store.library.find(x => String(x.al_id) === String(a.al_id))?.tmdb_id || null
})
watch(() => [anime.value?.id, anime.value?.tmdb_id, libraryEpisodeMeta.value], ([id, ownTmdb, libraryTmdb]) => {
  const a = anime.value
  const tmdbId = ownTmdb || libraryTmdb
  if (id && tmdbId && (store.epMeta[id] === undefined || store.epMetaNeedsTmdb[id]))
    store.loadEpMeta({ ...a, tmdb_id: tmdbId })
}, { immediate: true })

/* Cruza al otro lado de la app: del anime a su manga.
   · Si YA lo tienes, abre su ficha de la biblioteca — la que tiene los capítulos y el lector.
   · Si no, abre la ficha de MangaDex encima, con su botón de añadir, SIN sacarte de aquí. Antes
     esto llamaba a `ui.goto('library')` y no pasaba nada visible: `goto` limpia el manga abierto
     dentro de su propia View Transition, así que borraba justo lo que se acababa de abrir. */
async function abrirManga(c) {
  if (c.in_library) {
    const { useMangaStore } = await import('@/stores/manga')
    ui.goto('library')
    // Tras el cambio de vista, no dentro: `goto` hace `current = null` en su callback.
    await nextTick()
    useMangaStore().openFromWork({ title: c.title, cover: c.cover, ids: { anilist: c.al_id } })
    return
  }
  const { useMangadexStore } = await import('@/stores/mangadex')
  useMangadexStore().openByTitle(c.title, c.titles || [])
}

// Abre el modal de traducción por LOTES con los episodios que tienen archivo resoluble.
function openSubBatch() {
  const a = anime.value
  if (!a) return
  const titles = [a.title, a.title_romaji].filter(Boolean)
  const eps = [...mainEps.value, ...specials.value].filter(e => e.in_local || e.info_hash)
  const items = eps.map(e => ({
    episode: e.num, season: e.season || 1, title: e.title || '',
    anime_id: a.id, info_hash: e.info_hash || '', local_path: e.local_path || '',
    relative_path: e.relative_path || '', episode_key: animeEpisodeKey(e),
    ep_type: e.ep_type || 'episode', titles,
  }))
  subbatch.openFor({ title: a.title, items })
}

// Resplandor del póster en su color dominante (solo decorativo, detrás del póster).
// El hero ya aporta el ambiente de fondo (banner); esto le da identidad al póster.
const posterGlow = ref({})
watch(() => store.detail?.cover, async (cover) => {
  posterGlow.value = {}
  if (!cover) return
  // Del micro-thumb de 28 px, no de la portada entera: `coverRGB` reduce a 10×10 para
  // promediar, así que bajar 91 KB para eso era tirar 90. Y es la MISMA url que ya usa el
  // blur-up → sale de la caché del navegador, sin una sola petición extra.
  const rgb = await coverRGB(imgThumb(cover))
  if (rgb) { const v = vivid(rgb); posterGlow.value = { '--pglow': `0 10px 44px rgba(${v.r}, ${v.g}, ${v.b}, .5)` } }
}, { immediate: true })
// Preview = anime no-biblioteca (temporada/recomendación/estrenos). Solo "Agregar" + Torrents.
const isPreview = computed(() => !!store.previewAnime)
const inLibrary = computed(() => store.isInLibrary(anime.value))

// Crunchyroll-style hero: TMDB backdrop → AniList banner → blurred cover as last resort.
// banner_detail (set via the picker's "Fondo de esta página" tab) overrides the
// Home-hero banner just for this page, falling back to it when unset so existing
// entries keep working unchanged. If a tier fails to load (broken URL, blocked
// domain) heroIdx advances to the next one instead of leaving the hero blank.
const heroIdx = ref(0)
const heroTiers = computed(() => {
  const a = anime.value
  return a ? [a.banner_detail || a.banner, a.cover_xl, a.cover].filter(Boolean).map(url => imgProxy(url)) : []
})
const heroImg = computed(() => heroTiers.value[heroIdx.value] || '')
const hasBanner = computed(() => heroIdx.value <= 1 && !!((anime.value?.banner_detail || anime.value?.banner) || anime.value?.cover_xl))
function onHeroError() { if (heroIdx.value < heroTiers.value.length - 1) heroIdx.value++ }

// Falls back to the text title when no logo is cached, or the cached logo URL fails to load.
const logoFailed = ref(false)
const hasLogo = computed(() => !!anime.value?.logo && !logoFailed.value)
const posterFailed = ref(false)
watch(() => anime.value?.id, () => { heroIdx.value = 0; logoFailed.value = false; posterFailed.value = false; tab.value = 'eps' })
watch(() => anime.value?.cover_rev, () => { heroIdx.value = 0; posterFailed.value = false })

// Pestañas estilo Crunchyroll bajo el hero
const tab = ref('eps')

// openDetail() always pushes one history entry, so back consumes it and runs the
// guarded restore. Fall back to a direct close if there's no app history.
// Close the detail and stay on the anime view (no history jump); back/forward still work.
/* Retrocede de verdad, no reescribe la entrada actual. Con `replaceNav` la ficha se BORRABA del
   historial: "Volver" te devolvía a la biblioteca pero el siguiente "atrás" del ratón saltaba dos
   escalones (a la vista anterior a la ficha) y "adelante" ya no podía reabrirla. */
function goBack() { useUiStore().back(() => store.closeDetail()) }

const batch = computed(() => batchInfo(anime.value?.episodes || []))
const realEps = computed(() =>
  (anime.value?.episodes || []).filter(e => e.num !== 0 && e.ep_type !== 'special').sort((a, b) => a.num - b.num)
)
// Generate placeholder episodes when none are downloaded but total is known
const placeholders = computed(() => {
  if (realEps.value.length > 0 || !anime.value) return []
  const t = anime.value.total_episodes || 0
  if (t <= 0) return []
  return Array.from({ length: t }, (_, i) => ({ num: i + 1, ep_type: 'episode', in_local: false, in_qbt: false, watched: false }))
})
const mainEps = computed(() => realEps.value.length ? realEps.value : placeholders.value)
const specials = computed(() => (anime.value?.episodes || []).filter(e => e.ep_type === 'special'))

/* ── Selección por lote de episodios ────────────────────────────────────────────────────────
 * Liberar espacio de 6 episodios ya vistos eran 6 gestos idénticos. El mecanismo (shift+clic y
 * clic-arrastre) es el mismo que la lista de capítulos de manga, ahora compartido.
 * El orden que se le pasa es el VISIBLE (principales y luego especiales) para que el rango de
 * shift+clic siga lo que el usuario tiene delante. Sólo entra lo que está en disco: la única
 * acción de lote borra archivos, y marcar algo que no se puede borrar sería mentir. */
const selectableEps = computed(() =>
  [...mainEps.value, ...specials.value].filter(e => e.in_local))
const epSel = useMultiSelect(() => selectableEps.value.map(e => animeEpisodeKey(e)))
const selectedEps = computed(() => selectableEps.value.filter(e => epSel.has(animeEpisodeKey(e))))
const selectedBytes = computed(() => selectedEps.value.reduce((n, e) => n + (e.size || 0), 0))
// Cambiar de serie no debe arrastrar la selección de la anterior.
watch(() => anime.value?.id, () => epSel.clear())

async function deleteSelectedEps() {
  const eps = selectedEps.value
  if (!eps.length) return
  const ok = await ui.confirm({
    title: `¿Borrar ${eps.length} episodio(s)?`,
    body: `Se eliminan los archivos del disco${selectedBytes.value ? ` (${formatBytes(selectedBytes.value)})` : ''}.\nLa serie sigue en tu biblioteca; podrás volver a descargarlos.`,
    confirmLabel: 'Borrar archivos', danger: true,
  })
  if (!ok) return
  await store.deleteEpisodes(anime.value, eps)
  epSel.clear()
}

const total = computed(() => anime.value?.total_episodes || 0)
const done = computed(() => anime.value?.downloaded_count || 0)
const pct = computed(() => total.value ? Math.min(100, done.value / total.value * 100) : 0)
// Lo que la barra parecía decir y no decía. `done` son episodios DESCARGADOS, así que en una serie
// bajada entera salía llena y con el 100 % al lado justo mientras el botón ofrecía «Continuar · Ep 5».
// Ahora la barra lleva las dos capas: descargado en tenue (lo que tienes) y visto en sólido (por
// dónde vas), que es lo que uno busca al mirar una barra de progreso.
const vistos = computed(() => (anime.value?.episodes || [])
  .filter(e => e.num > 0 && e.ep_type !== 'special' && e.watched).length)
const pctVistos = computed(() => total.value ? Math.min(100, vistos.value / total.value * 100) : 0)
const diskSize = computed(() => anime.value?.disk_size || 0)

// "Continuar viendo": episodio en progreso, si no el próximo sin ver (ambos reproducibles).
// Sólo en biblioteca (los preview no tienen episodios descargados).
const resumeEp = computed(() => {
  if (isPreview.value || !anime.value) return null
  const eps = (anime.value.episodes || [])
  const inProg = eps.find(e => e.num > 0 && e.ep_type !== 'special' && e.resume_pos > 0 && !e.watched
    && (e.in_local || (e.in_qbt && e.progress >= 100)))
  return inProg || nextUnwatchedEp(anime.value)
})
const resumePct = computed(() => {
  const e = resumeEp.value
  if (!e?.resume_pos || !e?.duration) return 0
  return Math.min(100, e.resume_pos / e.duration * 100)
})

// Vista de episodios: cuadrícula (actual) ⇄ lista. Persiste la preferencia.
const epView = ref(localStorage.getItem('anime-epview') || 'grid')
function setEpView(v) { epView.value = v; localStorage.setItem('anime-epview', v) }
const isCurrent = (ep) => !!resumeEp.value && animeEpisodeKey(ep) === animeEpisodeKey(resumeEp.value) && ep.ep_type !== 'special'

const countdown = computed(() => {
  const na = store.nextAiring[anime.value?.al_id]
  if (!na?.airing_at) return null
  const c = fmtCountdown(na.airing_at, store.nowSec)
  return c ? { ...c, episode: na.episode } : null
})

const alId = computed(() => anime.value?.al_id)
const recs = computed(() => store.recs[alId.value] || [])
const tags = computed(() => store.tags[alId.value] || [])
// Umbrales del explorador de tags. Saltos de 10 desde 70: por debajo el tag deja de discriminar
// (AniList ya descarta lo que baja de 60) y por encima de 90 apenas quedan obras.
const TAG_RANKS = [0, 70, 80, 90]
const stacks = computed(() => store.stacks[alId.value] || [])
const franchise = computed(() => store.franchise[alId.value] || [])
const malUrl = computed(() => store.malUrls[alId.value])

const FMT_LABEL = { TV: 'TV', TV_SHORT: 'TV', MOVIE: 'Película', OVA: 'OVA', ONA: 'ONA', SPECIAL: 'Especial', MUSIC: 'Música' }
const fmtLabel = (f) => FMT_LABEL[f] || f || ''

const TABS = computed(() => [
  { key: 'eps', label: 'Episodios' },
  { key: 'info', label: 'Detalles' },
  { key: 'rel', label: 'Relacionados', badge: recs.value.length + stacks.value.length + (franchise.value.length > 1 ? franchise.value.length : 0) },
])

// Sinopsis completa (endpoint /synopsis) con la recortada de library como fallback
const synopsis = computed(() => store.fullSyn[alId.value] || anime.value?.synopsis || '')

/* ── Preview mudo del hero (estilo Netflix, clip local generado por el backend) ── */
const previewUrl = ref('')
const previewOn = ref(false)       // el vídeo ya reproduce → fade-in sobre el arte
const previewRef = ref(null)
let previewTimer = null
function schedulePreview() {
  clearTimeout(previewTimer)
  previewUrl.value = ''
  previewOn.value = false
  if (isPreview.value) return      // sin episodios descargados no hay clip
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  previewTimer = setTimeout(loadHeroPreview, 2500)
}
async function loadHeroPreview() {
  const a = anime.value
  const e = (a?.episodes || []).find(x => x.num > 0 && x.ep_type !== 'special'
    && (x.in_local || (x.in_qbt && x.progress >= 100)))
  if (!a || !e) return
  const base = e.in_local
    ? { anime_id: a.id, episode: e.num, local_path: e.local_path,
        info_hash: e.info_hash || '', relative_path: e.relative_path || '' }
    : { anime_id: a.id, episode: e.num, info_hash: e.info_hash }
  try {
    const d = await api.post('/api/anime/preview', base)
    if (d?.url && anime.value?.id === a.id) previewUrl.value = d.url
  } catch (_) {}
}
// pausar el clip mientras el player está abierto (misma vista debajo)
watch(() => store.player, (open) => {
  const v = previewRef.value
  if (!v) return
  if (open) v.pause()
  else v.play().catch(() => {})
})
onMounted(schedulePreview)
// Estado del horneado Anime4K: se pide una vez al abrir la ficha; el store sondea solo si hay algo.
const a4kStore = useAnimeUpscaleStore()
onMounted(() => a4kStore.refrescar())
watch(() => anime.value?.id, schedulePreview)
onBeforeUnmount(() => clearTimeout(previewTimer))

// ── Selector de estado (Viendo / Por ver / …) — desplegable propio ──────────
// Reemplaza el <select> nativo (menú del SO, feo y fuera de estilo) por un menú
// con la estética de la app. Cierra al elegir, con Escape o clic fuera.
const statusOpen = ref(false)
const statusRef = ref(null)
const curStatus = computed(() => ANIME_STATUS[anime.value?.status] || null)
function pickStatus(k) { store.setStatus(anime.value, k); statusOpen.value = false }
// Mantenimiento (portada, enlazar, borrar, eliminar) tras «⋯»: eran 5 botones del mismo peso que
// las acciones que sí usas a diario, con lo destructivo a un pixel de lo cosmético.
const mgmtOpen = ref(false)
const mgmtRef = ref(null)
const localFolderPicker = ref(false)
function mgmt(fn) { mgmtOpen.value = false; fn() }
function onDocClick(e) {
  if (statusRef.value && !statusRef.value.contains(e.target)) statusOpen.value = false
  if (mgmtRef.value && !mgmtRef.value.contains(e.target)) mgmtOpen.value = false
}
function onDocKey(e) { if (e.key === 'Escape') { statusOpen.value = false; mgmtOpen.value = false } }
onMounted(() => { document.addEventListener('click', onDocClick); document.addEventListener('keydown', onDocKey) })
onBeforeUnmount(() => { document.removeEventListener('click', onDocClick); document.removeEventListener('keydown', onDocKey) })
// Cerrar si se cambia de anime.
watch(() => anime.value?.id, () => { statusOpen.value = false; mgmtOpen.value = false })

const PICKER_TABS = [
  { key: 'cover', label: 'Portada' },
  { key: 'banner_detail', label: 'Fondo de esta página' },
  { key: 'banner', label: 'Fondo en Inicio' },
]
const activeTab = computed(() => store.coverPicker?.tabs[store.coverPicker.tab])
</script>

<template>
  <div v-if="anime" class="detail">
    <!-- La ficha entera va acotada a `--content-max`, hero incluido. Se probó a sangre (que el
         hero llegara al borde como el de Biblioteca) y se descartó: aquí el hero convive con el
         botón "Volver" y con la rejilla de episodios, y al salirse del ancho dejaba de estar
         alineado con ellos. En Biblioteca no pasa porque el hero es lo primero de la vista. -->
    <button class="detail__back" @click="goBack">
      <Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" /> Volver
    </button>

    <!-- Crunchyroll-style hero: wide HD art + logo, episodes follow below -->
    <header class="dhero" :class="{ 'is-cover': !hasBanner }">
      <div class="dhero__bg">
        <div class="dhero__img" :style="heroImg ? { backgroundImage: `url('${heroImg}')` } : {}" />
        <!-- invisible probe: detects a broken/blocked URL and advances to the next tier -->
        <img v-if="heroImg" :key="heroImg" :src="heroImg" alt="" class="dhero__probe" @error="onHeroError" />
        <!-- preview mudo en loop sobre el arte (fade-in cuando ya reproduce) -->
        <video v-if="previewUrl" ref="previewRef" class="dhero__video" :class="{ 'is-on': previewOn }"
               :src="previewUrl" autoplay muted loop playsinline
               @playing="previewOn = true" @error="previewUrl = ''" />
        <div class="dhero__shade" />
      </div>

      <div class="dhero__inner stagger">
        <img v-if="anime.cover && !posterFailed" class="dhero__poster" :src="imgProxy(anime.cover, 260)" :alt="anime.title"
             :style="[{ '--i': 0 }, posterGlow]" @error="posterFailed = true" />

        <div class="dhero__col" style="--i:1">
          <span class="dhero__fmt">{{ animeFormatLabel(anime.format) }}</span>
          <img v-if="hasLogo" class="dhero__logo" :src="imgProxy(anime.logo)" :alt="anime.title" @error="logoFailed = true" />
          <h1 v-else class="dhero__title">{{ anime.title }}</h1>

          <div class="dhero__stats">
            <span class="dhero__count">
              <strong>{{ vistos }}</strong> / {{ total || '?' }} vistos
              <span class="dhero__sub">· {{ done }} descargados</span>
              <span v-if="diskSize" class="dhero__disk"><Icon name="folder" :size="12" /> {{ formatBytes(diskSize) }}</span>
            </span>
            <div class="dhero__bar" :data-tip="`${vistos} vistos · ${done} descargados de ${total || '?'}`">
              <span class="dhero__bar-down" :style="{ '--bar-scale': pct / 100 }" />
              <span class="dhero__bar-seen" :style="{ '--bar-scale': pctVistos / 100 }" />
            </div>
          </div>

          <div v-if="countdown" class="dhero__airing">
            <span class="dhero__airing-dot" />
            Ep {{ countdown.episode }}
            <template v-if="countdown.d > 0">en {{ countdown.d }}d {{ countdown.h }}h</template>
            <template v-else-if="countdown.h > 0">en {{ countdown.h }}h {{ countdown.m }}m</template>
            <template v-else>en {{ countdown.m }} min</template>
          </div>

          <div class="dhero__row">
            <div class="dstatus" ref="statusRef">
              <button class="dstatus__btn" :class="{ 'is-open': statusOpen }" @click.stop="statusOpen = !statusOpen"
                      aria-haspopup="listbox" :aria-expanded="statusOpen">
                <span class="dstatus__dot" :style="{ background: curStatus?.color || 'var(--ink-ghost)' }" />
                <span class="dstatus__lbl" :style="{ color: curStatus?.color || 'var(--ink-soft)' }">{{ curStatus?.label || 'Sin estado' }}</span>
                <Icon name="chevron" :size="14" class="dstatus__chev" :style="{ transform: statusOpen ? 'rotate(90deg)' : 'rotate(-90deg)' }" />
              </button>
              <Transition name="dstatus-pop">
                <ul v-if="statusOpen" class="dstatus__menu" role="listbox">
                  <li v-for="(v, k) in ANIME_STATUS" :key="k" role="option" :aria-selected="anime.status === k"
                      class="dstatus__opt" :class="{ 'is-sel': anime.status === k }" @click="pickStatus(k)">
                    <span class="dstatus__dot" :style="{ background: v.color }" />
                    <span :style="{ color: v.color }">{{ v.label }}</span>
                    <Icon v-if="anime.status === k" name="check" :size="14" class="dstatus__ck" />
                  </li>
                  <li role="option" :aria-selected="!anime.status" class="dstatus__opt dstatus__opt--none"
                      :class="{ 'is-sel': !anime.status }" @click="pickStatus('')">
                    <span class="dstatus__dot dstatus__dot--none" />
                    <span>Sin estado</span>
                    <Icon v-if="!anime.status" name="check" :size="14" class="dstatus__ck" />
                  </li>
                </ul>
              </Transition>
            </div>
            <a v-if="anime.al_id" :href="`https://anilist.co/anime/${anime.al_id}`" target="_blank" rel="noopener" class="dhero__link">AniList</a>
            <a v-if="anime.mal_id" :href="`https://myanimelist.net/anime/${anime.mal_id}`" target="_blank" rel="noopener" class="dhero__link">MAL</a>
            <a v-if="malUrl" :href="malUrl + '/userrec'" target="_blank" rel="noopener" class="dhero__link" data-tip="Recomendaciones de la comunidad MAL">Comunidad</a>
          </div>

          <div class="dhero__mgmt">
            <!-- Preview (no en biblioteca): agregar + torrents -->
            <template v-if="isPreview">
              <button v-if="!inLibrary" class="mbtn mbtn--accent" @click="store.addToLibrary(anime)" data-tip="Añadir a Mi Anime">
                <Icon name="plus" :size="14" /> Agregar a Mi Anime
              </button>
              <button v-else class="mbtn mbtn--in" disabled><Icon name="check" :size="14" /> En Mi Anime</button>
              <button v-if="anime.al_id" class="mbtn" @click="store.openTorrents(anime)" data-tip="Buscar torrents">+ Torrents</button>
            </template>
            <!-- Biblioteca: reproducir manda; buscar torrents es lo segundo; el resto, bajo «⋯» -->
            <template v-else>
              <button v-if="resumeEp" class="mbtn mbtn--play" @click="store.play(anime, resumeEp)">
                <Icon name="play" :size="16" />
                {{ resumePct ? 'Continuar' : 'Reproducir' }} · Ep {{ resumeEp.num }}
              </button>
              <button v-if="anime.al_id" class="mbtn" :class="{ 'mbtn--accent': !resumeEp }"
                      @click="store.openTorrents(anime)" data-tip="Buscar torrents">+ Torrents</button>
              <div class="dmgmt" ref="mgmtRef">
                <button class="mbtn mbtn--icon" :class="{ 'is-open': mgmtOpen }" data-tip="Mantenimiento"
                        aria-haspopup="menu" :aria-expanded="mgmtOpen" aria-label="Mantenimiento"
                        @click.stop="mgmtOpen = !mgmtOpen">⋯</button>
                <Transition name="dstatus-pop">
                  <ul v-if="mgmtOpen" class="dmgmt__menu" role="menu">
                    <li v-if="anime.al_id" role="menuitem" @click="mgmt(() => localFolderPicker = true)">
                      <Icon name="folder" :size="14" /> Añadir carpeta local
                    </li>
                    <li role="menuitem" @click="mgmt(() => store.openCoverPicker(anime))">
                      <Icon name="library" :size="14" /> Cambiar portada o fondo
                    </li>
                    <li role="menuitem" @click="mgmt(() => store.resetAnimeCovers(anime))">
                      <Icon name="refresh" :size="14" /> Restablecer portadas
                    </li>
                    <li role="menuitem" @click="mgmt(() => store.openLinkTorrent())">
                      <Icon name="download" :size="14" /> Enlazar torrent de qBittorrent
                    </li>
                    <li role="menuitem" @click="mgmt(() => store.clearEpisodes(anime))">
                      <Icon name="folder" :size="14" /> Borrar episodios
                      <em v-if="diskSize">{{ formatBytes(diskSize) }}</em>
                    </li>
                    <li role="menuitem" class="is-danger" @click="mgmt(() => store.removeFromLibrary(anime.id))">
                      <Icon name="close" :size="14" /> Eliminar de Mi Anime
                    </li>
                  </ul>
                </Transition>
              </div>
            </template>
          </div>
        </div>
      </div>
    </header>


    <!-- Link torrent panel -->
    <div v-if="store.linkTorrent.show" class="linkpanel">
      <div class="linkpanel__head">
        <span>Enlazar torrent a "{{ anime.title }}"</span>
        <button @click="store.linkTorrent.show = false"><Icon name="close" :size="14" /></button>
      </div>
      <input class="linkpanel__sub" v-model="store.linkTorrent.subpath" placeholder="Subcarpeta (batch multi-temporada)…" />
      <div v-if="store.linkTorrent.loading" class="center-sm"><Spinner :size="16" /></div>
      <div v-else-if="!store.linkTorrent.list.length" class="linkpanel__empty">No hay torrents activos en qBittorrent</div>
      <div v-else class="linkpanel__list">
        <button v-for="t in store.linkTorrent.list" :key="t.hash" class="linkitem" @click="store.linkExistingTorrent(anime, t)">
          <span class="linkitem__name">{{ t.name }}</span>
          <span class="linkitem__meta">{{ t.state }} · {{ Math.round(t.progress) }}%</span>
        </button>
      </div>
    </div>

    <!-- Pestañas bajo el hero (estilo Crunchyroll) -->
    <nav class="dtabs">
      <button v-for="t in TABS" :key="t.key" class="dtab" :class="{ 'is-on': tab === t.key }" @click="tab = t.key">
        {{ t.label }}<span v-if="t.badge" class="dtab__badge">{{ t.badge }}</span>
      </button>
    </nav>

    <template v-if="tab === 'eps'">
    <!-- Aquí había una tarjeta «Continuar viendo» que pedía la MISMA acción que el botón del
         hero (medido: 273 px más arriba, mismo episodio) y mostraba la MISMA información que la
         propia rejilla, donde el episodio en curso ya sale con su miniatura, su «Reanudar · N %»
         y su barra estilo YouTube (`EpisodeCard`). La acción se queda donde no hay que bajar a
         buscarla —el hero— y el contexto donde ya vivía: la rejilla. -->
    <div class="eptoolbar">
      <span class="eptoolbar__lbl">{{ mainEps.length }} episodios</span>
      <button class="eptoolbar__batch" type="button" :aria-pressed="store.hideSpoilers" @click="store.toggleSpoilers()">
        Antiespoilers: {{ store.hideSpoilers ? 'activado' : 'desactivado' }}
      </button>
      <button class="eptoolbar__batch" data-tip="Buscar o traducir subtítulos en español de varios episodios"
              @click="openSubBatch">
        <Icon name="globe" :size="15" /> Subtítulos ES (lote)
      </button>
      <div class="epseg">
        <button :class="{ 'is-on': epView === 'grid' }" data-tip="Cuadrícula" @click="setEpView('grid')"><Icon name="library" :size="15" /></button>
        <button :class="{ 'is-on': epView === 'list' }" data-tip="Lista" @click="setEpView('list')"><Icon name="menu" :size="15" /></button>
      </div>
    </div>

    <div v-if="epView === 'list'" class="eplist">
      <EpisodeRow v-for="ep in mainEps" :key="animeEpisodeKey(ep)" :anime="anime" :ep="ep" :batch="batch" :current="isCurrent(ep)" :sel="epSel" />
    </div>
    <div v-else class="epgrid">
      <EpisodeCard v-for="ep in mainEps" :key="animeEpisodeKey(ep)" :anime="anime" :ep="ep" :batch="batch" />
    </div>

    <template v-if="specials.length">
      <div class="epgrid__sep"><Icon name="spark" :size="14" /> Especiales / Extras</div>
      <div v-if="epView === 'list'" class="eplist">
        <EpisodeRow v-for="ep in specials" :key="'sp-' + animeEpisodeKey(ep)" :anime="anime" :ep="ep" :batch="batch" :sel="epSel" />
      </div>
      <div v-else class="epgrid">
        <EpisodeCard v-for="ep in specials" :key="'sp-' + animeEpisodeKey(ep)" :anime="anime" :ep="ep" :batch="batch" />
      </div>
    </template>
    <!-- Barra de lote: sólo existe mientras hay algo marcado. Flotante para que no empuje la
         lista al aparecer y siga alcanzable con la lista desplazada. -->
    <Transition name="epbatch">
      <div v-if="epSel.count.value" class="epbatch">
        <span class="epbatch__n">{{ epSel.count.value }} seleccionado(s)</span>
        <span v-if="selectedBytes" class="epbatch__sz">{{ formatBytes(selectedBytes) }}</span>
        <button class="epbatch__btn" @click="epSel.all()">Todos</button>
        <button class="epbatch__btn epbatch__btn--danger" @click="deleteSelectedEps">
          <Icon name="trash" :size="14" /> Borrar archivos
        </button>
        <button class="epbatch__btn epbatch__x" data-tip="Quitar la selección" @click="epSel.clear()"><Icon name="close" :size="14" /></button>
      </div>
    </Transition>
    </template><!-- /tab eps -->

    <!-- Pestaña Detalles: sinopsis + ficha + tags -->
    <template v-else-if="tab === 'info'">
      <section class="dinfo">
        <div class="dinfo__main">
          <h3 class="disc__title">Sinopsis</h3>
          <p v-if="synopsis" class="dinfo__syn">{{ synopsis }}</p>
          <p v-else class="dinfo__none">Sin sinopsis disponible.</p>

          <template v-if="tags.length">
            <h3 class="disc__title" style="margin-top: var(--s-6)">Tags</h3>
            <div class="chips">
              <button v-for="t in tags.slice(0, 18)" :key="t.name" class="chip" @click="store.browseByTag(t.name, alId)">
                {{ t.name }}<span v-if="t.rank" class="chip__rank">{{ t.rank }}%</span>
              </button>
            </div>
          </template>
        </div>

        <aside class="dinfo__side">
          <div v-if="anime.genres?.length" class="dinfo__row">
            <span class="dinfo__k">Géneros</span>
            <span class="dinfo__v">{{ generos(anime.genres).join(', ') }}</span>
          </div>
          <div class="dinfo__row">
            <span class="dinfo__k">Formato</span>
            <span class="dinfo__v">{{ animeFormatLabel(anime.format) }}</span>
          </div>
          <div v-if="total" class="dinfo__row">
            <span class="dinfo__k">Episodios</span>
            <span class="dinfo__v">{{ total }}</span>
          </div>
          <div v-if="anime.score" class="dinfo__row">
            <span class="dinfo__k">Puntuación</span>
            <span class="dinfo__v">★ {{ (anime.score / 10).toFixed(1) }}</span>
          </div>
          <div v-if="diskSize" class="dinfo__row">
            <span class="dinfo__k">En disco</span>
            <span class="dinfo__v">{{ formatBytes(diskSize) }}</span>
          </div>
        </aside>
      </section>
    </template>

    <!-- Pestaña Relacionados: la obra original + orden de franquicia + recomendaciones + listas MAL -->
    <template v-else>
      <!-- Va PRIMERO: al terminar una temporada la pregunta es «¿por dónde sigo?», y la respuesta
           suele estar en el manga, no en otra serie parecida. Ver src/api/bridge.py. -->
      <CounterpartRow :al-id="alId" from="anime" @open="abrirManga" />

      <section v-if="franchise.length > 1" class="disc" style="margin-top: 0">
        <h3 class="disc__title">Orden de la franquicia <small class="disc__sub">por estreno</small></h3>
        <ol class="fran">
          <li v-for="(f, i) in franchise" :key="f.al_id">
            <button type="button" class="fran__row" :class="{ 'is-cur': f.is_current, 'is-own': f.in_library }"
                    :aria-label="`Abrir ${f.title}`" @click="store.openFranchiseItem(f)">
              <span class="fran__n">{{ i + 1 }}</span>
              <img v-if="f.cover" class="fran__cover" :src="imgProxy(f.cover)" :alt="f.title" loading="lazy" decoding="async" />
              <div class="fran__meta">
                <span class="fran__t">{{ f.title }}</span>
                <span class="fran__sub">
                  <em v-if="f.year">{{ f.year }}</em>
                  <span class="fran__badge">{{ fmtLabel(f.format) }}</span>
                  <span v-if="f.episodes">{{ f.episodes }} ep</span>
                </span>
              </div>
              <span v-if="f.is_current" class="fran__tag fran__tag--cur">Estás aquí</span>
              <span v-else-if="f.in_library" class="fran__tag fran__tag--own"><Icon name="check" :size="11" /> En tu biblioteca</span>
            </button>
          </li>
        </ol>
      </section>

      <section v-if="stacks.length" class="disc" :style="franchise.length > 1 ? {} : { marginTop: 0 }">
        <h3 class="disc__title">Listas de interés (MAL)</h3>
        <div class="chips">
          <button v-for="st in stacks" :key="st.id" class="chip chip--stack" @click="store.browseStack(st)">{{ st.name }}</button>
        </div>
      </section>

      <section v-if="recs.length" class="disc" :style="stacks.length ? {} : { marginTop: 0 }">
        <h3 class="disc__title">Recomendaciones</h3>
        <div class="recgrid">
          <button v-for="r in recs" :key="r.al_id" type="button" class="rec"
                  :aria-label="`Abrir ${r.title}`" @click="store.openRec(r)">
            <div class="rec__poster">
              <img v-if="r.cover" :src="imgProxy(r.cover)" :alt="r.title" loading="lazy" decoding="async" />
              <div class="rec__scrim" />
              <span v-if="r.score" class="rec__score">★ {{ (r.score / 10).toFixed(1) }}</span>
              <span v-if="store.isInLibrary(r)" class="rec__in"><Icon name="check" :size="10" /></span>
              <div class="rec__ov"><span class="rec__t">{{ r.title }}</span></div>
            </div>
          </button>
        </div>
      </section>
      <p v-if="!recs.length && !stacks.length && franchise.length <= 1" class="dinfo__none">Sin relacionados todavía.</p>
    </template>

    <!-- Stack browse overlay -->

    <Teleport to="body">
      <div v-if="store.stackBrowse" class="ov" @click.self="store.closeStackBrowse()">
        <div class="bmodal">
          <button class="bmodal__x" @click="store.closeStackBrowse()"><Icon name="close" :size="18" /></button>
          <header class="bmodal__head">
            <h2>{{ store.stackBrowseMeta?.name || store.stackBrowse.name }}</h2>
            <a v-if="store.stackBrowse.url" :href="store.stackBrowse.url" target="_blank" rel="noopener" class="bmodal__link">Ver en MAL ↗</a>
            <p v-if="store.stackBrowseMeta?.description" class="bmodal__desc">{{ store.stackBrowseMeta.description }}</p>
          </header>
          <div v-if="store.stackBrowseState === 'loading'" class="center"><Spinner /></div>
          <div v-else class="bgrid">
            <button v-for="a in store.stackBrowseAnime" :key="a.al_id || a.mal_id" type="button" class="rec"
                    :aria-label="`Abrir ${a.title}`" @click="store.openRec(a); store.closeStackBrowse()">
              <div class="rec__poster"><img v-if="a.cover" :src="imgProxy(a.cover)" :alt="a.title" loading="lazy" decoding="async" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Tag browse overlay -->
    <Teleport to="body">
      <div v-if="store.tagBrowse" class="ov" @click.self="store.closeTagBrowse()">
        <div class="bmodal">
          <button class="bmodal__x" @click="store.closeTagBrowse()"><Icon name="close" :size="18" /></button>
          <header class="bmodal__head">
            <h2>{{ store.tagBrowse }} <span class="muted">en AniList</span></h2>
            <!-- El umbral es lo que hace útil esta pantalla: «Magic» al 60 % son cientos de
                 series que sólo lo mencionan; al 90 % son las que VAN de eso. -->
            <div class="segm bmodal__rank">
              <button v-for="r in TAG_RANKS" :key="r" :class="{ 'is-active': store.tagBrowseRank === r }"
                      @click="store.setTagRank(r)">{{ r ? `${r}%+` : 'Cualquiera' }}</button>
            </div>
          </header>
          <div v-if="store.tagBrowseState === 'loading'" class="center"><Spinner /></div>
          <ErrorState v-else-if="store.tagBrowseState === 'error'" title="No se pudo consultar AniList."
                      @retry="store.setTagRank(store.tagBrowseRank)" />
          <EmptyState v-else-if="!store.tagBrowseAnime.length" icon="search"
                      :title="store.tagBrowseRank
                        ? `Ninguna serie llega al ${store.tagBrowseRank}% de «${store.tagBrowse}»`
                        : `Sin resultados para «${store.tagBrowse}»`"
                      :hint="store.tagBrowseRank ? 'Baja el umbral para ver las que sólo lo tocan de refilón.' : ''" />
          <div v-else class="bgrid">
            <button v-for="a in store.tagBrowseAnime" :key="a.al_id" type="button" class="rec"
                    :aria-label="`Abrir ${a.title}`" @click="store.openRec(a); store.closeTagBrowse()">
              <div class="rec__poster"><img v-if="a.cover" :src="imgProxy(a.cover)" :alt="a.title" loading="lazy" decoding="async" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><span v-if="a.tag_rank" class="rec__rank">{{ a.tag_rank }}%</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Episode override context menu -->
    <Teleport to="body">
      <div v-if="store.epOverrideMenu" class="ctx-backdrop" @click="store.epOverrideMenu = null">
        <div class="ctx" :style="{ left: store.epOverrideMenu.x + 'px', top: store.epOverrideMenu.y + 'px' }" @click.stop>
          <button @click="store.setEpOverride('episode')">Marcar como episodio</button>
          <button @click="store.setEpOverride('special')">Marcar como especial</button>
          <button @click="store.setEpOverride('hidden')">Ocultar</button>
        </div>
      </div>
    </Teleport>

    <!-- Cover / background picker modal -->
    <Teleport to="body">
      <div v-if="store.coverPicker" class="ov" @click.self="store.coverPicker = null">
        <div class="bmodal bmodal--covers">
          <button class="bmodal__x" @click="store.coverPicker = null"><Icon name="close" :size="18" /></button>
          <header class="bmodal__head"><h2>Cambiar imagen</h2></header>
          <div class="pickertabs">
            <button v-for="t in PICKER_TABS" :key="t.key" class="pickertab" :class="{ 'is-on': store.coverPicker.tab === t.key }"
                    @click="store.switchPickerTab(t.key)">{{ t.label }}</button>
          </div>
          <div v-if="store.coverPickerLoading" class="center"><Spinner /></div>
          <div v-else-if="!activeTab.options.length" class="covers__empty">No se encontraron imágenes</div>
          <div v-else class="covergrid" :class="{ 'covergrid--wide': store.coverPicker.tab !== 'cover' }">
            <button v-for="(o, i) in activeTab.options" :key="i" class="coveropt"
                    :class="{ 'is-current': o.url === activeTab.current }"
                    :disabled="store.coverSaving" @click="store.pickCover(o)">
              <!-- Las opciones de AniList/TMDB/MAL pasan por la caché común; las fuentes no
                   permitidas siguen intactas porque imgProxy() las deja pasar sin abrir el proxy. -->
              <img :src="imgProxy(o.url, store.coverPicker.tab === 'cover' ? 160 : 320)" :alt="o.label" loading="lazy" decoding="async" />
              <span class="coveropt__label">{{ o.label }}</span>
              <span v-if="o.url === activeTab.current" class="coveropt__current"><Icon name="check" :size="12" /></span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <FolderPicker v-model:open="localFolderPicker"
                  :title="`Añadir carpeta local a ${anime.title}`"
                  @pick="p => store.attachLocalFolder(anime, p.path || p.win)" />
  </div>
</template>

<style scoped>
/* Ancho casi completo, con margen. `Mi Anime` llega de borde a borde y es la vista que mejor
   funciona, pero aquí sí queremos que se vea un margen: la ficha no es una parrilla infinita, es
   UNA obra, y sin nada a los lados se pierde el borde de la tarjeta grande que en el fondo es.
   El margen crece con la pantalla (`3vw`) entre 2 y 6 rem, así que a 1600 es discreto y a 2560
   sigue existiendo en vez de quedarse en una raya. Los bloques de TEXTO no se estiran con él:
   `.dhero__inner` (56,25rem) y la sinopsis (62ch) tienen su propio tope. */
.detail { position: relative; padding: var(--s-4) clamp(var(--s-6), 3vw, var(--s-9)) var(--s-8); }

.detail__back { display: inline-flex; align-items: center; gap: var(--s-1); margin: var(--s-2) 0 var(--s-5);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft);
  border: 1px solid var(--line); background: var(--glass); backdrop-filter: blur(8px); font-size: var(--fs-sm); transition: all var(--t-fast); }
.detail__back:hover { color: var(--ink); border-color: var(--line-strong); }

/* Hero header — Crunchyroll-style wide HD art + logo, same recipe as HeroBanner.vue.
   A sangre (rompe el padding del contenedor), sin caja redondeada, más baja, y la IMAGEN se
   disuelve en el fondo por arriba y abajo (máscara de alfa), como el hero de Mi Anime. La rampa
   de arriba es una curva del 20 % y no una recta del 8 %. La máscara va en `.dhero__bg`, que aquí
   ya CONTIENE al velo — importa: enmascarar sólo la imagen deja que el velo corte en seco en el
   borde de la caja (medido: escalón de +21 de luminancia en una fila). Ver MediaHero.vue. */
.dhero { position: relative; margin: 0 0 var(--s-4); height: clamp(28.75rem, 50vw, 37.5rem);
  border-radius: 0; overflow: hidden; background: transparent; }
.dhero__bg { position: absolute; inset: 0;
  -webkit-mask-image: linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,.16) 5%, rgba(0,0,0,.5) 10%,
      rgba(0,0,0,.84) 15%, #000 20%, #000 66%, transparent 100%);
          mask-image: linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,.16) 5%, rgba(0,0,0,.5) 10%,
      rgba(0,0,0,.84) 15%, #000 20%, #000 66%, transparent 100%); }
.dhero__img { position: absolute; inset: 0; background-size: cover; background-position: center 18%; }
/* No wide banner cached yet → fall back to the (vertical) cover, blurred to fill the frame. */
.dhero.is-cover .dhero__img { filter: blur(28px) saturate(1.15) brightness(.85); transform: scale(1.18); }
.dhero__probe { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.dhero__video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
  object-position: center 18%; opacity: 0; transition: opacity 1.2s var(--ease-silk); }
.dhero__video.is-on { opacity: 1; }
.dhero__shade { position: absolute; inset: 0;
  background: linear-gradient(90deg, rgba(7,10,18,.9) 0%, rgba(7,10,18,.45) 36%, transparent 66%); }

.dhero__inner { position: relative; z-index: 1; height: 100%; display: flex; align-items: flex-end;
  gap: var(--s-5); max-width: 56.25rem; padding: var(--s-6) var(--s-7); }
/* natural aspect (height auto) + flex-shrink:0 → poster shown whole, not cropped or squeezed. */
.dhero__poster { width: 10.5rem; height: auto; border-radius: var(--r-md); box-shadow: var(--shadow-lg), var(--pglow, 0 0 0 transparent); border: 1px solid rgba(255,255,255,.16); flex-shrink: 0;
  transition: box-shadow var(--t-slow) var(--ease-silk);
  view-transition-name: detail-poster;   /* destino del morph desde la card (lib/vt.js) */ }
.dhero__col { display: flex; flex-direction: column; gap: var(--s-3); min-width: 0; }
.dhero__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--cyan); }
.dhero__title { font-family: var(--font-display); font-weight: 700; color: #fff; font-size: clamp(2rem, 4vw, 3.4rem);
  line-height: 1.06; text-shadow: 0 2px 24px rgba(0,0,0,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dhero__logo { max-width: min(32.5rem, 80%); max-height: clamp(6.25rem, 14vw, 11.25rem); width: auto; height: auto;
  object-fit: contain; object-position: left bottom; filter: drop-shadow(0 4px 20px rgba(0,0,0,.65)); }

.dhero__stats { display: flex; flex-direction: column; gap: var(--s-2); max-width: 22.5rem; }
.dhero__count { font-size: var(--fs-sm); color: var(--ice); text-shadow: 0 1px 8px rgba(0,0,0,.7); }
.dhero__count strong { color: #fff; font-family: var(--font-display); }
.dhero__disk { display: inline-flex; align-items: center; gap: 4px; margin-left: var(--s-2); padding: 1px 0.5rem;
  border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--ice);
  background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.16); }
.dhero__disk :deep(svg) { color: var(--cyan); }
.dhero__sub { color: var(--ink-faint); }
/* Dos capas en la misma pista: lo descargado va detrás y en tenue, lo visto delante y sólido.
   Se superponen (ambas parten de la izquierda), así que el tramo «descargado pero sin ver» se lee
   solo, como la parte apagada de la barra. */
.dhero__bar { position: relative; height: 4px; border-radius: var(--r-pill);
  background: rgba(255,255,255,.22); overflow: hidden; }
.dhero__bar span { position: absolute; left: 0; top: 0; width: 100%; height: 100%;
  transform: scaleX(var(--bar-scale, 0)); transform-origin: left; transition: transform .5s var(--ease-silk); }
.dhero__bar-down { background: rgba(255,255,255,.3); }
.dhero__bar-seen { background: linear-gradient(90deg, var(--azure-deep), var(--azure));
  box-shadow: 0 0 8px var(--azure-glow); }

.dhero__airing { display: inline-flex; align-items: center; gap: var(--s-2); width: fit-content;
  font-size: var(--fs-xs); color: var(--ice); padding: 4px 0.75rem; border-radius: var(--r-pill);
  background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(6px); }
.dhero__airing-dot { width: 0.4375rem; height: 0.4375rem; border-radius: 50%; background: var(--cyan); box-shadow: var(--glow-cyan); animation: pulse-live 2s var(--ease-drift) infinite; }

.dhero__row { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
/* Selector de estado propio (reemplaza el <select> nativo) */
.dstatus { position: relative; }
.dstatus__btn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.16);
  backdrop-filter: blur(8px); font-size: var(--fs-sm); font-weight: 600; cursor: pointer; transition: all var(--t-fast); }
.dstatus__btn:hover { border-color: var(--azure); background: rgba(255,255,255,.2); }
.dstatus__btn.is-open { border-color: var(--azure); background: rgba(255,255,255,.2); }
.dstatus__dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; flex-shrink: 0; box-shadow: 0 0 8px currentColor; }
.dstatus__dot--none { background: var(--ink-ghost); box-shadow: none; }
.dstatus__lbl { white-space: nowrap; }
.dstatus__chev { color: var(--ink-soft); transition: transform var(--t-fast); }

/* Se abre HACIA ARRIBA: el botón vive pegado al borde inferior del hero, que tiene
   overflow:hidden — hacia abajo el menú quedaría recortado. */
.dstatus__menu { position: absolute; z-index: 20; bottom: calc(100% + var(--s-1)); left: 0; min-width: 12rem;
  list-style: none; margin: 0; padding: var(--s-1); border-radius: var(--r-md);
  background: rgba(12,16,26,.97); border: 1px solid var(--line-2); backdrop-filter: blur(14px); box-shadow: var(--shadow-xl); }
.dstatus__opt { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 500; cursor: pointer; transition: background var(--t-fast); }
.dstatus__opt:hover { background: rgba(255,255,255,.08); }
.dstatus__opt.is-sel { background: var(--azure-haze); }
.dstatus__opt--none span:not(.dstatus__dot) { color: var(--ink-soft); }
.dstatus__ck { margin-left: auto; color: var(--azure-bright); }

.dstatus-pop-enter-active, .dstatus-pop-leave-active { transition: opacity var(--t-fast), transform var(--t-fast) var(--ease-silk); transform-origin: bottom left; }
.dstatus-pop-enter-from, .dstatus-pop-leave-to { opacity: 0; transform: translateY(4px) scale(.97); }
/* AniList/MAL/Comunidad son ENLACES a otra web, no acciones de la app: con forma de botón competían
   con «Reproducir». Ahora son texto subrayado al pasar, como cualquier enlace. */
.dhero__link { padding: var(--s-1) 0; color: var(--ink-soft); font-size: var(--fs-sm);
  border-bottom: 1px solid transparent; transition: color var(--t-fast), border-color var(--t-fast); }
.dhero__link:hover { color: #fff; border-bottom-color: rgba(255,255,255,.45); }
.dhero__row .dhero__link + .dhero__link { margin-left: var(--s-1); }

/* Pestañas bajo el hero — subrayado estilo Crunchyroll */
.dtabs { display: flex; gap: var(--s-5); margin: 0 0 var(--s-6); border-bottom: 1px solid var(--line); }
.dtab {
  position: relative; padding: var(--s-3) var(--s-1); font-family: var(--font-display);
  font-size: var(--fs-md); font-weight: 600; color: var(--ink-faint);
  border: none; background: transparent; cursor: pointer; transition: color var(--t-fast);
}
.dtab:hover { color: var(--ink); }
.dtab.is-on { color: var(--ink); }
.dtab.is-on::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 3px;
  border-radius: var(--r-pill); background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow);
}
.dtab__badge { margin-left: 0.4375rem; padding: 1px 0.4375rem; border-radius: var(--r-pill);
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700;
  color: var(--ink-soft); background: var(--surface-2); border: 1px solid var(--line); }

/* Pestaña Detalles */
/* Las dos columnas se DIMENSIONAN por su contenido y el par se centra, en vez de `1fr` + panel
   clavado a la derecha. Con la ficha ensanchada eso dejaba un agujero de 920 px entre el final de
   la sinopsis (topada a 62ch, que es lo que se lee cómodo) y el panel: dos bloques mirándose desde
   lejos. Ahora el panel se pega al texto y el sobrante se va al margen derecho.
   `start` y no `center`: TODO en esta vista está alineado a la izquierda (Volver, el hero, las
   pestañas), y centrar sólo este bloque lo dejaba flotando sin relación con nada.
   `minmax(0, …)` en la primera para que pueda encoger en pantallas medianas sin desbordar. */
.dinfo { display: grid; grid-template-columns: minmax(0, 62ch) minmax(15rem, 20rem);
  justify-content: start; gap: var(--s-7); align-items: start; }
.dinfo__syn { font-size: var(--fs-md); line-height: var(--lh-body); color: var(--ink-soft);
  max-width: 62ch; white-space: pre-line; }
.dinfo__none { color: var(--ink-faint); font-size: var(--fs-sm); }
.dinfo__side { display: flex; flex-direction: column; gap: var(--s-3); padding: var(--s-4);
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); }
.dinfo__row { display: flex; flex-direction: column; gap: 2px; }
.dinfo__k { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--ink-faint); }
.dinfo__v { font-size: var(--fs-sm); color: var(--ink); }
@media (max-width: 640px) { .dinfo { grid-template-columns: 1fr; } }


.eptoolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); margin: 0 0 var(--s-4); }
.eptoolbar__lbl { font-family: var(--font-display); font-size: var(--fs-lg); font-weight: 600; color: var(--ink); }
.eptoolbar__batch { margin-left: auto; display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); font-size: var(--fs-sm); font-weight: 600;
  color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.eptoolbar__batch:hover { color: #fff; border-color: var(--azure); background: var(--azure-haze); }
.epseg { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.epseg button { width: 2.125rem; height: 1.875rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-faint); transition: all var(--t-fast); }
.epseg button:hover { color: var(--ink); }
.epseg button.is-on { background: var(--surface-3); color: var(--azure-bright); }

.eplist { display: flex; flex-direction: column; gap: var(--s-2); }
.epgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(19rem, 1fr)); gap: var(--s-5); }
.epgrid__sep { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-7) 0 var(--s-4); color: var(--ink-soft); font-family: var(--font-display); font-weight: 600; }
.epgrid__sep :deep(svg) { color: var(--gold); }

/* management */
.dhero__mgmt { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.mbtn { padding: 0.375rem 0.75rem; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink);
  background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(8px); transition: all var(--t-fast); }
.mbtn:hover { color: #fff; border-color: rgba(255,255,255,.32); background: rgba(255,255,255,.18); }
.mbtn--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.mbtn__sz { margin-left: 0.375rem; padding: 1px 0.375rem; border-radius: var(--r-pill); font-family: var(--font-mono);
  font-size: var(--fs-2xs); color: var(--cyan); background: rgba(255,255,255,.1); }
.mbtn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.mbtn--accent:hover { background: var(--azure-bright); color: #fff; }
.mbtn--accent, .mbtn--in { display: inline-flex; align-items: center; gap: 0.3125rem; }
.mbtn--in { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); background: color-mix(in srgb, var(--jade) 12%, transparent); opacity: 1; }

/* Reproducir es LA acción de esta pantalla: blanco sólido sobre el arte, como en cualquier app de
   vídeo. El resto de la fila queda en cristal translúcido, un escalón por debajo. */
.mbtn--play { display: inline-flex; align-items: center; gap: 0.4375rem; padding: 0.5rem 1.125rem;
  font-size: var(--fs-sm); font-weight: 650; color: #0a0d15; background: #fff; border-color: transparent;
  box-shadow: 0 6px 20px rgba(0,0,0,.35); }
.mbtn--play:hover { color: #0a0d15; background: #fff; border-color: transparent; transform: translateY(-1px); }
.mbtn--icon { font-size: var(--fs-md); line-height: 1; padding: 0.375rem 0.625rem; letter-spacing: .06em; }
.mbtn--icon.is-open { background: rgba(255,255,255,.22); color: #fff; }

.dmgmt { position: relative; }
.dmgmt__menu { position: absolute; z-index: 20; bottom: calc(100% + var(--s-1)); left: 0; min-width: 17.5rem; white-space: nowrap;
  list-style: none; margin: 0; padding: var(--s-1); border-radius: var(--r-md);
  background: rgba(12,16,26,.97); border: 1px solid var(--line-2); backdrop-filter: blur(14px); box-shadow: var(--shadow-xl); }
.dmgmt__menu li { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink); cursor: pointer; transition: background var(--t-fast), color var(--t-fast); }
.dmgmt__menu li:hover { background: rgba(255,255,255,.08); }
.dmgmt__menu li.is-danger { color: var(--coral); }
.dmgmt__menu li.is-danger:hover { background: color-mix(in srgb, var(--coral) 14%, transparent); }
.dmgmt__menu em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.linkpanel { margin: 0 0 var(--s-6); padding: var(--s-4); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.linkpanel__head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: var(--s-3); }
.linkpanel__head button { color: var(--ink-faint); }
.linkpanel__sub { width: 100%; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); margin-bottom: var(--s-3); }
.linkpanel__empty { color: var(--ink-faint); font-size: var(--fs-sm); text-align: center; padding: var(--s-3); }
.linkpanel__list { display: flex; flex-direction: column; gap: var(--s-1); max-height: 15rem; overflow-y: auto; }
.linkitem { display: flex; flex-direction: column; gap: 2px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line); text-align: left; transition: all var(--t-fast); }
.linkitem:hover { border-color: var(--azure); }
.linkitem__name { font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.linkitem__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
.center-sm { display: grid; place-items: center; padding: var(--s-3); }

/* discovery sections */
.disc { margin-top: var(--s-7); }
.disc__title { font-family: var(--font-display); font-size: var(--fs-lg); margin-bottom: var(--s-3); }
.disc__sub { font-family: var(--font-body); font-size: var(--fs-xs); font-weight: 400; color: var(--ink-faint); margin-left: var(--s-2); }

/* orden de franquicia: timeline vertical de entregas */
.fran { list-style: none; display: flex; flex-direction: column; gap: var(--s-2); }
.fran__row {
  display: flex; width: 100%; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3);
  color: inherit; font: inherit; text-align: left; background: transparent;
  border: 1px solid var(--line); border-radius: var(--r-md); cursor: pointer;
  transition: border-color var(--t-fast), background var(--t-fast);
}
.fran__row:focus-visible { outline: 2px solid var(--azure-bright); outline-offset: 2px; }
.fran__row:hover { border-color: var(--azure); background: color-mix(in srgb, var(--azure) 7%, transparent); }
.fran__row.is-cur { cursor: default; border-color: var(--azure); background: color-mix(in srgb, var(--azure) 12%, transparent); }
.fran__n { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); width: 1.4rem; text-align: center; flex: none; }
.fran__cover { width: 2.6rem; height: 3.7rem; object-fit: cover; border-radius: var(--r-sm); flex: none; background: var(--surface-2); }
.fran__meta { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.fran__t { font-size: var(--fs-sm); font-weight: 600; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fran__sub { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-soft); }
.fran__sub em { font-style: normal; font-family: var(--font-mono); color: var(--ink-faint); }
.fran__badge { padding: 1px 0.4375rem; border-radius: var(--r-pill); border: 1px solid var(--line); font-size: var(--fs-2xs); color: var(--ink-soft); }
.fran__tag { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-2xs); font-weight: 700; white-space: nowrap; flex: none; }
.fran__tag--cur { color: var(--azure-bright); }
.fran__tag--own { color: var(--jade); }
.chips { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.chip { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 0.3125rem 0.75rem; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.chip:hover { color: var(--ink); border-color: var(--azure); }
.chip__rank { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.chip--stack { color: var(--violet); border-color: color-mix(in srgb, var(--violet) 30%, transparent); }
.chip--stack:hover { background: color-mix(in srgb, var(--violet) 12%, transparent); }

.recgrid, .bgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.125rem, 1fr)); gap: var(--s-4); }
.rec { display: block; width: 100%; padding: 0; border: 0; color: inherit; background: transparent;
  font: inherit; text-align: left; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.rec:focus-visible { outline: 2px solid var(--azure-bright); outline-offset: 3px; border-radius: var(--r-md); }
.rec:hover { transform: translateY(-5px); }
.rec__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.rec:hover .rec__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-md); }
.rec__poster img { width: 100%; height: 100%; object-fit: cover; }
.rec__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 45%, rgba(5,7,13,.92)); }
.rec__score { position: absolute; top: 0.375rem; left: 0.375rem; font-size: var(--fs-2xs); font-weight: 700; color: var(--gold); padding: 2px 0.375rem; border-radius: var(--r-pill); background: rgba(7,10,18,.6); }
.rec__in { position: absolute; top: 0.375rem; right: 0.375rem; width: 1.125rem; height: 1.125rem; display: grid; place-items: center; border-radius: 50%; background: var(--jade); color: #fff; }
.rec__ov { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-2); }
.rec__t { font-size: var(--fs-2xs); font-weight: 600; color: #fff; line-height: 1.25; text-shadow: 0 1px 4px rgba(0,0,0,.7); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* browse overlays */
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.bmodal { position: relative; width: min(48.75rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); padding: var(--s-5); overflow-y: auto; }
.bmodal__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.bmodal__head { margin-bottom: var(--s-4); padding-right: var(--s-7); }
.bmodal__head h2 { font-size: var(--fs-xl); }
.bmodal__head .muted { color: var(--ink-faint); font-weight: 400; }
.bmodal__link { font-size: var(--fs-xs); color: var(--azure-bright); }
.bmodal__desc { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: var(--s-2); }
.center { display: grid; place-items: center; padding: var(--s-7); }

/* context menu */
.ctx-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); }
.ctx { position: fixed; display: flex; flex-direction: column; min-width: 12.5rem; padding: var(--s-1); border-radius: var(--r-md); background: var(--glass-strong); backdrop-filter: blur(16px); border: 1px solid var(--line-2); box-shadow: var(--shadow-lg); }
.ctx button { text-align: left; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); }
.ctx button:hover { background: var(--surface-2); color: var(--ink); }

/* cover / background picker */
.bmodal--covers { width: min(56.25rem, 100%); }
.pickertabs { display: flex; gap: var(--s-2); margin-bottom: var(--s-4); flex-wrap: wrap; }
.pickertab { padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); font-size: var(--fs-sm); color: var(--ink-soft);
  border: 1px solid var(--line); background: var(--surface-2); transition: all var(--t-fast); }
.pickertab:hover { color: var(--ink); border-color: var(--line-strong); }
.pickertab.is-on { color: #fff; background: var(--azure); border-color: transparent; }
.covers__empty { color: var(--ink-faint); font-size: var(--fs-sm); text-align: center; padding: var(--s-7); }
.covergrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.75rem, 1fr)); gap: var(--s-4); }
.covergrid--wide { grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); }
.coveropt { position: relative; display: flex; flex-direction: column; gap: var(--s-2); border-radius: var(--r-md); overflow: hidden;
  border: 2px solid var(--line); background: var(--surface-2); transition: all var(--t-fast); text-align: left; }
.coveropt:hover { border-color: var(--azure-glow); transform: translateY(-3px); }
.coveropt.is-current { border-color: var(--azure); }
.coveropt:disabled { opacity: .6; pointer-events: none; }
.coveropt img { width: 100%; aspect-ratio: 2/3; object-fit: cover; background: var(--base); }
.covergrid--wide .coveropt img { aspect-ratio: 16/9; }
.coveropt__label { padding: 0 var(--s-2) var(--s-2); font-size: var(--fs-2xs); color: var(--ink-faint); }
.coveropt__current { position: absolute; top: 0.375rem; right: 0.375rem; width: 1.25rem; height: 1.25rem; display: grid; place-items: center; border-radius: 50%; background: var(--azure); color: #fff; }

@media (max-width: 640px) {
  .detail { padding: var(--s-3) var(--s-4) var(--s-8); }
  .dhero { height: clamp(23.75rem, 72vw, 30rem); border-radius: 0; margin: 0 calc(-1 * var(--s-4)) var(--s-4); }
  .detail__body { padding: 0 var(--s-4); }
  .dhero__inner { padding: var(--s-5) var(--s-4); }
  .dhero__poster { display: none; }
  .dhero__logo { max-width: 70%; max-height: 5.625rem; }
  .epgrid { grid-template-columns: repeat(auto-fill, minmax(13.5rem, 1fr)); gap: var(--s-3); }
}

/* ── Barra de selección por lote de episodios ─────────────────────────────── */
.epbatch {
  position: sticky; bottom: var(--s-4); z-index: 20;
  display: flex; align-items: center; gap: var(--s-3);
  margin: var(--s-4) auto 0; width: fit-content; max-width: 100%;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-pill);
  background: var(--glass-strong); backdrop-filter: blur(16px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-lg);
}
.epbatch__n { font-size: var(--fs-sm); font-weight: 600; color: var(--ink); white-space: nowrap; }
.epbatch__sz { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.epbatch__btn {
  display: inline-flex; align-items: center; gap: 0.375rem; white-space: nowrap;
  padding: 0.375rem 0.75rem; border-radius: var(--r-pill);
  font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft);
  border: 1px solid var(--line); transition: all var(--t-fast);
}
.epbatch__btn:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface); }
.epbatch__btn--danger:hover { color: #fff; background: var(--coral); border-color: transparent; }
.epbatch__x { padding: 0.375rem; }
.epbatch-enter-active, .epbatch-leave-active { transition: opacity var(--t-fast) var(--ease-silk), transform var(--t-fast) var(--ease-silk); }
.epbatch-enter-from, .epbatch-leave-to { opacity: 0; transform: translateY(0.5rem); }
@media (prefers-reduced-motion: reduce) {
  .epbatch-enter-active, .epbatch-leave-active { transition: none; }
}
.bmodal__head { display: flex; align-items: center; justify-content: space-between;
  gap: var(--s-4); flex-wrap: wrap; }
.bmodal__rank { flex: none; }
/* El % del tag va ARRIBA IZQUIERDA, enfrente de la nota: son las dos cifras que se comparan al
   barrer la rejilla, y encontradas se leen de un vistazo. */
.rec__rank { position: absolute; top: var(--s-2); left: var(--s-2); z-index: 2;
  padding: 2px var(--s-2); border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 700;
  color: var(--ink); background: rgba(8,11,20,.72); backdrop-filter: blur(6px); }
</style>
