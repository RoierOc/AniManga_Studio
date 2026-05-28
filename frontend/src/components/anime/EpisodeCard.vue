<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeEpLabel, isEpisodePlayable } from '@/lib/anime'
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

function onPlay() { if (playable.value) store.play(props.anime, props.ep) }
</script>

<template>
  <div class="ep" :class="{ 'ep--watched': ep.watched, 'ep--dl': downloading, 'ep--missing': !playable && !downloading }"
       @mouseenter.once="store.loadSkip(anime, ep)">

    <div class="ep__thumb" @click="onPlay">
      <div class="ep__bg" :style="anime.cover ? `background-image:url('${anime.cover}')` : ''" />
      <img v-if="playable" class="ep__img" :src="`/api/anime/thumb/${anime.id}/${ep.num}`"
           loading="lazy" @load="$event.target.classList.add('is-loaded')" @error="$event.target.style.display='none'" alt="" />

      <div class="ep__num"><span class="ep__num-k">EP</span><span class="ep__num-v">{{ String(ep.num).padStart(2, '0') }}</span></div>

      <div v-if="ep.watched" class="ep__chip ep__chip--seen"><Icon name="check" :size="11" /> Visto</div>

      <!-- downloading overlay -->
      <div v-if="downloading" class="ep__dlbar"><span :style="{ width: dlPct + '%' }" /><em>{{ Math.round(dlPct) }}%</em></div>

      <!-- watched scrim -->
      <div v-if="ep.watched" class="ep__seen-veil" />

      <div v-if="playable" class="ep__play"><Icon name="play" :size="30" /></div>
    </div>

    <div class="ep__foot">
      <div class="ep__title">{{ animeEpLabel(anime, ep) }}</div>

      <div class="ep__actions" v-if="playable">
        <button v-if="opEnd" class="ep__icon ep__icon--skip" :title="`Saltar OP → ${Math.floor(opEnd)}s`"
                @click.stop="store.play(anime, ep, '', opEnd)">
          <Icon name="play" :size="13" /><span class="ep__icon-lbl">OP</span>
        </button>
        <button v-if="anime.mal_id" class="ep__icon" :class="{ 'is-on': infoOpen }" title="Descripción"
                @click.stop="store.loadEpInfo(anime, ep)">
          <span class="ep__i">i</span>
        </button>
        <button class="ep__icon" :class="{ 'is-on': ep.watched }" :title="ep.watched ? 'No visto' : 'Visto'"
                @click.stop="store.toggleWatched(anime, ep)">
          <Icon name="check" :size="14" />
        </button>
        <button v-if="!ep.in_local" class="ep__icon ep__icon--danger" title="Borrar"
                @click.stop="store.deleteEpisode(anime, ep)">
          <Icon name="close" :size="13" />
        </button>
      </div>
      <div v-else-if="downloading" class="ep__status">↓ Descargando</div>
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
.ep--missing .ep__thumb, .ep--dl .ep__thumb { cursor: default; }
.ep__bg { position: absolute; inset: 0; background-size: cover; background-position: center; filter: blur(18px) brightness(.4); transform: scale(1.2); }
.ep__img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk); }
.ep__img.is-loaded { opacity: 1; }
.ep:hover .ep__img { transform: scale(1.06); }

.ep__num { position: absolute; top: var(--s-2); left: var(--s-2); display: flex; align-items: baseline; gap: 3px; text-shadow: 0 1px 4px rgba(0,0,0,.7); }
.ep__num-k { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ice); letter-spacing: .08em; }
.ep__num-v { font-family: var(--font-display); font-weight: 600; font-size: 1.1rem; }

.ep__chip { position: absolute; top: var(--s-2); right: var(--s-2); display: inline-flex; align-items: center; gap: 4px;
  font-size: var(--fs-2xs); font-weight: 600; padding: 2px 7px; border-radius: var(--r-pill); backdrop-filter: blur(6px); }
.ep__chip--seen { color: var(--ice); background: rgba(7,10,18,.6); }
.ep__seen-veil { position: absolute; inset: 0; background: rgba(7,10,18,.5); backdrop-filter: saturate(.6) brightness(.8); }

.ep__dlbar { position: absolute; left: 0; right: 0; bottom: 0; height: 22px; background: rgba(7,10,18,.7); display: flex; align-items: center; }
.ep__dlbar span { position: absolute; left: 0; bottom: 0; top: 0; background: linear-gradient(90deg, var(--cyan), var(--azure)); opacity: .35; }
.ep__dlbar em { position: relative; margin-left: auto; margin-right: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-style: normal; color: var(--cyan); }

.ep__play { position: absolute; inset: 0; display: grid; place-items: center; color: #fff; opacity: 0; transition: opacity var(--t-base); }
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
