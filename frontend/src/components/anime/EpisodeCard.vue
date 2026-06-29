<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeEpLabel, isEpisodePlayable } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  anime: { type: Object, required: true },
  ep: { type: Object, required: true },
  batch: { type: Object, required: true },
})
const store = useAnimeStore()

const playable = computed(() => isEpisodePlayable(props.ep, props.batch))
const downloading = computed(() =>
  (props.ep.in_qbt && props.ep.progress < 100 && !props.batch.hasBatch) ||
  (props.ep.num > 0 && props.batch.hasBatch && !props.batch.batchDone)
)
const dlPct = computed(() => props.batch.hasBatch ? (props.batch.batchEp?.progress || 0) : props.ep.progress)

const skipKey = computed(() => `${props.anime.id}_${props.ep.num}`)
const opEnd = computed(() => store.skipTimes[skipKey.value]?.op_end)

const infoKey = computed(() => `${props.anime.mal_id}_${props.ep.num}`)
const infoOpen = computed(() => store.epInfoOpen === infoKey.value)
const info = computed(() => store.epInfo[infoKey.value])

const subKey = computed(() => store.subKey(props.anime, props.ep))
const subTask = computed(() => store.subTasks[subKey.value])
const subRunning = computed(() => subTask.value && ['starting', 'injecting', 'translating', 'downloading', 'running'].includes(subTask.value.status))
const subFetching = computed(() => store.subFetching === subKey.value)

function onPlay() { if (playable.value) store.play(props.anime, props.ep) }
</script>

<template>
  <div class="ep" :class="{ 'ep--watched': ep.watched, 'ep--dl': downloading, 'ep--missing': !playable && !downloading }"
       @mouseenter.once="store.loadSkip(anime, ep)">

    <div class="ep__thumb" @click="onPlay">
      <div class="ep__bg" :style="anime.cover ? `background-image:url('${imgProxy(anime.cover)}')` : ''" />
      <img v-if="playable || ep.has_thumb" class="ep__img" :src="`/api/anime/thumb/${anime.id}/${ep.num}${ep.ep_type === 'special' ? '?special=1' : ''}`"
           loading="lazy" @load="$event.target.classList.add('is-loaded')" @error="$event.target.style.display='none'" alt="" />

      <div class="ep__num"><span class="ep__num-k">{{ ep.ep_type === 'special' ? 'SP' : 'EP' }}</span><span class="ep__num-v">{{ String(ep.num).padStart(2, '0') }}</span></div>

      <div v-if="ep.watched" class="ep__chip ep__chip--seen"><Icon name="check" :size="11" /> Visto</div>

      <!-- resume progress bar (partially watched) -->
      <div v-if="!ep.watched && ep.resume_pos > 0 && ep.duration > 0 && !downloading" class="ep__resumebar">
        <span :style="{ width: Math.min(100, (ep.resume_pos / ep.duration) * 100) + '%' }" />
      </div>

      <!-- downloading overlay — barra fina como la de progreso; el % va en el estado -->
      <div v-if="downloading" class="ep__dlbar"><span :style="{ width: dlPct + '%' }" /></div>

      <!-- watched scrim -->
      <div v-if="ep.watched" class="ep__seen-veil" />

      <div v-if="playable" class="ep__play"><Icon name="play" :size="30" /></div>
    </div>

    <div class="ep__foot">
      <div class="ep__title">{{ animeEpLabel(anime, ep) }}</div>

      <!-- subtitle translation progress -->
      <div v-if="playable && (subRunning || subTask?.status === 'done')" class="ep__sub" :class="{ 'is-done': subTask?.status === 'done' }">
        <template v-if="subTask?.status === 'done'"><Icon name="check" :size="12" /> ESP ✓</template>
        <template v-else>
          <span class="ep__sub-bar"><span :style="{ width: (subTask?.progress || 0) + '%' }" /></span>
          <span class="ep__sub-msg">{{ subTask?.engine === 'ollama' ? '🦙' : '✨' }} {{ subTask?.progress || 0 }}%</span>
          <button class="ep__sub-x" @click.stop="store.cancelTranslate(anime, ep)"><Icon name="close" :size="11" /></button>
        </template>
      </div>

      <div class="ep__actions" v-if="playable">
        <button v-if="opEnd" class="ep__icon ep__icon--skip" :title="`Saltar OP → ${Math.floor(opEnd)}s`"
                @click.stop="store.play(anime, ep, '', opEnd)">
          <Icon name="play" :size="13" /><span class="ep__icon-lbl">OP</span>
        </button>
        <button v-if="!subRunning && subTask?.status !== 'done'" class="ep__icon ep__icon--sub" :disabled="subFetching"
                :title="subFetching ? 'Buscando…' : 'Subtítulos en español'" @click.stop="store.translateSubs(anime, ep)">
          <span v-if="subFetching" class="ep__mini-spin" /><span v-else class="ep__sub-lbl">ES</span>
        </button>
        <button v-if="subTask?.status === 'error' || subTask?.status === 'done'" class="ep__icon ep__icon--retry"
                :title="subTask?.status === 'error' ? 'Reintentar' : 'Re-inyectar subtítulo'"
                @click.stop="store.translateSubs(anime, ep)">
          <Icon name="spark" :size="13" />
        </button>
        <button v-if="anime.mal_id" class="ep__icon" :class="{ 'is-on': infoOpen }" title="Descripción"
                @click.stop="store.loadEpInfo(anime, ep)">
          <span class="ep__i">i</span>
        </button>
        <button class="ep__icon" :class="{ 'is-on': ep.watched }" :title="ep.watched ? 'No visto' : 'Visto'"
                @click.stop="store.toggleWatched(anime, ep)">
          <Icon name="check" :size="14" />
        </button>
        <button v-if="ep.in_local" class="ep__icon" title="Cambiar tipo de episodio"
                @click.stop="store.openEpOverrideMenu($event, anime, ep)">
          <span class="ep__dots3">⋮</span>
        </button>
        <button v-if="!ep.in_local" class="ep__icon ep__icon--danger" title="Borrar"
                @click.stop="store.deleteEpisode(anime, ep)">
          <Icon name="close" :size="13" />
        </button>
      </div>
      <div v-else-if="downloading" class="ep__status">↓ Descargando {{ Math.round(dlPct) }}%</div>
      <div v-else class="ep__actions">
        <button class="ep__searchbtn" :title="'Buscar torrent para Ep. ' + ep.num" @click.stop="store.openTorrents(anime, ep.num)">
          <Icon name="search" :size="13" /> Buscar
        </button>
      </div>
    </div>

    <!-- expandable episode info -->
    <Transition name="info">
      <div v-if="anime.mal_id && infoOpen" class="ep__info">
        <div v-if="info === null" class="ep__info-load"><span class="dot" /><span class="dot" /><span class="dot" /></div>
        <template v-else-if="info?.synopsis">
          <div v-if="info.title" class="ep__info-title">{{ info.title }}</div>
          <p class="ep__info-syn">{{ info.synopsis }}</p>
          <div class="ep__info-meta">
            <span>{{ info.aired }}</span>
            <span v-if="info.filler" class="ep__badge ep__badge--filler">Filler</span>
            <span v-if="info.recap" class="ep__badge ep__badge--recap">Recap</span>
          </div>
        </template>
        <div v-else class="ep__info-empty">Sin descripción disponible</div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.ep {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md);
  overflow: hidden; transition: border-color var(--t-base) var(--ease-silk), transform var(--t-base) var(--ease-snap), box-shadow var(--t-base);
}
.ep:hover { transform: translateY(-3px); border-color: var(--line-strong); box-shadow: var(--shadow-md); }

