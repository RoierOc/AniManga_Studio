<script setup>
import { ref, computed, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { useUiStore } from '@/stores/ui'
import { ANIME_STATUS, animeFormatLabel, animeEpLabel, batchInfo, fmtCountdown, nextUnwatchedEp } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import { formatBytes } from '@/lib/format'
import EpisodeCard from '@/components/anime/EpisodeCard.vue'
import EpisodeRow from '@/components/anime/EpisodeRow.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
const anime = computed(() => store.detail)
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
  return a ? [a.banner_detail || a.banner, a.cover_xl, a.cover].filter(Boolean).map(imgProxy) : []
})
const heroImg = computed(() => heroTiers.value[heroIdx.value] || '')
const hasBanner = computed(() => heroIdx.value <= 1 && !!((anime.value?.banner_detail || anime.value?.banner) || anime.value?.cover_xl))
function onHeroError() { if (heroIdx.value < heroTiers.value.length - 1) heroIdx.value++ }

// Falls back to the text title when no logo is cached, or the cached logo URL fails to load.
const logoFailed = ref(false)
const hasLogo = computed(() => !!anime.value?.logo && !logoFailed.value)
const posterFailed = ref(false)
watch(() => anime.value?.id, () => { heroIdx.value = 0; logoFailed.value = false; posterFailed.value = false; tab.value = 'eps' })

// Pestañas estilo Crunchyroll bajo el hero
const tab = ref('eps')

// openDetail() always pushes one history entry, so back consumes it and runs the
// guarded restore. Fall back to a direct close if there's no app history.
// Close the detail and stay on the anime view (no history jump); back/forward still work.
function goBack() { store.closeDetail(); useUiStore().replaceNav() }

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

const total = computed(() => anime.value?.total_episodes || 0)
const done = computed(() => anime.value?.downloaded_count || 0)
const pct = computed(() => total.value ? Math.min(100, done.value / total.value * 100) : 0)
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
const resumeTitle = computed(() => resumeEp.value ? animeEpLabel(anime.value, resumeEp.value) : '')
const resumeThumbFailed = ref(false)
watch(resumeEp, () => { resumeThumbFailed.value = false })

// Vista de episodios: cuadrícula (actual) ⇄ lista. Persiste la preferencia.
const epView = ref(localStorage.getItem('anime-epview') || 'grid')
function setEpView(v) { epView.value = v; localStorage.setItem('anime-epview', v) }
const isCurrent = (ep) => !!resumeEp.value && ep.num === resumeEp.value.num && ep.ep_type !== 'special'

const countdown = computed(() => {
  const na = store.nextAiring[anime.value?.al_id]
  if (!na?.airing_at) return null
  const c = fmtCountdown(na.airing_at, store.nowSec)
  return c ? { ...c, episode: na.episode } : null
})

const alId = computed(() => anime.value?.al_id)
const recs = computed(() => store.recs[alId.value] || [])
const tags = computed(() => store.tags[alId.value] || [])
const stacks = computed(() => store.stacks[alId.value] || [])
const malUrl = computed(() => store.malUrls[alId.value])

const TABS = computed(() => [
  { key: 'eps', label: 'Episodios' },
  { key: 'info', label: 'Detalles' },
  { key: 'rel', label: 'Relacionados', badge: recs.value.length + stacks.value.length },
])

// Sinopsis completa (endpoint /synopsis) con la recortada de library como fallback
const synopsis = computed(() => store.fullSyn[alId.value] || anime.value?.synopsis || '')

const PICKER_TABS = [
  { key: 'cover', label: 'Portada' },
  { key: 'banner_detail', label: 'Fondo de esta página' },
  { key: 'banner', label: 'Fondo en Inicio' },
]
const activeTab = computed(() => store.coverPicker?.tabs[store.coverPicker.tab])
</script>

