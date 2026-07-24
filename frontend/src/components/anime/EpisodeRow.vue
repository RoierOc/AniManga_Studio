<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeEpLabel, isEpisodePlayable } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  anime: { type: Object, required: true },
  ep: { type: Object, required: true },
  batch: { type: Object, required: true },
  current: { type: Boolean, default: false },   // episodio "en curso" (coincide con el atajo de arriba)
})
const store = useAnimeStore()

const playable = computed(() => isEpisodePlayable(props.ep, props.batch))
const downloading = computed(() =>
  (props.ep.in_qbt && props.ep.progress < 100 && !props.batch.hasBatch) ||
  (props.ep.num > 0 && props.batch.hasBatch && !props.batch.batchDone)
)
const dlPct = computed(() => props.batch.hasBatch ? (props.batch.batchEp?.progress || 0) : props.ep.progress)
const resumePct = computed(() => {
  if (props.ep.watched || !props.ep.resume_pos || !props.ep.duration || downloading.value) return 0
  return Math.min(100, (props.ep.resume_pos / props.ep.duration) * 100)
})

const meta = computed(() => store.epMeta[props.anime.id]?.[props.ep.num] || null)
const infoOpen = computed(() => store.epInfoOpen === `${props.anime.id}_${props.ep.num}`)
const jikanInfo = computed(() => store.epInfo[`${props.anime.mal_id}_${props.ep.num}`])
const info = computed(() => {
  if (meta.value?.overview) return { title: meta.value.title, synopsis: meta.value.overview, aired: meta.value.aired }
  return jikanInfo.value
})

const subKey = computed(() => store.subKey(props.anime, props.ep))
const subTask = computed(() => store.subTasks[subKey.value])
const subRunning = computed(() => subTask.value && ['starting', 'injecting', 'translating', 'downloading', 'running'].includes(subTask.value.status))
const subFetching = computed(() => store.subFetching === subKey.value)

// Título real del episodio (TMDB/MAL) si lo tenemos; si no, la etiqueta derivada del archivo.
const epTitle = computed(() => {
  if (props.ep.ep_type !== 'special' && meta.value?.title) return meta.value.title
  return animeEpLabel(props.anime, props.ep)
})

function onPlay() { if (playable.value) store.play(props.anime, props.ep) }
</script>

