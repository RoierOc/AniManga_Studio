<script setup>
import { onMounted } from 'vue'
import { useSourcesStore } from '@/stores/sources'
import SourceDetailModal from '@/components/manga/SourceDetailModal.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useSourcesStore()
// Siempre re-chequear al entrar: con Suwayomi on-demand la JVM puede haberse
// apagado por inactividad desde la última visita — el wake=1 la despierta.
onMounted(() => { store.checkHealth() })

function openManga(m) {
  store.openDetail(m)
}

function openMangaFromGroup(m, groupSource) {
  store.openDetail({
    ...m,
    sourceId: groupSource.id,
    sourceName: groupSource.name,
    sourceLang: groupSource.lang,
  })
}
</script>

<template>
  <div class="src">
    <header class="src__head">
      <div>
        <p class="eyebrow"><span class="tick" /> EXTENSIONES MIHON / SUWAYOMI</p>
        <h1>Fuentes</h1>
      </div>
      <div class="src__head-r">
        <button class="src__webui" :disabled="store.openingWebUI" @click="store.openWebUI()"
          title="Abrir Suwayomi (:4567) para instalar extensiones y elegir fuentes">
          <Icon name="external" :size="15" />
          {{ store.openingWebUI ? 'Abriendo…' : 'Gestionar fuentes' }}
        </button>
        <span class="src__status" :class="{ 'is-on': store.online, 'is-starting': store.starting }">
          <span class="dot" /> {{ store.online ? `${store.sources.length} fuentes` : (store.starting ? 'Arrancando fuentes…' : 'Suwayomi offline') }}
        </span>
      </div>
    </header>

    <div v-if="store.checked && !store.online && store.starting" class="offline">
      <Spinner :size="28" />
      <p>Arrancando el servidor de fuentes… (~15 s la primera vez)</p>
    </div>
    <div v-else-if="store.checked && !store.online" class="offline">
      <Icon name="globe" :size="36" />
      <p>Suwayomi no está corriendo (puerto 4567).</p>
      <div class="offline__btns">
        <button class="btn btn--ghost" @click="store.checkHealth()">Reintentar</button>
        <button class="btn" :disabled="store.openingWebUI" @click="store.openWebUI()">
          <Icon name="external" :size="14" /> {{ store.openingWebUI ? 'Abriendo…' : 'Arrancar y gestionar fuentes' }}
        </button>
      </div>
    </div>

    <template v-else>
      <label class="bigbox">
        <Icon name="search" :size="18" />
        <input v-model="store.query" @keyup.enter="store.search()" type="search"
          :placeholder="store.hasSelection ? 'Buscar en ' + store.selectedCount + ' fuente' + (store.selectedCount > 1 ? 's' : '') + '…' : 'Selecciona fuentes o Todas para buscar…'" />
        <button class="bigbox__go" :disabled="!store.hasSelection" @click="store.search()">Buscar</button>
        <span v-if="store.hasSelection" class="bigbox__sep" />
        <button v-if="store.hasSelection" class="bigbox__pop" :disabled="store.popularLoading" @click="store.loadPopular()">
          <Icon name="library" :size="14" /> Populares
        </button>
      </label>

      <!-- Source filter chips -->
      <div v-if="store.sources.length" class="srcsel">
        <button class="schip" :class="{ 'is-active': store.allSourcesSelected }"
          @click="store.allSourcesSelected ? store.clearAll() : store.selectAll()">
          Todas
          <span v-if="store.allSourcesSelected" class="schip__cnt">{{ store.selectedCount }}</span>
        </button>
        <button v-for="s in store.sources" :key="s.id" class="schip"
          :class="{ 'is-active': store.activeSources.includes(s.id) }"
          @click="store.toggleSource(s.id)">
          <img v-if="s.iconUrl" :src="s.iconUrl" class="schip__ic" referrerpolicy="no-referrer" /> {{ s.name }}
          <span class="schip__lang">{{ s.lang }}</span>
        </button>
      </div>

      <!-- Progress bar (search or popular) -->
      <div v-if="(store.searching || store.popularLoading) && store.progress.total" class="prog">
        <div class="prog__bar"><span :style="{ width: (store.progress.done / store.progress.total * 100) + '%' }" /></div>
        <span class="prog__n">{{ store.progress.done }}/{{ store.progress.total }} fuentes</span>
      </div>

      <!-- Popular loading (no progress bar yet, showing spinner) -->
      <div v-if="store.popularLoading && !store.progress.total" class="center"><Spinner :size="28" /><p class="cload">Cargando populares…</p></div>

      <!-- Popular results (only when not searching and no search results) -->
      <template v-else-if="store.popular.length && !store.searching && !store.results.length">
        <div class="pophead">
          <span class="pophead__title">Populares</span>
          <span class="pophead__count">{{ store.popular.length }} manga</span>
        </div>
        <div class="grid">
          <article v-for="(m, i) in store.popular" :key="m.sourceId + '_' + m.id + '_' + i" class="sc" tabindex="0"
            @click="openManga(m)" @keydown.enter="openManga(m)">
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

      <!-- Search results grouped by source -->
      <template v-else-if="store.resultGroups.length">
        <div v-if="store.searching && !store.resultGroups.length" class="center"><Spinner :size="28" /></div>
        <template v-else>
          <div v-for="group in store.resultGroups" :key="group.source.id" class="src-group">
            <div class="src-group__head">
              <img v-if="group.source.iconUrl" :src="group.source.iconUrl" class="src-group__ic" referrerpolicy="no-referrer" />
              <span class="src-group__name">{{ group.source.name }}</span>
              <span class="src-group__lang">{{ group.source.lang?.toUpperCase() }}</span>
              <span class="src-group__count">{{ group.results.length }}</span>
            </div>
            <div class="grid">
              <article v-for="(m, i) in group.results" :key="m.sourceId + '_' + m.id + '_' + i" class="sc" tabindex="0"
                @click="openMangaFromGroup(m, group.source)" @keydown.enter="openMangaFromGroup(m, group.source)">
                <div class="sc__poster">
                  <img v-if="m.thumbnailUrl" :src="m.thumbnailUrl" :alt="m.title" loading="lazy" referrerpolicy="no-referrer" @load="$event.target.classList.add('is-loaded')" class="sc__img" />
                  <div v-else class="sc__ph"><Icon name="globe" :size="24" /></div>
                  <div class="sc__scrim" />
                  <span v-if="m.inLibrary" class="sc__lib"><Icon name="check" :size="11" /></span>
                  <div class="sc__overlay">
                    <h3 class="sc__title">{{ m.title }}</h3>
                    <span class="sc__src">{{ group.source.name }}</span>
                  </div>
                </div>
              </article>
            </div>
          </div>
        </template>
      </template>

      <!-- Empty -->
      <div v-else-if="!store.searching && !store.popularLoading" class="hint">
        <Icon name="globe" :size="32" />
        <p v-if="store.hasSelection">Selecciona fuentes y pulsa Buscar o Populares.</p>
        <p v-else>Selecciona una o más fuentes para buscar y ver sus populares.</p>
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