<template>
  <div v-if="anime" class="detail">
    <button class="detail__back" @click="goBack">
      <Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" /> Volver
    </button>

    <!-- Crunchyroll-style hero: wide HD art + logo, episodes follow below -->
    <header class="dhero" :class="{ 'is-cover': !hasBanner }">
      <div class="dhero__bg">
        <div class="dhero__img" :style="heroImg ? { backgroundImage: `url('${heroImg}')` } : {}" />
        <!-- invisible probe: detects a broken/blocked URL and advances to the next tier -->
        <img v-if="heroImg" :key="heroImg" :src="heroImg" alt="" class="dhero__probe" @error="onHeroError" />
        <div class="dhero__shade" />
      </div>

      <div class="dhero__inner stagger">
        <img v-if="anime.cover && !posterFailed" class="dhero__poster" :src="imgProxy(anime.cover)" :alt="anime.title" style="--i:0" @error="posterFailed = true" />

        <div class="dhero__col" style="--i:1">
          <span class="dhero__fmt">{{ animeFormatLabel(anime.format) }}</span>
          <img v-if="hasLogo" class="dhero__logo" :src="imgProxy(anime.logo)" :alt="anime.title" @error="logoFailed = true" />
          <h1 v-else class="dhero__title">{{ anime.title }}</h1>

          <div class="dhero__stats">
            <span class="dhero__count">
              <strong>{{ done }}</strong> / {{ total || '?' }} episodios
              <span v-if="diskSize" class="dhero__disk"><Icon name="folder" :size="12" /> {{ formatBytes(diskSize) }}</span>
            </span>
            <div class="dhero__bar"><span :style="{ width: pct + '%' }" /></div>
          </div>

          <div v-if="countdown" class="dhero__airing">
            <span class="dhero__airing-dot" />
            Ep {{ countdown.episode }}
            <template v-if="countdown.d > 0">en {{ countdown.d }}d {{ countdown.h }}h</template>
            <template v-else-if="countdown.h > 0">en {{ countdown.h }}h {{ countdown.m }}m</template>
            <template v-else>en {{ countdown.m }} min</template>
          </div>

          <div class="dhero__row">
            <select class="dhero__status" :value="anime.status || ''"
                    :style="{ color: ANIME_STATUS[anime.status]?.color || 'var(--ink-faint)' }"
                    @change="store.setStatus(anime, $event.target.value)">
              <option value="">Sin estado</option>
              <option v-for="(v, k) in ANIME_STATUS" :key="k" :value="k">{{ v.label }}</option>
            </select>
            <a v-if="anime.al_id" :href="`https://anilist.co/anime/${anime.al_id}`" target="_blank" rel="noopener" class="dhero__link">AniList</a>
            <a v-if="anime.mal_id" :href="`https://myanimelist.net/anime/${anime.mal_id}`" target="_blank" rel="noopener" class="dhero__link">MAL</a>
            <a v-if="malUrl" :href="malUrl + '/userrec'" target="_blank" rel="noopener" class="dhero__link" title="Recomendaciones de la comunidad MAL">Comunidad</a>
          </div>

          <div class="dhero__mgmt">
            <!-- Preview (no en biblioteca): agregar + torrents -->
            <template v-if="isPreview">
              <button v-if="!inLibrary" class="mbtn mbtn--accent" @click="store.addToLibrary(anime)" title="Añadir a Mi Anime">
                <Icon name="plus" :size="14" /> Agregar a Mi Anime
              </button>
              <button v-else class="mbtn mbtn--in" disabled><Icon name="check" :size="14" /> En Mi Anime</button>
              <button v-if="anime.al_id" class="mbtn" @click="store.openTorrents(anime)" title="Buscar torrents">+ Torrents</button>
            </template>
            <!-- Biblioteca: gestión completa -->
            <template v-else>
              <button v-if="anime.al_id" class="mbtn mbtn--accent" @click="store.openTorrents(anime)" title="Buscar torrents">+ Torrents</button>
              <button class="mbtn" @click="store.openCoverPicker(anime)" title="Cambiar portada o fondo">Cambiar portada</button>
              <button class="mbtn" @click="store.openLinkTorrent()" title="Enlazar torrent de qBittorrent">Enlazar</button>
              <button class="mbtn" @click="store.clearEpisodes(anime)" title="Borrar episodios para liberar espacio">Borrar eps<span v-if="diskSize" class="mbtn__sz">{{ formatBytes(diskSize) }}</span></button>
              <button class="mbtn mbtn--danger" @click="store.removeFromLibrary(anime.id)" title="Eliminar serie">Eliminar</button>
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
    <!-- Continuar viendo: salta directo al próximo episodio sin bajar a la lista -->
    <section v-if="resumeEp" class="dresume" @click="store.play(anime, resumeEp)">
      <div class="dresume__thumb">
        <img v-if="!resumeThumbFailed" :src="`/api/anime/thumb/${anime.id}/${resumeEp.num}`" :alt="'Ep ' + resumeEp.num"
             loading="lazy" @error="resumeThumbFailed = true" />
        <img v-else-if="anime.cover" :src="imgProxy(anime.cover)" :alt="anime.title" />
        <div class="dresume__scrim" />
        <div class="dresume__play"><Icon name="play" :size="24" /></div>
        <div v-if="resumePct" class="dresume__bar"><span :style="{ width: resumePct + '%' }" /></div>
      </div>
      <div class="dresume__info">
        <span class="dresume__eyebrow">{{ resumePct ? 'CONTINUAR VIENDO' : 'SIGUIENTE EPISODIO' }}</span>
        <span class="dresume__ep">Episodio {{ resumeEp.num }}</span>
        <span v-if="resumeTitle && resumeTitle !== 'Episodio ' + resumeEp.num" class="dresume__t">{{ resumeTitle }}</span>
      </div>
      <button class="dresume__btn"><Icon name="play" :size="16" /> {{ resumePct ? 'Continuar' : 'Reproducir' }}</button>
    </section>

    <div class="eptoolbar">
      <span class="eptoolbar__lbl">{{ mainEps.length }} episodios</span>
      <div class="epseg">
        <button :class="{ 'is-on': epView === 'grid' }" title="Cuadrícula" @click="setEpView('grid')"><Icon name="library" :size="15" /></button>
        <button :class="{ 'is-on': epView === 'list' }" title="Lista" @click="setEpView('list')"><Icon name="menu" :size="15" /></button>
      </div>
    </div>

    <div v-if="epView === 'list'" class="eplist">
      <EpisodeRow v-for="ep in mainEps" :key="ep.num" :anime="anime" :ep="ep" :batch="batch" :current="isCurrent(ep)" />
    </div>
    <div v-else class="epgrid">
      <EpisodeCard v-for="ep in mainEps" :key="ep.num" :anime="anime" :ep="ep" :batch="batch" />
    </div>

    <template v-if="specials.length">
      <div class="epgrid__sep"><Icon name="spark" :size="14" /> Especiales / Extras</div>
      <div v-if="epView === 'list'" class="eplist">
        <EpisodeRow v-for="ep in specials" :key="'sp-' + ep.num" :anime="anime" :ep="ep" :batch="batch" />
      </div>
      <div v-else class="epgrid">
        <EpisodeCard v-for="ep in specials" :key="'sp-' + ep.num" :anime="anime" :ep="ep" :batch="batch" />
      </div>
    </template>
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
            <span class="dinfo__v">{{ anime.genres.join(', ') }}</span>
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

    <!-- Pestaña Relacionados: recomendaciones + listas MAL -->
    <template v-else>
      <section v-if="stacks.length" class="disc" style="margin-top: 0">
        <h3 class="disc__title">Listas de interés (MAL)</h3>
        <div class="chips">
          <button v-for="st in stacks" :key="st.id" class="chip chip--stack" @click="store.browseStack(st)">{{ st.name }}</button>
        </div>
      </section>

      <section v-if="recs.length" class="disc" :style="stacks.length ? {} : { marginTop: 0 }">
        <h3 class="disc__title">Recomendaciones</h3>
        <div class="recgrid">
          <article v-for="r in recs" :key="r.al_id" class="rec" @click="store.openRec(r)">
            <div class="rec__poster">
              <img v-if="r.cover" :src="imgProxy(r.cover)" :alt="r.title" loading="lazy" />
              <div class="rec__scrim" />
              <span v-if="r.score" class="rec__score">★ {{ (r.score / 10).toFixed(1) }}</span>
              <span v-if="store.isInLibrary(r)" class="rec__in"><Icon name="check" :size="10" /></span>
              <div class="rec__ov"><span class="rec__t">{{ r.title }}</span></div>
            </div>
          </article>
        </div>
      </section>
      <p v-if="!recs.length && !stacks.length" class="dinfo__none">Sin relacionados todavía.</p>
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
            <article v-for="a in store.stackBrowseAnime" :key="a.al_id || a.mal_id" class="rec" @click="store.openRec(a); store.closeStackBrowse()">
              <div class="rec__poster"><img v-if="a.cover" :src="imgProxy(a.cover)" :alt="a.title" loading="lazy" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
            </article>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Tag browse overlay -->
    <Teleport to="body">
      <div v-if="store.tagBrowse" class="ov" @click.self="store.closeTagBrowse()">
        <div class="bmodal">
          <button class="bmodal__x" @click="store.closeTagBrowse()"><Icon name="close" :size="18" /></button>
          <header class="bmodal__head"><h2>{{ store.tagBrowse }} <span class="muted">en AniList</span></h2></header>
          <div v-if="store.tagBrowseState === 'loading'" class="center"><Spinner /></div>
          <div v-else class="bgrid">
            <article v-for="a in store.tagBrowseAnime" :key="a.al_id" class="rec" @click="store.openRec(a); store.closeTagBrowse()">
              <div class="rec__poster"><img v-if="a.cover" :src="imgProxy(a.cover)" :alt="a.title" loading="lazy" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
            </article>
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
              <img :src="o.url" :alt="o.label" loading="lazy" />
              <span class="coveropt__label">{{ o.label }}</span>
              <span v-if="o.url === activeTab.current" class="coveropt__current"><Icon name="check" :size="12" /></span>
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.detail { position: relative; padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }

