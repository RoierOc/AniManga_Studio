<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, animeFormatLabel, batchInfo, fmtCountdown } from '@/lib/anime'
import EpisodeCard from '@/components/anime/EpisodeCard.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const anime = computed(() => store.detail)

const batch = computed(() => batchInfo(anime.value?.episodes || []))
const mainEps = computed(() =>
  (anime.value?.episodes || []).filter(e => e.num !== 0 && e.ep_type !== 'special').sort((a, b) => a.num - b.num)
)
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
</script>

<template>
  <div v-if="anime" class="detail">
    <!-- aura backdrop from cover -->
    <div class="detail__aura" :style="anime.cover ? `background-image:url('${anime.cover}')` : ''" />

    <button class="detail__back" @click="store.closeDetail()">
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
        </div>
      </div>
    </header>

    <div class="epgrid">
      <EpisodeCard v-for="ep in mainEps" :key="ep.num" :anime="anime" :ep="ep" :batch="batch" />
    </div>

    <template v-if="specials.length">
      <div class="epgrid__sep"><Icon name="spark" :size="14" /> Especiales / Extras</div>
      <div class="epgrid">
        <EpisodeCard v-for="ep in specials" :key="'sp-' + ep.num" :anime="anime" :ep="ep" :batch="batch" />
      </div>
    </template>
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
.detail__cover { width: 180px; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-lg); border: 1px solid var(--line-2); flex-shrink: 0; }
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

.epgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--s-4); }
.epgrid__sep { display: flex; align-items: center; gap: var(--s-2); margin: var(--s-7) 0 var(--s-4); color: var(--ink-soft); font-family: var(--font-display); font-weight: 600; }
.epgrid__sep :deep(svg) { color: var(--gold); }

@media (max-width: 640px) {
  .detail { padding: var(--s-3) var(--s-4) var(--s-8); }
  .detail__head { flex-direction: column; }
  .detail__cover { width: 130px; }
  .epgrid { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: var(--s-3); }
}
</style>
