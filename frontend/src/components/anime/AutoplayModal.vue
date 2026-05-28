<script setup>
import { computed } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeEpLabel } from '@/lib/anime'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const C = 106.8 // circumference of r=17 ring
const offset = computed(() => C * (1 - store.autoplaySeconds / 10))

function playNow() {
  const a = store.autoplay
  store.dismissAutoplay()
  if (a) store.play(a.anime, a.ep)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="ap">
      <div v-if="store.autoplay" class="ap" @click.self="store.dismissAutoplay()">
        <div class="ap__card">
          <img v-if="store.autoplay.anime.cover" :src="store.autoplay.anime.cover" class="ap__cover" alt="" />
          <div class="ap__body">
            <p class="ap__eyebrow"><span class="ap__tick" /> A CONTINUACIÓN</p>
            <h3 class="ap__title">{{ animeEpLabel(store.autoplay.anime, store.autoplay.ep) }}</h3>
            <p class="ap__series">{{ store.autoplay.anime.title }}</p>

            <div class="ap__row">
              <svg class="ap__ring" viewBox="0 0 40 40" width="52" height="52">
                <circle cx="20" cy="20" r="17" fill="none" stroke="var(--line-2)" stroke-width="2.5" />
                <circle cx="20" cy="20" r="17" fill="none" stroke="var(--azure)" stroke-width="2.5"
                        stroke-linecap="round" :stroke-dasharray="C" :stroke-dashoffset="offset" />
                <text x="20" y="25" text-anchor="middle" fill="var(--ink)" font-size="13" font-weight="600">{{ store.autoplaySeconds }}</text>
              </svg>
              <div class="ap__btns">
                <button class="ap__play" @click="playNow"><Icon name="play" :size="15" /> Reproducir ahora</button>
                <button class="ap__cancel" @click="store.dismissAutoplay()">Cancelar</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ap { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center;
  background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.ap__card { display: flex; width: min(500px, calc(100vw - 40px)); border-radius: var(--r-lg); overflow: hidden;
  background: var(--glass-strong); border: 1px solid var(--line-2); box-shadow: var(--shadow-xl); }
.ap__cover { width: 124px; object-fit: cover; flex-shrink: 0; }
.ap__body { flex: 1; padding: var(--s-5); display: flex; flex-direction: column; gap: var(--s-2); }
.ap__eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); }
.ap__tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.ap__title { font-size: var(--fs-lg); }
.ap__series { font-size: var(--fs-sm); color: var(--ink-faint); }
.ap__row { display: flex; align-items: center; gap: var(--s-4); margin-top: var(--s-3); }
.ap__ring circle:last-of-type { transform: rotate(-90deg); transform-origin: center; transition: stroke-dashoffset .9s linear; }
.ap__btns { display: flex; flex-direction: column; gap: var(--s-2); flex: 1; }
.ap__play { display: inline-flex; align-items: center; justify-content: center; gap: var(--s-2); padding: var(--s-3); border-radius: var(--r-sm);
  background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.ap__play:hover { background: var(--azure-bright); }
.ap__cancel { padding: var(--s-2); border-radius: var(--r-sm); border: 1px solid var(--line-2); color: var(--ink-faint); font-size: var(--fs-sm); transition: all var(--t-fast); }
.ap__cancel:hover { color: var(--ink); border-color: var(--line-strong); }

.ap-enter-active, .ap-leave-active { transition: opacity var(--t-base); }
.ap-enter-active .ap__card { transition: transform var(--t-base) var(--ease-snap); }
.ap-enter-from, .ap-leave-to { opacity: 0; }
.ap-enter-from .ap__card { transform: scale(.92) translateY(10px); }

@media (max-width: 540px) { .ap__cover { display: none; } }
</style>