<template>
  <div class="eprow" :class="{ 'eprow--watched': ep.watched, 'eprow--dl': downloading, 'eprow--missing': !playable && !downloading, 'eprow--current': current }"
       @mouseenter.once="store.loadSkip(anime, ep)">
    <div class="eprow__main">
      <div class="eprow__thumb" @click="onPlay">
        <div class="eprow__bg" :style="anime.cover ? `background-image:url('${imgProxy(anime.cover, 120)}')` : ''" />
        <img v-if="playable || ep.has_thumb" class="eprow__img" :src="`/api/anime/thumb/${anime.id}/${ep.num}${ep.ep_type === 'special' ? '?special=1' : ''}`"
             loading="lazy" decoding="async" @load="$event.target.classList.add('is-loaded')" @error="$event.target.style.display='none'" alt="" />
        <div class="eprow__num"><span class="eprow__num-k">{{ ep.ep_type === 'special' ? 'SP' : 'EP' }}</span><span class="eprow__num-v">{{ String(ep.num).padStart(2, '0') }}</span></div>
        <!-- resume / download / finished bar -->
        <div v-if="resumePct" class="eprow__bar eprow__bar--resume"><span :style="{ width: resumePct + '%' }" /></div>
        <div v-else-if="downloading" class="eprow__bar eprow__bar--dl"><span :style="{ width: dlPct + '%' }" /></div>
        <div v-if="playable" class="eprow__play"><Icon name="play" :size="24" /></div>
      </div>

      <div class="eprow__body">
        <div class="eprow__titrow">
          <span v-if="current" class="eprow__badge">EN CURSO</span>
          <span class="eprow__title">{{ epTitle }}</span>
        </div>

        <!-- subtitle translation progress -->
        <div v-if="playable && (subRunning || subTask?.status === 'done')" class="eprow__sub" :class="{ 'is-done': subTask?.status === 'done' }">
          <template v-if="subTask?.status === 'done'"><Icon name="check" :size="12" /> ESP ✓</template>
          <template v-else>
            <span class="eprow__sub-bar"><span :style="{ width: (subTask?.progress || 0) + '%' }" /></span>
            <span class="eprow__sub-msg">{{ subTask?.engine === 'ollama' ? '🦙' : '✨' }} {{ subTask?.progress || 0 }}%</span>
            <button class="eprow__sub-x" @click.stop="store.cancelTranslate(anime, ep)"><Icon name="close" :size="11" /></button>
          </template>
        </div>
        <div v-else-if="downloading" class="eprow__status">↓ Descargando {{ Math.round(dlPct) }}%</div>
        <div v-else-if="!playable" class="eprow__status eprow__status--muted">Sin descargar</div>
        <div v-else-if="resumePct" class="eprow__status eprow__status--resume">Reanudar · {{ Math.round(resumePct) }}%</div>
      </div>

      <div class="eprow__acts">
        <template v-if="playable">
          <button v-if="!subRunning && subTask?.status !== 'done'" class="eprow__icon eprow__icon--sub" :disabled="subFetching"
                  :title="subFetching ? 'Buscando…' : 'Subtítulos en español'" @click.stop="store.translateSubs(anime, ep)">
            <Spinner v-if="subFetching" :size="12" tone="ok" /><span v-else class="eprow__sub-lbl">ES</span>
          </button>
          <button v-if="subTask?.status === 'error' || subTask?.status === 'done'" class="eprow__icon eprow__icon--retry"
                  :title="subTask?.status === 'error' ? 'Reintentar' : 'Re-inyectar subtítulo'"
                  @click.stop="store.translateSubs(anime, ep)">
            <Icon name="spark" :size="13" />
          </button>
          <button v-if="anime.mal_id || meta?.overview" class="eprow__icon" :class="{ 'is-on': infoOpen }" title="Descripción"
                  @click.stop="store.loadEpInfo(anime, ep)">
            <span class="eprow__i">i</span>
          </button>
          <button class="eprow__icon" :class="{ 'is-on': ep.watched }" :title="ep.watched ? 'No visto' : 'Visto'"
                  @click.stop="store.toggleWatched(anime, ep)">
            <Icon name="check" :size="14" />
          </button>
          <button v-if="ep.in_local" class="eprow__icon" title="Cambiar tipo de episodio"
                  @click.stop="store.openEpOverrideMenu($event, anime, ep)">
            <span class="eprow__dots3">⋮</span>
          </button>
          <button v-if="!ep.in_local" class="eprow__icon eprow__icon--danger" title="Borrar"
                  @click.stop="store.deleteEpisode(anime, ep)">
            <Icon name="close" :size="13" />
          </button>
        </template>
        <button v-else-if="!downloading" class="eprow__searchbtn" :title="'Buscar torrent para Ep. ' + ep.num"
                @click.stop="store.openTorrents(anime, ep.num)">
          <Icon name="search" :size="13" /> Buscar
        </button>
      </div>
    </div>

    <!-- expandable episode info -->
    <Transition name="info">
      <div v-if="infoOpen" class="eprow__info">
        <div v-if="info === null" class="eprow__info-load"><span class="dot" /><span class="dot" /><span class="dot" /></div>
        <template v-else-if="info?.synopsis">
          <div v-if="info.title" class="eprow__info-title">{{ info.title }}</div>
          <p class="eprow__info-syn">{{ info.synopsis }}</p>
          <div class="eprow__info-meta">
            <span>{{ info.aired }}</span>
            <span v-if="info.filler" class="eprow__ibadge eprow__ibadge--filler">Filler</span>
            <span v-if="info.recap" class="eprow__ibadge eprow__ibadge--recap">Recap</span>
          </div>
        </template>
        <div v-else class="eprow__info-empty">Sin descripción disponible</div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.eprow { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); overflow: hidden;
  transition: border-color var(--t-base) var(--ease-silk), box-shadow var(--t-base); }
