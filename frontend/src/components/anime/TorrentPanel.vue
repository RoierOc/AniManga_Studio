<script setup>
import { ref, computed, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/lib/format'
import { animeFormatLabel } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
const a = computed(() => store.torrentAnime)
const inLibrary = computed(() => store.isInLibrary(a.value))

// Stepper +/−: navigating to an episode deep-fetches its torrents (debounced so rapid clicks
// only fire once). Nyaa's RSS window starves early episodes, so each episode needs its own
// targeted "<title> - 0N" fetch — without it ep2 would show the 1-2 torrents that happened to
// fall inside the newest-75 window instead of its true count (e.g. 36 for Honzuki ep2).
let _stepTimer = null
function stepEp(delta) {
  const next = Math.max(1, (store.targetEp ?? 1) + delta)
  store.targetEp = next
  clearTimeout(_stepTimer)
  _stepTimer = setTimeout(() => store.fetchEpisodeTorrents(next), 350)
}
const expanded = ref(new Set())

// openTorrents() always pushes one history entry, so back consumes it and runs
// the guarded restore (closes the panel + restores the previous sub-tab).
// Close the torrent panel and stay on the search sub-view (no history jump).
function goBack() { store.closeTorrents(); useUiStore().replaceNav() }

const LANGS = computed(() => [
  { id: 'all', label: 'Todos', n: store.langCounts.all },
  { id: 'esp', label: 'Español', n: store.langCounts.esp },
  { id: 'eng', label: 'Inglés', n: store.langCounts.eng },
  { id: 'other', label: 'Otros', n: store.langCounts.other },
])

function toggle(ep) {
  const s = new Set(expanded.value)
  if (s.has(ep)) s.delete(ep)
  else { s.add(ep); if (ep > 0) store.fetchEpisodeTorrents(ep) }  // deep-fetch this episode on expand
  expanded.value = s
}
const isOpen = (ep) => expanded.value.has(ep)
const epLoading = (ep) => store.epFetch[ep] === 'loading'

// Al entrar en cualquier vista de torrents (episodio único o todos) los grupos arrancan
// COLAPSADOS — el usuario despliega el que quiera. El deep-fetch por episodio ocurre en toggle()
// al expandir, así que colapsar además evita peticiones a Nyaa que no se van a mirar.
watch(() => store.groupedEpisodes, () => {
  expanded.value = new Set()
}, { immediate: true })
const epLabel = (n) => n === 0 ? 'Batch / Completo' : n === -1 ? 'Sin clasificar' : `Episodio ${n}`
const keyOf = (t) => t.info_hash || t.torrent_url
</script>

<template>
  <div v-if="a" class="tp">
    <button class="tp__back" @click="goBack">
      <Icon name="chevron" :size="16" :style="{ transform: 'rotate(180deg)' }" /> Resultados
    </button>

    <header class="tp__head">
      <img v-if="a.cover" :src="imgProxy(a.cover)" class="tp__cover" :alt="a.title" />
      <div class="tp__meta">
        <div class="tp__title-row">
          <span class="tp__fmt">{{ animeFormatLabel(a.format) }}</span>
          <button v-if="!inLibrary" class="tp__add-lib" @click="store.addToLibrary(a)">
            <Icon name="plus" :size="13" /> Mi Anime
          </button>
          <span v-else class="tp__in-lib"><Icon name="check" :size="13" /> En biblioteca</span>
        </div>
        <h1 class="tp__title">{{ a.title }}</h1>
        <p v-if="a.title_romaji && a.title_romaji !== a.title" class="tp__romaji">{{ a.title_romaji }}</p>
        <div class="tp__searchbar">
          <input v-model="store.torrentQuery" @keyup.enter="store.searchTorrents()" placeholder="Refinar búsqueda en Nyaa…" />
          <button class="tp__sbtn" @click="store.searchTorrents()"><Icon name="search" :size="14" /> Nyaa</button>
        </div>
        <div v-if="store.torrentVariants.length > 1" class="tp__alias" title="Alias de AniList que se buscan en Nyaa">
          <Icon name="search" :size="11" />
          <span class="tp__alias-lbl">Alias:</span>
          <button v-for="al in store.torrentVariants" :key="al" class="tp__chip"
                  :class="{ 'is-on': store.torrentQuery.trim() === al.trim() }"
                  @click="store.torrentQuery = al; store.searchTorrents()">{{ al }}</button>
        </div>
      </div>
    </header>

    <!-- Target episode stepper (from anime detail) -->
    <div v-if="store.targetEp !== null" class="tep">
      <span class="tep__label">Registrar como episodio:</span>
      <div class="tep__stepper">
        <button class="tep__btn" @click="stepEp(-1)">−</button>
        <span class="tep__num">{{ store.targetEp }}</span>
        <button class="tep__btn" @click="stepEp(+1)">+</button>
      </div>
      <button class="tep__clear" @click="store.targetEp = null">Ver todos</button>
    </div>

    <!-- Filters -->
    <div class="tp__filters">
      <div class="seg">
        <button v-for="l in LANGS" :key="l.id" :class="{ 'is-active': store.flt.lang === l.id }" @click="store.flt.lang = l.id">
          {{ l.label }} <span class="seg__n">{{ l.n }}</span>
        </button>
      </div>
      <select v-if="store.torrentQualities.length" class="sel" v-model="store.flt.quality">
        <option value="">Calidad</option>
        <option v-for="q in store.torrentQualities" :key="q" :value="q">{{ q }}</option>
      </select>
      <select v-if="store.torrentGroups.length" class="sel" v-model="store.flt.group">
        <option value="">Grupo</option>
        <option v-for="g in store.torrentGroups" :key="g" :value="g">{{ g }}</option>
      </select>
      <div class="seg">
        <button :class="{ 'is-active': store.flt.ep === 'all' }" @click="store.flt.ep = 'all'">Todo</button>
        <button :class="{ 'is-active': store.flt.ep === 'batch' }" @click="store.flt.ep = 'batch'">Batch</button>
        <button :class="{ 'is-active': store.flt.ep === 'episodes' }" @click="store.flt.ep = 'episodes'">Eps</button>
      </div>
      <label class="chk"><input type="checkbox" v-model="store.flt.hideDead" /> Con seeds</label>
    </div>

    <div v-if="store.torrentsLoading" class="tp__groups">
      <div v-for="n in 6" :key="n" class="tp__skel" />
    </div>
    <div v-else-if="!store.groupedEpisodes.length" class="empty"><Icon name="search" :size="34" /><p>Sin torrents para estos filtros.</p></div>

    <div v-else class="tp__groups">
      <div v-for="g in store.groupedEpisodes" :key="g.episode" class="egrp">
        <button class="egrp__head" @click="toggle(g.episode)">
          <Icon name="chevron" :size="14" :style="{ transform: isOpen(g.episode) ? 'rotate(90deg)' : 'none' }" />
          <span class="egrp__label">{{ epLabel(g.episode) }}</span>
          <Spinner v-if="epLoading(g.episode)" :size="13" />
          <span class="egrp__count">{{ g.torrents.length }}</span>
        </button>
        <div v-show="isOpen(g.episode)" class="egrp__list">
          <div v-if="!g.torrents.length" class="egrp__empty">
            <template v-if="epLoading(g.episode)"><Spinner :size="14" /> Buscando torrents…</template>
            <template v-else>Sin torrents para este episodio.</template>
          </div>
          <div v-for="t in g.torrents" :key="keyOf(t)" class="tr">
            <div class="tr__info">
              <div class="tr__name">
                <span v-if="t.isSpanish" class="lang lang--es">ES</span>
                <span v-else-if="t.isEnglish" class="lang lang--en">EN</span>
                {{ t.title }}
              </div>
              <div class="tr__stats">
                <span v-if="t.quality" class="tg">{{ t.quality }}</span>
                <span v-if="t.group" class="tg tg--group">{{ t.group }}</span>
                <span class="muted">{{ formatBytes(t.size) }}</span>
                <span class="tr__seed">▲ {{ t.seeders }}</span>
                <span class="muted">▼ {{ t.leechers }}</span>
              </div>
            </div>
            <a v-if="t.view_url" :href="t.view_url" target="_blank" rel="noopener" class="tr__nyaa" title="Ver en Nyaa" @click.stop>
              <Icon name="external" :size="14" /> Nyaa
            </a>
            <button class="tr__add" :class="{ 'is-added': store.isAdded(keyOf(t)) }"
                    :disabled="store.isAdding(keyOf(t)) || store.isAdded(keyOf(t))" @click="store.addToQbt(t)">
              <Spinner v-if="store.isAdding(keyOf(t))" :size="13" />
              <Icon v-else :name="store.isAdded(keyOf(t)) ? 'check' : 'download'" :size="14" />
              {{ store.isAdded(keyOf(t)) ? 'Añadido' : 'Descargar' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tp { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.tp__back { display: inline-flex; align-items: center; gap: var(--s-1); margin: var(--s-4) 0 var(--s-5); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft); border: 1px solid var(--line); background: var(--surface); font-size: var(--fs-sm); transition: all var(--t-fast); }
.tp__back:hover { color: var(--ink); border-color: var(--line-strong); }

.tp__head { display: flex; gap: var(--s-5); margin-bottom: var(--s-5); }
.tp__cover { width: 120px; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.tp__meta { flex: 1; min-width: 0; }
.tp__title-row { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-1); }
.tp__fmt { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); }
.tp__add-lib { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: var(--r-pill); background: transparent; border: 1px solid var(--line-2); color: var(--ink-soft); font-size: var(--fs-2xs); font-weight: 500; transition: all var(--t-fast); }
.tp__add-lib:hover { border-color: var(--azure); color: var(--azure-bright); background: var(--azure-haze); }
.tp__in-lib { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-2xs); color: var(--jade); }
.tp__title { font-size: var(--fs-2xl); }
.tp__romaji { color: var(--ink-faint); font-size: var(--fs-sm); margin-bottom: var(--s-3); }
.tp__searchbar { display: flex; gap: var(--s-2); margin-top: var(--s-3); flex-wrap: wrap; }
.tp__searchbar input { flex: 1; min-width: 180px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.tp__searchbar input:focus { outline: none; border-color: var(--azure); }
.tp__sbtn { display: inline-flex; align-items: center; gap: 5px; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-size: var(--fs-sm); font-weight: 600; }
.tp__alias { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: var(--s-2); color: var(--ink-faint); }
.tp__alias-lbl { font-size: var(--fs-2xs); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.tp__chip { padding: 2px 8px; border-radius: var(--r-pill); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink-soft); font-size: var(--fs-2xs); transition: all var(--t-fast); }
.tp__chip:hover { border-color: var(--azure); color: var(--ink); }
.tp__chip.is-on { background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright); font-weight: 600; }

.tp__filters { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; margin-bottom: var(--s-5); padding-bottom: var(--s-4); border-bottom: 1px solid var(--line); }
.seg { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); }
.seg button { display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--ink-faint); transition: all var(--t-fast); }
.seg button:hover { color: var(--ink); }
.seg button.is-active { background: var(--azure-haze); color: var(--azure-bright); }
.seg__n { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .7; }
.sel { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
/* Target episode stepper */
.tep { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); margin-bottom: var(--s-4); background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 25%, transparent); border-radius: var(--r-md); font-size: var(--fs-sm); }
.tep__label { color: var(--azure-bright); font-weight: 500; }
.tep__stepper { display: inline-flex; align-items: center; gap: 2px; background: var(--surface); border: 1px solid var(--line-2); border-radius: var(--r-sm); }
.tep__btn { width: 30px; height: 28px; display: grid; place-items: center; font-size: var(--fs-lg); font-weight: 600; color: var(--ink-soft); transition: all var(--t-fast); }
.tep__btn:hover { color: var(--azure-bright); background: var(--azure-haze); }
.tep__num { font-family: var(--font-mono); font-size: var(--fs-md); font-weight: 700; color: var(--azure-bright); min-width: 44px; text-align: center; }
.tep__clear { margin-left: auto; font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line); border-radius: var(--r-sm); padding: var(--s-1) var(--s-3); }
.tep__clear:hover { color: var(--ink); border-color: var(--line-strong); }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; }