.detail__back { display: inline-flex; align-items: center; gap: var(--s-1); margin: var(--s-2) 0 var(--s-5);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft);
  border: 1px solid var(--line); background: var(--glass); backdrop-filter: blur(8px); font-size: var(--fs-sm); transition: all var(--t-fast); }
.detail__back:hover { color: var(--ink); border-color: var(--line-strong); }

/* Hero header — Crunchyroll-style wide HD art + logo, same recipe as HeroBanner.vue */
.dhero { position: relative; margin: 0 0 var(--s-7); height: clamp(500px, 58vw, 680px);
  border-radius: var(--r-xl); overflow: hidden; background: var(--surface); }
.dhero__bg { position: absolute; inset: 0; }
.dhero__img { position: absolute; inset: 0; background-size: cover; background-position: center 18%; }
/* No wide banner cached yet → fall back to the (vertical) cover, blurred to fill the frame. */
.dhero.is-cover .dhero__img { filter: blur(28px) saturate(1.15) brightness(.85); transform: scale(1.18); }
.dhero__probe { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.dhero__shade { position: absolute; inset: 0;
  background:
    linear-gradient(90deg, rgba(7,10,18,.92) 0%, rgba(7,10,18,.62) 38%, rgba(7,10,18,.15) 70%, transparent 100%),
    linear-gradient(0deg, rgba(7,10,18,.95) 0%, rgba(7,10,18,.30) 32%, transparent 60%); }

.dhero__inner { position: relative; z-index: 1; height: 100%; display: flex; align-items: flex-end;
  gap: var(--s-5); max-width: 900px; padding: var(--s-6) var(--s-7); }
/* natural aspect (height auto) + flex-shrink:0 → poster shown whole, not cropped or squeezed. */
.dhero__poster { width: 168px; height: auto; border-radius: var(--r-md); box-shadow: var(--shadow-lg); border: 1px solid rgba(255,255,255,.16); flex-shrink: 0;
  view-transition-name: detail-poster;   /* destino del morph desde la card (lib/vt.js) */ }
.dhero__col { display: flex; flex-direction: column; gap: var(--s-3); min-width: 0; }
.dhero__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--cyan); }
.dhero__title { font-family: var(--font-display); font-weight: 700; color: #fff; font-size: clamp(2rem, 4vw, 3.4rem);
  line-height: 1.06; text-shadow: 0 2px 24px rgba(0,0,0,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dhero__logo { max-width: min(520px, 80%); max-height: clamp(100px, 14vw, 180px); width: auto; height: auto;
  object-fit: contain; object-position: left bottom; filter: drop-shadow(0 4px 20px rgba(0,0,0,.65)); }

.dhero__stats { display: flex; flex-direction: column; gap: var(--s-2); max-width: 360px; }
.dhero__count { font-size: var(--fs-sm); color: var(--ice); text-shadow: 0 1px 8px rgba(0,0,0,.7); }
.dhero__count strong { color: #fff; font-family: var(--font-display); }
.dhero__disk { display: inline-flex; align-items: center; gap: 4px; margin-left: var(--s-2); padding: 1px 8px;
  border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--ice);
  background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.16); }
.dhero__disk :deep(svg) { color: var(--cyan); }
.dhero__bar { height: 4px; border-radius: var(--r-pill); background: rgba(255,255,255,.22); overflow: hidden; }
.dhero__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--azure-deep), var(--azure)); }