.eprow:hover { border-color: var(--line-strong); box-shadow: var(--shadow-sm); }
.eprow--current { border-color: var(--azure); box-shadow: inset 0 0 0 1px var(--azure-glow); }
.eprow--watched { background: color-mix(in srgb, var(--surface) 92%, transparent); }

.eprow__main { display: flex; align-items: stretch; gap: var(--s-3); padding: var(--s-2); }

.eprow__thumb { position: relative; flex-shrink: 0; width: 10rem; aspect-ratio: 16/9; border-radius: var(--r-sm); overflow: hidden; cursor: pointer; }
.eprow--missing .eprow__thumb, .eprow--dl .eprow__thumb { cursor: default; }
.eprow__bg { position: absolute; inset: 0; background-size: cover; background-position: center; filter: blur(16px) brightness(.4); transform: scale(1.2); }
.eprow__img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.eprow__img.is-loaded { opacity: 1; }
.eprow__thumb:hover .eprow__img { transform: scale(1.06); }
/* Episodio visto → portada atenuada (nítida pero apagada), sin blur; al hover recupera vida. */
.eprow--watched .eprow__img { filter: brightness(.45) saturate(.55); transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk), filter var(--t-base) var(--ease-silk); }
.eprow--watched .eprow__thumb:hover .eprow__img { filter: brightness(.85) saturate(.9); }
.eprow--watched .eprow__num-v { opacity: .75; }
.eprow__num { position: absolute; bottom: 3px; left: var(--s-2); display: flex; flex-direction: column; align-items: flex-start; color: #fff; z-index: 3; pointer-events: none; }
.eprow__num-k { font-family: var(--font-mono); font-size: 0.5rem; font-weight: 600; color: var(--ice); letter-spacing: .14em; line-height: 1; }
.eprow__num-v { font-family: var(--font-display); font-weight: 700; font-size: 1.5rem; line-height: 1; text-shadow: 0 2px 12px rgba(0,0,0,.95); }
.eprow__bar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; height: 3px; background: rgba(0,0,0,.4); }
.eprow__bar span { position: absolute; left: 0; top: 0; bottom: 0; transition: width .5s var(--ease-silk); }
.eprow__bar--resume span { background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow); }
.eprow__bar--dl span { background: linear-gradient(90deg, var(--cyan), var(--azure)); box-shadow: 0 0 8px var(--azure-glow); }
.eprow__play { position: absolute; inset: 0; z-index: 4; display: grid; place-items: center; color: #fff; opacity: 0; transition: opacity var(--t-base); }
.eprow__play :deep(svg) { filter: drop-shadow(0 2px 8px rgba(0,0,0,.6)); }
.eprow__thumb:hover .eprow__play { opacity: 1; }

.eprow__body { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 0.3125rem; }
.eprow__titrow { display: flex; align-items: center; gap: var(--s-2); min-width: 0; }
.eprow__badge { flex-shrink: 0; font-family: var(--font-mono); font-size: 0.5625rem; font-weight: 700; letter-spacing: .06em;
  color: var(--azure-bright); padding: 2px 0.4375rem; border-radius: var(--r-pill); background: var(--azure-haze); border: 1px solid var(--azure-glow); }
.eprow__title { font-size: var(--fs-sm); font-weight: 600; color: var(--ink); line-height: var(--lh-snug);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eprow--watched .eprow__title { color: var(--ink-faint); font-weight: 500; }
.eprow__status { font-size: var(--fs-xs); font-family: var(--font-mono); color: var(--cyan); }
.eprow__status--muted { color: var(--ink-faint); }
.eprow__status--resume { color: var(--azure-bright); }

.eprow__acts { display: flex; align-items: center; gap: var(--s-1); flex-shrink: 0; }
.eprow__icon { width: 1.875rem; height: 1.75rem; display: inline-flex; align-items: center; justify-content: center; gap: 3px;
  border-radius: var(--r-xs); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast) var(--ease-silk); }
.eprow__icon:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.eprow__icon.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.eprow__icon--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.eprow__icon--sub { width: auto; padding: 0 0.5rem; color: var(--jade); border-color: color-mix(in srgb, var(--jade) 25%, transparent); }
.eprow__icon--sub:hover { background: color-mix(in srgb, var(--jade) 12%, transparent); border-color: color-mix(in srgb, var(--jade) 50%, transparent); }
.eprow__icon--retry { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 25%, transparent); }
.eprow__icon--retry:hover { background: color-mix(in srgb, var(--amber) 12%, transparent); border-color: color-mix(in srgb, var(--amber) 50%, transparent); }
.eprow__sub-lbl { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; }
.eprow__i { font-family: var(--font-display); font-style: italic; font-weight: 600; font-size: .95rem; }
.eprow__dots3 { font-size: 1rem; line-height: 1; }
.eprow__searchbtn { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 0.3125rem 0.875rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); background: transparent; transition: all var(--t-fast); }
.eprow__searchbtn:hover { background: var(--azure-haze); color: #fff; border-color: var(--azure-bright); }

.eprow__sub { display: flex; align-items: center; gap: 0.375rem; font-size: var(--fs-2xs); color: var(--jade); }
.eprow__sub.is-done { font-weight: 700; }
.eprow__sub-bar { width: 7.5rem; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.eprow__sub-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--jade), var(--cyan)); transition: width var(--t-base); }
.eprow__sub-msg { font-family: var(--font-mono); color: var(--ink-soft); flex-shrink: 0; }
.eprow__sub-x { width: 1.125rem; height: 1.125rem; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); flex-shrink: 0; }
.eprow__sub-x:hover { color: var(--coral); }