.center { display: grid; place-items: center; padding: var(--s-8); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

.tp__groups { display: flex; flex-direction: column; gap: var(--s-2); }
.tp__skel { height: 2.6rem; border-radius: var(--r-md);
  background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%);
  background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.egrp { border: 1px solid var(--line); border-radius: var(--r-md); overflow: hidden; background: var(--surface); }
.egrp__head { display: flex; align-items: center; gap: var(--s-2); width: 100%; padding: var(--s-3) var(--s-4); color: var(--ink); font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.egrp__head:hover { background: var(--surface-2); }
.egrp__label { flex: 1; text-align: left; }
.egrp__count { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); padding: 2px 8px; border-radius: var(--r-pill); background: var(--surface-3); }
.egrp__list { border-top: 1px solid var(--line); }
.egrp__empty { display: flex; align-items: center; justify-content: center; gap: var(--s-2); padding: var(--s-4); font-size: var(--fs-xs); color: var(--ink-faint); }

.tr { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-bottom: 1px solid var(--line); }
.tr:last-child { border-bottom: none; }
.tr__info { flex: 1; min-width: 0; }
.tr__name { font-size: var(--fs-sm); display: flex; align-items: center; gap: var(--s-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lang { font-family: var(--font-mono); font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: var(--r-xs); flex-shrink: 0; }
.lang--es { background: color-mix(in srgb, var(--jade) 20%, transparent); color: var(--jade); }
.lang--en { background: var(--azure-haze); color: var(--azure-bright); }
.tr__stats { display: flex; align-items: center; gap: var(--s-3); margin-top: 4px; font-size: var(--fs-xs); color: var(--ink-soft); font-family: var(--font-mono); }
.tr__stats .muted { color: var(--ink-faint); }
.tg { padding: 1px 6px; border-radius: var(--r-xs); background: var(--surface-3); color: var(--ink-soft); }
.tg--group { color: var(--ice); }
.tr__seed { color: var(--jade); }
.tr__add { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-size: var(--fs-xs); font-weight: 600; transition: background var(--t-fast); }
.tr__add:hover { background: var(--azure-bright); }
.tr__add.is-added { background: transparent; color: var(--jade); border: 1px solid color-mix(in srgb, var(--jade) 35%, transparent); }
.tr__nyaa { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.tr__nyaa:hover { color: var(--azure-bright); border-color: var(--azure); }

@media (max-width: 640px) { .tp { padding: 0 var(--s-4) var(--s-8); } .tp__head { flex-direction: column; } }
</style>