.dhero__airing { display: inline-flex; align-items: center; gap: var(--s-2); width: fit-content;
  font-size: var(--fs-xs); color: var(--ice); padding: 4px 12px; border-radius: var(--r-pill);
  background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(6px); }
.dhero__airing-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); box-shadow: var(--glow-cyan); animation: pulse-live 2s var(--ease-drift) infinite; }

.dhero__row { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.dhero__status { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(8px); font-size: var(--fs-sm); font-weight: 500; cursor: pointer; }
.dhero__link { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(8px); color: var(--ink); font-size: var(--fs-sm); transition: all var(--t-fast); }
.dhero__link:hover { color: #fff; border-color: var(--azure); background: rgba(255,255,255,.2); }

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
.dtab__badge { margin-left: 7px; padding: 1px 7px; border-radius: var(--r-pill);
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700;
  color: var(--ink-soft); background: var(--surface-2); border: 1px solid var(--line); }

/* Pestaña Detalles */
.dinfo { display: grid; grid-template-columns: 1fr minmax(15rem, 20rem); gap: var(--s-7); align-items: start; }
.dinfo__syn { font-size: var(--fs-md); line-height: var(--lh-relaxed, 1.7); color: var(--ink-soft);
  max-width: 62ch; white-space: pre-line; }
.dinfo__none { color: var(--ink-faint); font-size: var(--fs-sm); }
.dinfo__side { display: flex; flex-direction: column; gap: var(--s-3); padding: var(--s-4);
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); }
.dinfo__row { display: flex; flex-direction: column; gap: 2px; }
.dinfo__k { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--ink-faint); }
.dinfo__v { font-size: var(--fs-sm); color: var(--ink); }
@media (max-width: 640px) { .dinfo { grid-template-columns: 1fr; } }