.eprow__info { padding: var(--s-3); border-top: 1px solid var(--line); background: var(--base); font-size: var(--fs-xs); line-height: 1.55; }
.eprow__info-title { font-weight: 600; color: var(--ink); margin-bottom: 4px; font-size: var(--fs-sm); }
.eprow__info-syn { color: var(--ink-soft); }
.eprow__info-meta { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-2); color: var(--ink-faint); }
.eprow__ibadge { padding: 1px 0.375rem; border-radius: var(--r-xs); font-weight: 600; font-size: var(--fs-2xs); }
.eprow__ibadge--filler { background: color-mix(in srgb, var(--gold) 18%, transparent); color: var(--gold); }
.eprow__ibadge--recap { background: var(--azure-haze); color: var(--azure-bright); }
.eprow__info-empty { color: var(--ink-faint); font-style: italic; }
.eprow__info-load { display: flex; gap: 0.3125rem; justify-content: center; padding: var(--s-2); }
.eprow__info-load .dot { width: 0.3125rem; height: 0.3125rem; border-radius: 50%; background: var(--azure); animation: pulse-live 1s var(--ease-drift) infinite; }
.eprow__info-load .dot:nth-child(2) { animation-delay: .15s; }
.eprow__info-load .dot:nth-child(3) { animation-delay: .3s; }

.info-enter-active, .info-leave-active { transition: all var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { opacity: 0; max-height: 0; }
.info-enter-to, .info-leave-from { max-height: 18.75rem; }

@media (max-width: 640px) {
  .eprow__thumb { width: 7rem; }
  .eprow__acts { flex-wrap: wrap; justify-content: flex-end; max-width: 40%; }
}
</style>
