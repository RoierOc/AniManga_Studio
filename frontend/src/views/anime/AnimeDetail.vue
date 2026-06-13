<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, animeFormatLabel, batchInfo, fmtCountdown } from '@/lib/anime'
import EpisodeCard from '@/components/anime/EpisodeCard.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
const anime = computed(() => store.detail)

// openDetail() always pushes one history entry, so back consumes it and runs the
// guarded restore. Fall back to a direct close if there's no app history.
function goBack() {
  if (window.history.state && window.history.state.pos > 0) window.history.back()
  else store.closeDetail()
}

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
</script>

<template>
  <div v-if="anime" class="detail">
    <!-- aura backdrop from cover -->
    <div class="detail__aura" :style="anime.cover ? `background-image:url('${anime.cover}')` : ''" />

    <button class="detail__back" @click="goBack">
      <Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" /> Volver
    </button>

    <header class="detail__head stagger">
      <img v-if="anime.cover" :src="anime.cover" class="detail__cover" :alt="anime.title" style="--i:0" />
      <div class="detail__meta" style="--i:1">
        <span class="detail__fmt">{{ animeFormatLabel(anime.format) }}</span>
        <h1 class="detail__title">{{ anime.title }}</h1>

        <div class="detail__stats">
          <span class="detail__count"><strong>{{ done }}</strong> / {{ total || '?' }} episodios</span>
          <div class="detail__bar"><span :style="{ width: pct + '%' }" /></div>
        </div>

        <div v-if="countdown" class="detail__airing">
          <span class="detail__airing-dot" />
          Ep {{ countdown.episode }}
          <template v-if="countdown.d > 0">en {{ countdown.d }}d {{ countdown.h }}h</template>
          <template v-else-if="countdown.h > 0">en {{ countdown.h }}h {{ countdown.m }}m</template>
          <template v-else>en {{ countdown.m }} min</template>
        </div>

        <div class="detail__row">
          <select class="detail__status" :value="anime.status || ''"
                  :style="{ color: ANIME_STATUS[anime.status]?.color || 'var(--ink-faint)' }"
                  @change="store.setStatus(anime, $event.target.value)">
            <option value="">Sin estado</option>
            <option v-for="(v, k) in ANIME_STATUS" :key="k" :value="k">{{ v.label }}</option>
          </select>
          <a v-if="anime.al_id" :href="`https://anilist.co/anime/${anime.al_id}`" target="_blank" rel="noopener" class="detail__link">AniList</a>
          <a v-if="anime.mal_id" :href="`https://myanimelist.net/anime/${anime.mal_id}`" target="_blank" rel="noopener" class="detail__link">MAL</a>
          <a v-if="malUrl" :href="malUrl + '/userrec'" target="_blank" rel="noopener" class="detail__link" title="Recomendaciones de la comunidad MAL">Comunidad</a>
        </div>

        <div class="detail__mgmt">
          <button v-if="anime.al_id" class="mbtn mbtn--accent" @click="store.openTorrents(anime)" title="Buscar torrents">+ Torrents</button>
          <button class="mbtn" @click="store.openRename(anime)" title="Renombrar episodios">Renombrar</button>
          <button class="mbtn" @click="store.openLinkTorrent()" title="Enlazar torrent de qBittorrent">Enlazar</button>
          <button class="mbtn" @click="store.clearEpisodes(anime)" title="Borrar episodios">Borrar eps</button>
          <button class="mbtn mbtn--danger" @click="store.removeFromLibrary(anime.id)" title="Eliminar serie">Eliminar</button>
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

    <div class="epgrid">
      <EpisodeCard v-for="ep in mainEps" :key="ep.num" :anime="anime" :ep="ep" :batch="batch" />
    </div>

    <template v-if="specials.length">
      <div class="epgrid__sep"><Icon name="spark" :size="14" /> Especiales / Extras</div>
      <div class="epgrid">
        <EpisodeCard v-for="ep in specials" :key="'sp-' + ep.num" :anime="anime" :ep="ep" :batch="batch" />
      </div>
    </template>

    <!-- Tags -->
    <section v-if="tags.length" class="disc">
      <h3 class="disc__title">Tags</h3>
      <div class="chips">
        <button v-for="t in tags.slice(0, 18)" :key="t.name" class="chip" @click="store.browseByTag(t.name, alId)">
          {{ t.name }}<span v-if="t.rank" class="chip__rank">{{ t.rank }}%</span>
        </button>
      </div>
    </section>

    <!-- MAL Stacks -->
    <section v-if="stacks.length" class="disc">
      <h3 class="disc__title">Listas de interés (MAL)</h3>
      <div class="chips">
        <button v-for="st in stacks" :key="st.id" class="chip chip--stack" @click="store.browseStack(st)">{{ st.name }}</button>
      </div>
    </section>

    <!-- AniList recommendations -->
    <section v-if="recs.length" class="disc">
      <h3 class="disc__title">Recomendaciones</h3>
      <div class="recgrid">
        <article v-for="r in recs" :key="r.al_id" class="rec" @click="store.openRec(r)">
          <div class="rec__poster">
            <img v-if="r.cover" :src="r.cover" :alt="r.title" loading="lazy" />
            <div class="rec__scrim" />
            <span v-if="r.score" class="rec__score">★ {{ (r.score / 10).toFixed(1) }}</span>
            <span v-if="store.isInLibrary(r)" class="rec__in"><Icon name="check" :size="10" /></span>
            <div class="rec__ov"><span class="rec__t">{{ r.title }}</span></div>
          </div>
        </article>
      </div>
    </section>

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
              <div class="rec__poster"><img v-if="a.cover" :src="a.cover" :alt="a.title" loading="lazy" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
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
              <div class="rec__poster"><img v-if="a.cover" :src="a.cover" :alt="a.title" loading="lazy" /><div class="rec__scrim" /><span v-if="a.score" class="rec__score">★ {{ (a.score/10).toFixed(1) }}</span><div class="rec__ov"><span class="rec__t">{{ a.title }}</span></div></div>
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

    <!-- Rename preview modal -->
    <Teleport to="body">
      <div v-if="store.rename" class="ov" @click.self="store.rename = null">
        <div class="bmodal bmodal--rename">
          <button class="bmodal__x" @click="store.rename = null"><Icon name="close" :size="18" /></button>
          <header class="bmodal__head"><h2>Renombrar episodios</h2></header>
          <div class="renlist">
            <div v-for="(r, i) in store.rename.items" :key="i" class="renrow" :class="{ 'is-changed': r.changed }">
              <span class="renrow__old">{{ r.old_name }}</span>
              <Icon name="chevron" :size="13" />
              <span class="renrow__new">{{ r.new_name }}</span>
            </div>
          </div>
          <div class="renfoot">
            <button class="mbtn" @click="store.rename = null">Cancelar</button>
            <button class="mbtn mbtn--accent" :disabled="store.renameBusy" @click="store.applyRename()">{{ store.renameBusy ? 'Aplicando…' : 'Aplicar' }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.detail { position: relative; padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }
.detail__aura {
  position: absolute; top: 0; left: 0; right: 0; height: 420px; z-index: -1;
  background-size: cover; background-position: center 25%;
  -webkit-mask-image: linear-gradient(180deg, rgba(0,0,0,.4), transparent 90%);
  mask-image: linear-gradient(180deg, rgba(0,0,0,.4), transparent 90%);
  filter: blur(30px) saturate(1.1); opacity: .35; transform: scaleY(-1);
}

.detail__back { display: inline-flex; align-items: center; gap: var(--s-1); margin: var(--s-2) 0 var(--s-5);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft);
  border: 1px solid var(--line); background: var(--glass); backdrop-filter: blur(8px); font-size: var(--fs-sm); transition: all var(--t-fast); }
