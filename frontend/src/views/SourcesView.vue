<script setup>
import { onMounted } from 'vue'
import { useSourcesStore } from '@/stores/sources'
import SourceDetailModal from '@/components/manga/SourceDetailModal.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useSourcesStore()
onMounted(() => { if (!store.checked) store.checkHealth() })
</script>

<template>
  <div class="src">
    <header class="src__head">
      <div>
        <p class="eyebrow"><span class="tick" /> EXTENSIONES MIHON / SUWAYOMI</p>
        <h1>Fuentes</h1>
      </div>
      <span class="src__status" :class="{ 'is-on': store.online }">
        <span class="dot" /> {{ store.online ? `${store.sources.length} fuentes` : 'Suwayomi offline' }}
      </span>
    </header>

    <div v-if="store.checked && !store.online" class="offline">
      <Icon name="globe" :size="36" />
      <p>Suwayomi no está corriendo (puerto 4567).</p>
      <button class="btn" @click="store.checkHealth()">Reintentar</button>
    </div>

    <template v-else>
      <label class="bigbox">
        <Icon name="search" :size="18" />
        <input v-model="store.query" @keyup.enter="store.search()" type="search" placeholder="Buscar en todas las fuentes…" />
        <button class="bigbox__go" @click="store.search()">Buscar</button>
      </label>

      <div v-if="store.sources.length" class="srcsel">
        <button class="schip" :class="{ 'is-active': !store.activeSource }" @click="store.activeSource = ''">Todas</button>
        <button v-for="s in store.sources" :key="s.id" class="schip" :class="{ 'is-active': store.activeSource === s.id }" @click="store.activeSource = s.id">
          <img v-if="s.iconUrl" :src="s.iconUrl" class="schip__ic" referrerpolicy="no-referrer" /> {{ s.name }}
          <span class="schip__lang">{{ s.lang }}</span>
        </button>
      </div>

      <div v-if="store.searching && store.progress.total" class="prog">
        <div class="prog__bar"><span :style="{ width: (store.progress.done / store.progress.total * 100) + '%' }" /></div>
        <span class="prog__n">{{ store.progress.done }}/{{ store.progress.total }} fuentes</span>
      </div>

      <div v-if="store.searching && !store.results.length" class="center"><Spinner :size="28" /></div>
      <div v-else-if="!store.results.length" class="hint"><Icon name="globe" :size="32" /><p>Busca un manga en tus fuentes instaladas.</p></div>
      <div v-else class="grid">
        <article v-for="(m, i) in store.results" :key="m.sourceId + '_' + m.id + '_' + i" class="sc" tabindex="0" @click="store.openDetail(m)" @keydown.enter="store.openDetail(m)">
          <div class="sc__poster">
            <img v-if="m.thumbnailUrl" :src="m.thumbnailUrl" :alt="m.title" loading="lazy" referrerpolicy="no-referrer" @load="$event.target.classList.add('is-loaded')" class="sc__img" />
            <div v-else class="sc__ph"><Icon name="globe" :size="24" /></div>
            <div class="sc__scrim" />
            <span v-if="m.inLibrary" class="sc__lib"><Icon name="check" :size="11" /></span>
            <div class="sc__overlay">
              <h3 class="sc__title">{{ m.title }}</h3>
              <span class="sc__src">{{ m.sourceName }}</span>
            </div>
          </div>
        </article>
      </div>
    </template>

    <SourceDetailModal />
  </div>
</template>

<style scoped>
.src { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.src__head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.src__status { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); padding: 6px 12px; border-radius: var(--r-pill); border: 1px solid var(--line); }
.src__status.is-on { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 30%, transparent); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-ghost); }
.src__status.is-on .dot { background: var(--jade); box-shadow: 0 0 10px color-mix(in srgb, var(--jade) 60%, transparent); }

.offline { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); text-align: center; }
.btn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }

.bigbox { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-lg); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.bigbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 4px var(--azure-haze); }
.bigbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-lg); }
.bigbox__go { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }

.srcsel { display: flex; gap: var(--s-2); flex-wrap: wrap; margin: var(--s-4) 0; }
.schip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.schip:hover { color: var(--ink); border-color: var(--line-strong); }
.schip.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }
.schip__ic { width: 16px; height: 16px; border-radius: 3px; }
.schip__lang { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; text-transform: uppercase; }

.prog { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-4); }
.prog__bar { flex: 1; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.prog__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.prog__n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }

.center { display: grid; place-items: center; padding: var(--s-8); }
.hint { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: var(--s-4); }

.sc { outline: none; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.sc:hover, .sc:focus-visible { transform: translateY(-5px); }
.sc__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); transition: box-shadow var(--t-base), border-color var(--t-base); }
.sc:hover .sc__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.sc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow); }
.sc__img.is-loaded { opacity: 1; }
.sc__ph { position: absolute; inset: 0; display: grid; place-items: center; color: var(--ink-ghost); }
.sc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 45%, rgba(5,7,13,.95) 100%); }
.sc__lib { position: absolute; top: var(--s-2); right: var(--s-2); width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; color: #fff; background: var(--jade); }
.sc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-2) var(--s-3); }
.sc__title { font-size: var(--fs-xs); font-weight: 600; color: #fff; line-height: var(--lh-snug); text-shadow: 0 1px 5px rgba(0,0,0,.7); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.sc__src { font-size: var(--fs-2xs); color: var(--ice); text-shadow: 0 1px 4px rgba(0,0,0,.6); }

@media (max-width: 540px) { .src { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); } }
</style>
