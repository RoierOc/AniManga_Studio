<script setup>
/* Ficha de una NOVELA — hermana del MangaModal: mismo lenguaje visual (overlay glass, ambiente
 * por portada, píldoras de acción `hbtn`, filas de capítulo) pero con lo propio del texto:
 * idioma, fuente, nº de capítulos y progreso por capítulo en vez de 4K/descargas.
 *
 * Antes, pulsar una novela entraba DIRECTO al lector: no se veía la sinopsis, ni cuántos
 * capítulos hay, ni por dónde ibas. */
import { computed, ref, watch } from 'vue'
import { useNovelsStore } from '@/stores/novels'
import { imgProxy } from '@/lib/img'
import { coverRGB, vivid } from '@/lib/coverColor'
import Icon from '@/components/ui/Icon.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useModal } from '@/lib/useModal'

const novels = useNovelsStore()
const d = computed(() => novels.detail)
const novel = computed(() => (novels.novel?.pluginId === d.value?.pluginId &&
                              novels.novel?.path === d.value?.path ? novels.novel : null))
const chapters = computed(() => novel.value?.chapters || [])
const progress = computed(() => (d.value ? novels.progressOf(d.value.novelId) : null))
const currentIndex = computed(() => progress.value?.chapterIndex ?? -1)
const query = ref('')

const cover = computed(() => imgProxy(novel.value?.cover || d.value?.cover || ''))

// Los plugins devuelven el estado en inglés (enum NovelStatus); la UI es en español.
const STATUS = { Ongoing: 'En curso', Completed: 'Completa', 'On Hiatus': 'En pausa',
                 Cancelled: 'Cancelada', 'Publishing Finished': 'Publicación terminada',
                 Licensed: 'Licenciada', Unknown: '' }
const statusLabel = computed(() => STATUS[novel.value?.status] ?? novel.value?.status ?? '')
const LANG = { 'Español': 'ES', English: 'EN', 'Français': 'FR', 'Português': 'PT' }
const langShort = computed(() => {
  const l = d.value?.lang || ''
  return LANG[l] || l.slice(0, 2).toUpperCase()
})

// Ambiente por portada, igual que en MangaModal: la portada esmerilada de fondo + aura del
// color dominante. Sólo decorativo, siempre DETRÁS del texto.
const coverArt = ref(null)
watch(cover, async (url) => {
  coverArt.value = null
  if (!url) return
  const rgb = await coverRGB(url)
  if (!rgb) return
  const v = vivid(rgb)
  coverArt.value = { css: `${v.r}, ${v.g}, ${v.b}`, art: `url("${url}")` }
}, { immediate: true })
const coverStyle = computed(() => (coverArt.value
  ? { '--cv': coverArt.value.css, '--cvart': coverArt.value.art } : {}))

// Progreso de lectura de la serie, para la barra de la cabecera.
const readPct = computed(() => {
  const n = chapters.value.length
  if (!n || currentIndex.value < 0) return 0
  return Math.round(((currentIndex.value + 1) / n) * 100)
})

// `genres` llega como array en unos plugins y como texto separado por comas en otros
// (libread: "Action, Adventure…"). Sin normalizar, el v-for pintaría una letra por chip.
const genres = computed(() => {
  const g = novel.value?.genres
  if (Array.isArray(g)) return g
  return typeof g === 'string' ? g.split(',').map(x => x.trim()).filter(Boolean) : []
})

const list = computed(() => {
  const all = chapters.value.map((c, i) => ({ ...c, _i: i }))
  const q = query.value.trim().toLowerCase()
  if (!q) return all
  const n = parseInt(q, 10)
  return all.filter(c => (c.name || '').toLowerCase().includes(q) ||
                         (!isNaN(n) && String(c._i + 1).startsWith(String(n))))
})

function read(index = null) {
  novels.openReader({ id: d.value.novelId, title: d.value.title, novel: d.value }, index)
}