.ep__thumb { position: relative; aspect-ratio: 16 / 9; cursor: pointer; overflow: hidden; }
.ep__thumb::after { content: ''; position: absolute; inset: 0; z-index: 1; pointer-events: none; background: linear-gradient(0deg, rgba(5,7,13,.7) 0%, transparent 38%); }
.ep--missing .ep__thumb, .ep--dl .ep__thumb { cursor: default; }
.ep__bg { position: absolute; inset: 0; background-size: cover; background-position: center; filter: blur(18px) brightness(.4); transform: scale(1.2); }
.ep__img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.ep__img.is-loaded { opacity: 1; }
.ep:hover .ep__img { transform: scale(1.06); }

/* Big episode number, bottom-left — like the original */
.ep__num { position: absolute; bottom: var(--s-2); left: var(--s-3); display: flex; flex-direction: column; align-items: flex-start; color: #fff; z-index: 3; pointer-events: none; }
.ep__num-k { font-family: var(--font-mono); font-size: 9px; font-weight: 600; color: var(--ice); letter-spacing: .14em; line-height: 1; }
.ep__num-v { font-family: var(--font-display); font-weight: 700; font-size: 2.3rem; line-height: 1; text-shadow: 0 2px 16px rgba(0,0,0,.95), 0 0 40px rgba(0,0,0,.6); }

.ep__chip { position: absolute; top: var(--s-2); right: var(--s-2); z-index: 3; display: inline-flex; align-items: center; gap: 4px;
  font-size: var(--fs-2xs); font-weight: 600; padding: 2px 7px; border-radius: var(--r-pill); backdrop-filter: blur(6px); }
.ep__chip--seen { color: var(--ice); background: rgba(7,10,18,.6); }
.ep__seen-veil { position: absolute; inset: 0; z-index: 2; background: rgba(7,10,18,.5); backdrop-filter: saturate(.6) brightness(.8); }

.ep__resumebar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; height: 3px; background: rgba(0,0,0,.4); }
.ep__resumebar span { position: absolute; left: 0; top: 0; bottom: 0; background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow); transition: width .6s var(--ease-silk); }
/* Barra de descarga: fina (3px) e igual a la de progreso/resume; el % se muestra en el estado. */
.ep__dlbar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; height: 3px; background: rgba(7,10,18,.5); }
.ep__dlbar span { position: absolute; left: 0; top: 0; bottom: 0; background: linear-gradient(90deg, var(--cyan), var(--azure)); box-shadow: 0 0 8px var(--azure-glow); transition: width .5s var(--ease-silk); }