/* Continuar viendo — franja horizontal antes de la lista de episodios */
.dresume {
  display: flex; align-items: center; gap: var(--s-4); margin: 0 0 var(--s-6);
  padding: var(--s-3); border-radius: var(--r-lg); cursor: pointer;
  background: linear-gradient(100deg, color-mix(in srgb, var(--azure) 12%, var(--surface)), var(--surface) 70%);
  border: 1px solid var(--line-2); transition: border-color var(--t-base), box-shadow var(--t-base), transform var(--t-base) var(--ease-snap);
}
.dresume:hover { border-color: var(--azure-glow); box-shadow: var(--shadow-md); transform: translateY(-2px); }
.dresume__thumb { position: relative; flex-shrink: 0; width: 200px; aspect-ratio: 16/9; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.dresume__thumb img { width: 100%; height: 100%; object-fit: cover; }
.dresume__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.1), rgba(5,7,13,.55)); }
.dresume__play { position: absolute; inset: 0; display: grid; place-items: center; color: #fff; transition: transform var(--t-base) var(--ease-snap); }
.dresume__play :deep(svg) { filter: drop-shadow(0 2px 8px rgba(0,0,0,.7)); }
.dresume:hover .dresume__play { transform: scale(1.14); }
.dresume__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.4); }
.dresume__bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: 0 0 6px var(--azure-glow); }
.dresume__info { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.dresume__eyebrow { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--cyan); }
.dresume__ep { font-family: var(--font-display); font-weight: 600; font-size: var(--fs-lg); color: var(--ink); }
.dresume__t { font-size: var(--fs-sm); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dresume__btn { flex-shrink: 0; display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5);
  border-radius: var(--r-md); background: var(--azure); color: #fff; font-size: var(--fs-sm); font-weight: 600; transition: all var(--t-fast); }
.dresume__btn:hover { background: var(--azure-bright); box-shadow: var(--glow-azure); }

.eptoolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); margin: 0 0 var(--s-4); }
.eptoolbar__lbl { font-family: var(--font-display); font-size: var(--fs-lg); font-weight: 600; color: var(--ink); }
.epseg { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.epseg button { width: 34px; height: 30px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-faint); transition: all var(--t-fast); }
.epseg button:hover { color: var(--ink); }
.epseg button.is-on { background: var(--surface-3); color: var(--azure-bright); }

.eplist { display: flex; flex-direction: column; gap: var(--s-2); }
.epgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(19rem, 1fr)); gap: var(--s-5); }
.epgrid__sep { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-7) 0 var(--s-4); color: var(--ink-soft); font-family: var(--font-display); font-weight: 600; }
.epgrid__sep :deep(svg) { color: var(--gold); }

/* management */
.dhero__mgmt { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.mbtn { padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink);
  background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.16); backdrop-filter: blur(8px); transition: all var(--t-fast); }