// Escape cierra, el foco no se escapa por detrás y el fondo no scrollea.
const modalEl = ref(null)
useModal(() => !!d.value, () => novels.closeDetail(), modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="ovf">
      <div v-if="d" class="ov" @click.self="novels.closeDetail()">
        <div class="modal" ref="modalEl" :class="{ 'modal--art': !!coverArt }" :style="coverStyle">
          <button class="modal__x" @click="novels.closeDetail()" aria-label="Cerrar"><Icon name="close" :size="18" /></button>

          <header class="modal__head">
            <div class="modal__ambient" aria-hidden="true" />
            <img v-if="cover" :src="cover" class="modal__cover" :alt="d.title" />
            <div v-else class="modal__cover modal__cover--ph"><Icon name="book" :size="30" /></div>

            <div class="modal__info">
              <div class="modal__badges">
                <span class="tag tag--type">Novela</span>
                <span v-if="langShort" class="tag" :class="{ 'tag--es': langShort === 'ES' }">{{ langShort }}</span>
                <span v-if="statusLabel" class="tag tag--soft">{{ statusLabel }}</span>
              </div>

              <h2 class="modal__title">{{ novel?.name || d.title }}</h2>
              <p class="modal__sub">
                <span v-if="novel?.author">{{ novel.author }}</span>
                <span v-if="novel?.author && chapters.length"> · </span>
                <span v-if="novels.novelLoading">cargando capítulos…</span>
                <span v-else-if="chapters.length">{{ chapters.length }} capítulos</span>
                <span v-if="d.sourceName || d.pluginId"> · {{ d.sourceName || d.pluginId }}</span>
              </p>

              <!-- Progreso de la serie: el equivalente a la barra de cobertura 4K del manga. -->
              <div v-if="readPct" class="modal__cov" :title="`Has leído ${currentIndex + 1} de ${chapters.length} capítulos`">
                <div class="modal__covbar"><span class="modal__covseg" :style="{ width: readPct + '%' }" /></div>
                <span class="modal__covn">{{ readPct }}%</span>
              </div>

              <div class="modal__hacts">
                <button class="hbtn hbtn--accent" :disabled="!chapters.length" @click="read()">
                  <Icon name="book" :size="14" />
                  <template v-if="progress">Continuar · {{ progress.chapterName || `cap. ${currentIndex + 1}` }}</template>
                  <template v-else>Empezar a leer</template>
                </button>
                <button v-if="progress && chapters.length" class="hbtn" @click="read(0)">Desde el principio</button>
              </div>
            </div>
          </header>

          <div class="modal__body">
            <p v-if="novel?.summary" class="synopsis">{{ novel.summary }}</p>

            <div v-if="genres.length" class="chips">
              <span v-for="g in genres.slice(0, 10)" :key="g" class="chip">{{ g }}</span>
            </div>

            <div class="chhead">
              <h3>Capítulos <span v-if="chapters.length" class="chhead__n">{{ chapters.length }}</span></h3>
              <input v-model="query" class="chsearch" type="search" placeholder="Buscar capítulo o nº…" />
            </div>

            <div v-if="novels.novelLoading" class="sk">
              <Skeleton v-for="i in 7" :key="i" height="2.3rem" />
            </div>

            <p v-else-if="!chapters.length" class="empty">
              <Icon name="alert" :size="14" /> No se pudieron leer los capítulos de esta fuente.
            </p>

            <div v-else class="chapters">
              <button v-for="c in list" :key="c.path" class="ch"
                      :class="{ 'is-current': c._i === currentIndex, 'is-read': c._i < currentIndex }"
                      @click="read(c._i)">
                <span class="ch__n">{{ c._i + 1 }}</span>
                <span class="ch__name">{{ c.name || `Capítulo ${c._i + 1}` }}</span>
                <span v-if="c._i === currentIndex" class="ch__tag">vas por aquí</span>
                <Icon v-else-if="c._i < currentIndex" name="check" :size="13" class="ch__done" />
              </button>
              <p v-if="!list.length" class="empty">Ningún capítulo coincide.</p>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5);
  background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(45rem, 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg);
  box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 2.125rem; height: 2.125rem;
  display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft);
  background: var(--surface); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { color: var(--ink); border-color: var(--line-strong); }

.modal__head { position: relative; overflow: hidden; display: flex; gap: var(--s-4); padding: var(--s-5);
  border-bottom: 1px solid var(--line); flex-shrink: 0; }