.ep__play { position: absolute; inset: 0; z-index: 4; display: grid; place-items: center; color: #fff; opacity: 0; transition: opacity var(--t-base); }
.ep__play :deep(svg) { filter: drop-shadow(0 2px 8px rgba(0,0,0,.6)); transform: scale(.85); transition: transform var(--t-base) var(--ease-snap); }
.ep__thumb:hover .ep__play { opacity: 1; }
.ep__thumb:hover .ep__play :deep(svg) { transform: scale(1); }

.ep__foot { padding: var(--s-3); }
.ep__title { font-size: var(--fs-sm); font-weight: 500; line-height: var(--lh-snug); color: var(--ink-soft); margin-bottom: var(--s-2);
  display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
.ep--watched .ep__title { color: var(--ink-faint); }

.ep__actions { display: flex; gap: var(--s-1); }
.ep__icon { width: 30px; height: 28px; display: inline-flex; align-items: center; justify-content: center; gap: 3px;
  border-radius: var(--r-xs); border: 1px solid var(--line); color: var(--ink-faint);
  transition: all var(--t-fast) var(--ease-silk); }
.ep__icon:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ep__icon.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.ep__icon--skip { width: auto; padding: 0 8px; color: var(--gold); border-color: color-mix(in srgb, var(--gold) 25%, transparent); }
.ep__icon--skip:hover { background: color-mix(in srgb, var(--gold) 12%, transparent); border-color: color-mix(in srgb, var(--gold) 50%, transparent); }
.ep__icon-lbl { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; }
.ep__icon--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.ep__i { font-family: var(--font-display); font-style: italic; font-weight: 600; font-size: .95rem; }
.ep__status { font-size: var(--fs-xs); color: var(--cyan); font-family: var(--font-mono); }
.ep__icon--sub { width: auto; padding: 0 8px; color: var(--jade); border-color: color-mix(in srgb, var(--jade) 25%, transparent); }
.ep__icon--sub:hover { background: color-mix(in srgb, var(--jade) 12%, transparent); border-color: color-mix(in srgb, var(--jade) 50%, transparent); }
.ep__sub-lbl { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; }
.ep__mini-spin { width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--jade); animation: spin .7s linear infinite; }
.ep__icon--retry { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 25%, transparent); }
.ep__icon--retry:hover { background: color-mix(in srgb, var(--amber) 12%, transparent); border-color: color-mix(in srgb, var(--amber) 50%, transparent); }
.ep__searchbtn { display: inline-flex; align-items: center; gap: 5px; padding: 5px 14px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); background: transparent; transition: all var(--t-fast); }
.ep__searchbtn:hover { background: var(--azure-haze); color: #fff; border-color: var(--azure-bright); }
.ep__sub { display: flex; align-items: center; gap: 6px; margin-bottom: var(--s-2); font-size: var(--fs-2xs); color: var(--jade); }
.ep__sub.is-done { font-weight: 700; }
.ep__sub-bar { flex: 1; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.ep__sub-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--jade), var(--cyan)); transition: width var(--t-base); }
.ep__sub-msg { font-family: var(--font-mono); color: var(--ink-soft); flex-shrink: 0; }
.ep__sub-x { width: 18px; height: 18px; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); flex-shrink: 0; }
.ep__sub-x:hover { color: var(--coral); }

.ep__info { padding: var(--s-3); border-top: 1px solid var(--line); background: var(--base); font-size: var(--fs-xs); line-height: 1.55; }
.ep__info-title { font-weight: 600; color: var(--ink); margin-bottom: 4px; font-size: var(--fs-sm); }
.ep__info-syn { color: var(--ink-soft); }
.ep__info-meta { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-2); color: var(--ink-faint); }
.ep__badge { padding: 1px 6px; border-radius: var(--r-xs); font-weight: 600; font-size: var(--fs-2xs); }
.ep__badge--filler { background: color-mix(in srgb, var(--gold) 18%, transparent); color: var(--gold); }
.ep__badge--recap { background: var(--azure-haze); color: var(--azure-bright); }
.ep__info-empty { color: var(--ink-faint); font-style: italic; }
.ep__info-load { display: flex; gap: 5px; justify-content: center; padding: var(--s-2); }
.ep__info-load .dot { width: 5px; height: 5px; border-radius: 50%; background: var(--azure); animation: pulse-live 1s var(--ease-drift) infinite; }
.ep__info-load .dot:nth-child(2) { animation-delay: .15s; }
.ep__info-load .dot:nth-child(3) { animation-delay: .3s; }

.info-enter-active, .info-leave-active { transition: all var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { opacity: 0; max-height: 0; }
.info-enter-to, .info-leave-from { max-height: 300px; }
</style>