.detail__back:hover { color: var(--ink); border-color: var(--line-strong); }

.detail__head { display: flex; gap: var(--s-5); margin-bottom: var(--s-7); }
.detail__cover { width: 180px; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-lg); border: 1px solid var(--line-2); flex-shrink: 0; overflow: hidden; }
.detail__meta { display: flex; flex-direction: column; gap: var(--s-3); padding-top: var(--s-3); min-width: 0; }
.detail__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); }
.detail__title { font-size: var(--fs-3xl); }
.detail__stats { display: flex; flex-direction: column; gap: var(--s-2); max-width: 360px; }
.detail__count { font-size: var(--fs-sm); color: var(--ink-soft); }
.detail__count strong { color: var(--ink); font-family: var(--font-display); }
.detail__bar { height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.detail__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--azure-deep), var(--azure)); }

.detail__airing { display: inline-flex; align-items: center; gap: var(--s-2); width: fit-content;
  font-size: var(--fs-xs); color: var(--ice); padding: 4px 12px; border-radius: var(--r-pill);
  background: var(--azure-haze); border: 1px solid var(--line-2); }
.detail__airing-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); box-shadow: var(--glow-cyan); animation: pulse-live 2s var(--ease-drift) infinite; }

.detail__row { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-1); }
.detail__status { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); font-size: var(--fs-sm); font-weight: 500; cursor: pointer; }
.detail__link { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-soft); font-size: var(--fs-sm); transition: all var(--t-fast); }
.detail__link:hover { color: var(--azure-bright); border-color: var(--azure); }

.epgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: var(--s-4); }
.epgrid__sep { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-7) 0 var(--s-4); color: var(--ink-soft); font-family: var(--font-display); font-weight: 600; }
.epgrid__sep :deep(svg) { color: var(--gold); }

/* management */
.detail__mgmt { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-2); }
.mbtn { padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.mbtn:hover { color: var(--ink); border-color: var(--line-strong); }
.mbtn--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.mbtn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.mbtn--accent:hover { background: var(--azure-bright); color: #fff; }

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

/* context menu + rename */
.ctx-backdrop { position: fixed; inset: 0; z-index: var(--z-modal); }
.ctx { position: fixed; display: flex; flex-direction: column; min-width: 200px; padding: var(--s-1); border-radius: var(--r-md); background: var(--glass-strong); backdrop-filter: blur(16px); border: 1px solid var(--line-2); box-shadow: var(--shadow-lg); }
.ctx button { text-align: left; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); }
.ctx button:hover { background: var(--surface-2); color: var(--ink); }
.bmodal--rename { width: min(38.75rem, 100%); }
.renlist { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.renrow { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2); border-radius: var(--r-xs); font-size: var(--fs-xs); opacity: .55; }
.renrow.is-changed { opacity: 1; }
.renrow__old { color: var(--ink-faint); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.renrow__new { color: var(--azure-bright); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.renfoot { display: flex; justify-content: flex-end; gap: var(--s-2); margin-top: var(--s-4); }

@media (max-width: 640px) {
  .detail { padding: var(--s-3) var(--s-4) var(--s-8); }
  .detail__head { flex-direction: column; }
  .detail__cover { width: 130px; }
  .epgrid { grid-template-columns: repeat(auto-fill, minmax(11.25rem, 1fr)); gap: var(--s-3); }
}
</style>