/* Mismo ambiente que el MangaModal: portada esmerilada + aura del color dominante, detrás de todo. */
.modal__ambient { display: none; }
.modal--art .modal__ambient { display: block; position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.modal--art .modal__ambient::before {
  content: ''; position: absolute; inset: 0;
  background-image: var(--cvart); background-size: cover; background-position: center 22%;
  filter: blur(28px) saturate(1.25); transform: scale(1.25); opacity: .22;
}
.modal--art .modal__ambient::after {
  content: ''; position: absolute; inset: 0;
  background:
    radial-gradient(120% 90% at 18% 0%, rgba(var(--cv), .30), transparent 62%),
    linear-gradient(180deg, transparent 30%, var(--glass-strong) 96%);
}
.modal__head > :not(.modal__ambient) { position: relative; z-index: 1; }

.modal__cover { width: 8.25rem; height: auto; align-self: flex-start; border-radius: var(--r-md);
  box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal--art .modal__cover { box-shadow: var(--shadow-md), 0 6px 30px rgba(var(--cv), .45); }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost);
  width: 8.25rem; aspect-ratio: 2/3; }
.modal__info { min-width: 0; padding-right: var(--s-7); }

.modal__badges { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-bottom: var(--s-2); }
.tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .04em;
  padding: 3px 0.5rem; border-radius: var(--r-pill); color: var(--ink-soft); border: 1px solid var(--line-2); }
.tag--type { color: var(--azure-bright); background: var(--azure-haze);
  border-color: color-mix(in srgb, var(--azure) 30%, transparent); text-transform: uppercase; }
.tag--es { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 35%, transparent); }
.tag--soft { color: var(--ink-faint); }

.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); }
.modal__sub { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: var(--s-1); }

.modal__cov { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); max-width: 20rem; }
.modal__covbar { flex: 1; height: 0.3125rem; border-radius: var(--r-pill); overflow: hidden; background: var(--surface-2); }
.modal__covseg { display: block; height: 100%; background: var(--cyan); }
.modal__covn { font-size: var(--fs-2xs); color: var(--ink-faint); font-family: var(--font-mono); }

.modal__hacts { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.hbtn { display: inline-flex; align-items: center; gap: 0.3125rem; padding: 0.3125rem 0.625rem; border-radius: var(--r-sm);
  font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.hbtn:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.hbtn--accent { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 30%, transparent); }
.hbtn:disabled { opacity: .45; cursor: default; }

.modal__body { padding: var(--s-5); display: flex; flex-direction: column; gap: var(--s-4); overflow-y: auto; }
.synopsis { font-size: var(--fs-sm); line-height: var(--lh-relaxed, 1.7); color: var(--ink-soft); }
.chips { display: flex; flex-wrap: wrap; gap: var(--s-1); }
.chip { font-size: var(--fs-2xs); color: var(--ink-faint); border: 1px solid var(--line); border-radius: var(--r-pill);
  padding: 3px 0.5625rem; }

.chhead { display: flex; align-items: center; gap: var(--s-3); }
.chhead h3 { font-size: var(--fs-md); font-weight: 600; color: var(--ink); }
.chhead__n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); margin-left: 4px; }
.chsearch { flex: 1; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface);
  border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-xs); }
.chsearch:focus { outline: none; border-color: var(--azure); }
.sk { display: flex; flex-direction: column; gap: var(--s-2); }

.chapters { display: flex; flex-direction: column; max-height: 26rem; overflow-y: auto; margin: 0 calc(-1 * var(--s-2)); }
.ch { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm);
  text-align: left; font-size: var(--fs-xs); color: var(--ink-soft); transition: background var(--t-fast), color var(--t-fast); }
.ch:hover { background: var(--surface-2); color: var(--ink); }
.ch.is-read { color: var(--ink-faint); }
.ch.is-current { background: var(--azure-haze); color: var(--azure-bright); font-weight: 600; }
.ch__n { flex: none; min-width: 2.6rem; font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; }
.ch__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch__tag { flex: none; font-size: var(--fs-2xs); font-weight: 600; }
.ch__done { flex: none; color: var(--cyan); opacity: .55; }
.empty { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }

.ovf-enter-active, .ovf-leave-active { transition: opacity var(--t-med); }
.ovf-enter-from, .ovf-leave-to { opacity: 0; }
</style>