.src__head-r { display: flex; align-items: center; gap: var(--s-3); }
.src__webui { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); font-weight: 500; color: var(--ink-soft); padding: 6px 12px; border-radius: var(--r-pill); border: 1px solid var(--line); background: var(--surface); transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast); }
.src__webui:hover:not(:disabled) { color: var(--ink); border-color: var(--azure); background: var(--azure-haze); }
.src__webui:hover:not(:disabled) :deep(svg) { color: var(--azure-bright); }
.src__webui:disabled { opacity: .6; cursor: default; }

.offline { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); text-align: center; }
.offline__btns { display: flex; gap: var(--s-3); flex-wrap: wrap; justify-content: center; }
.btn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); border: 1px solid transparent; transition: background var(--t-fast); }
.btn:hover:not(:disabled) { background: var(--azure-bright); }
.btn:disabled { opacity: .6; cursor: default; }
.btn--ghost { background: transparent; color: var(--ink-soft); border-color: var(--line-strong); }
.btn--ghost:hover:not(:disabled) { background: var(--surface-2); color: var(--ink); }

.bigbox { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-lg); color: var(--ink-faint); transition: border-color var(--t-fast), box-shadow var(--t-fast); }
.bigbox:focus-within { border-color: var(--azure); box-shadow: 0 0 0 4px var(--azure-haze); }
.bigbox input { flex: 1; border: none; outline: none; background: none; color: var(--ink); font-size: var(--fs-lg); }
.bigbox__go { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }
.bigbox__go:disabled { opacity: .4; cursor: not-allowed; }
.bigbox__sep { width: 1px; height: 24px; background: var(--line); flex-shrink: 0; }
.bigbox__pop { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 500; color: var(--ink-soft); border: 1px solid var(--line); white-space: nowrap; transition: all var(--t-fast); flex-shrink: 0; }
.bigbox__pop:hover { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 40%, transparent); background: color-mix(in srgb, var(--amber) 8%, transparent); }

.srcsel { display: flex; gap: var(--s-2); flex-wrap: wrap; margin: var(--s-4) 0; }
.schip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); transition: all var(--t-fast); }
.schip:hover { color: var(--ink); border-color: var(--line-strong); }
.schip.is-active { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); }
.schip__ic { width: 16px; height: 16px; border-radius: 3px; }
.schip__lang { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; text-transform: uppercase; }
.schip__cnt { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 16px; padding: 0 4px; border-radius: var(--r-pill); font-size: 10px; font-weight: 700; background: var(--azure); color: #fff; }

.prog { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-4); }
.prog__bar { flex: 1; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.prog__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.prog__n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }

.center { display: grid; place-items: center; padding: var(--s-8); }
.hint { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-9) 0; color: var(--ink-faint); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: var(--s-4); }

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

.pophead { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-4); padding-bottom: var(--s-3); border-bottom: 1px solid var(--line); }
.pophead__title { font-weight: 600; font-size: var(--fs-md); }
.pophead__lang { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); padding: 1px 5px; border-radius: var(--r-xs); border: 1px solid var(--line-2); }
.pophead__count { font-size: var(--fs-xs); color: var(--ink-faint); }

.src-group { margin-bottom: var(--s-6); }
.src-group__head { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-3); padding-bottom: var(--s-3); border-bottom: 1px solid var(--line); }
.src-group__ic { width: 20px; height: 20px; border-radius: 4px; object-fit: contain; }
.src-group__name { font-weight: 600; font-size: var(--fs-sm); color: var(--ink); }
.src-group__lang { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); padding: 1px 5px; border-radius: var(--r-xs); border: 1px solid var(--line-2); }
.src-group__count { margin-left: auto; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }

@media (max-width: 540px) { .src { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr)); } }
</style>