.mbtn:hover { color: #fff; border-color: rgba(255,255,255,.32); background: rgba(255,255,255,.18); }
.mbtn--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.mbtn__sz { margin-left: 6px; padding: 1px 6px; border-radius: var(--r-pill); font-family: var(--font-mono);
  font-size: var(--fs-2xs); color: var(--cyan); background: rgba(255,255,255,.1); }
.mbtn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.mbtn--accent:hover { background: var(--azure-bright); color: #fff; }
.mbtn--accent, .mbtn--in { display: inline-flex; align-items: center; gap: 5px; }
.mbtn--in { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); background: color-mix(in srgb, var(--jade) 12%, transparent); opacity: 1; }

.linkpanel { margin: 0 0 var(--s-6); padding: var(--s-4); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.linkpanel__head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: var(--s-3); }
.linkpanel__head button { color: var(--ink-faint); }
.linkpanel__sub { width: 100%; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); margin-bottom: var(--s-3); }
.linkpanel__empty { color: var(--ink-faint); font-size: var(--fs-sm); text-align: center; padding: var(--s-3); }
.linkpanel__list { display: flex; flex-direction: column; gap: var(--s-1); max-height: 240px; overflow-y: auto; }
.linkitem { display: flex; flex-direction: column; gap: 2px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line); text-align: left; transition: all var(--t-fast); }
.linkitem:hover { border-color: var(--azure); }
.linkitem__name { font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.linkitem__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
.center-sm { display: grid; place-items: center; padding: var(--s-3); }

/* discovery sections */
.disc { margin-top: var(--s-7); }
.disc__title { font-family: var(--font-display); font-size: var(--fs-lg); margin-bottom: var(--s-3); }
.chips { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.chip { display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.chip:hover { color: var(--ink); border-color: var(--azure); }
.chip__rank { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.chip--stack { color: var(--violet); border-color: color-mix(in srgb, var(--violet) 30%, transparent); }
.chip--stack:hover { background: color-mix(in srgb, var(--violet) 12%, transparent); }

.recgrid, .bgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.125rem, 1fr)); gap: var(--s-4); }
.rec { cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.rec:hover { transform: translateY(-5px); }
.rec__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.rec:hover .rec__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-md); }
.rec__poster img { width: 100%; height: 100%; object-fit: cover; }
.rec__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 45%, rgba(5,7,13,.92)); }
.rec__score { position: absolute; top: 6px; left: 6px; font-size: var(--fs-2xs); font-weight: 700; color: var(--gold); padding: 2px 6px; border-radius: var(--r-pill); background: rgba(7,10,18,.6); }
.rec__in { position: absolute; top: 6px; right: 6px; width: 18px; height: 18px; display: grid; place-items: center; border-radius: 50%; background: var(--jade); color: #fff; }
.rec__ov { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-2); }
.rec__t { font-size: var(--fs-2xs); font-weight: 600; color: #fff; line-height: 1.25; text-shadow: 0 1px 4px rgba(0,0,0,.7); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* browse overlays */
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.bmodal { position: relative; width: min(48.75rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); padding: var(--s-5); overflow-y: auto; }
.bmodal__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 32px; height: 32px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.bmodal__head { margin-bottom: var(--s-4); padding-right: var(--s-7); }
.bmodal__head h2 { font-size: var(--fs-xl); }
.bmodal__head .muted { color: var(--ink-faint); font-weight: 400; }
.bmodal__link { font-size: var(--fs-xs); color: var(--azure-bright); }
.bmodal__desc { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: var(--s-2); }
.center { display: grid; place-items: center; padding: var(--s-7); }

/* context menu */
.ctx-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); }
.ctx { position: fixed; display: flex; flex-direction: column; min-width: 200px; padding: var(--s-1); border-radius: var(--r-md); background: var(--glass-strong); backdrop-filter: blur(16px); border: 1px solid var(--line-2); box-shadow: var(--shadow-lg); }
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
.coveropt__current { position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; display: grid; place-items: center; border-radius: 50%; background: var(--azure); color: #fff; }

@media (max-width: 640px) {
  .detail { padding: var(--s-3) var(--s-4) var(--s-8); }
  .dhero { height: clamp(380px, 72vw, 480px); border-radius: var(--r-lg); }
  .dhero__inner { padding: var(--s-5) var(--s-4); }
  .dhero__poster { display: none; }
  .dhero__logo { max-width: 70%; max-height: 90px; }
  .epgrid { grid-template-columns: repeat(auto-fill, minmax(13.5rem, 1fr)); gap: var(--s-3); }
  .dresume__thumb { width: 128px; }
  .dresume__btn { padding: var(--s-2) var(--s-3); }
}
</style>
