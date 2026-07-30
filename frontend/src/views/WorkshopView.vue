<script setup>
import { ref, computed, onMounted } from 'vue'
import { useWorkshopStore } from '@/stores/workshop'
import { useMangaStore } from '@/stores/manga'
import MangaCard from '@/components/manga/MangaCard.vue'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

const store = useWorkshopStore()
const manga = useMangaStore()

const dragging = ref(false)
const showGrid = ref(false)
const anchorStart = ref('')
const renumBase = ref('')

const ARCHIVE_RE = /\.(cbz|cbr|zip|rar)$/i
const st = computed(() => store.staged)

function takeFirst(list) {
  const f = Array.from(list || []).find(f => ARCHIVE_RE.test(f.name))
  if (f) store.analyze(f)
}
function onDrop(e) { dragging.value = false; takeFirst(e.dataTransfer?.files) }
function onInputChange(e) { takeFirst(e.target.files); e.target.value = '' }

function reset() { showGrid.value = false; anchorStart.value = ''; renumBase.value = ''; store.cancelStaged() }
async function doCommit() { await store.commit(); if (!store.staged) reset() }

onMounted(() => store.loadList())
</script>

<template>
  <div class="view">
    <header class="hero stagger">
      <div class="hero__head" style="--i:0">
        <p class="hero__eyebrow"><span class="hero__tick" /> IMPORTA TUS PROPIOS MANGAS</p>
        <h1 class="hero__title">Taller</h1>
        <p class="hero__sub">Sube un CBZ/CBR y el Taller detecta los capítulos automáticamente (por nombres, ComicInfo o contenido). Tú solo confirmas. Luego tradúcelo y escálalo a 4K desde la ficha de siempre.</p>
      </div>
    </header>

    <!-- ── Paso 1: dropzone ────────────────────────────────────────────── -->
    <div v-if="!st" class="dz" :class="{ 'is-drag': dragging }"
         @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
      <template v-if="store.analyzing">
        <Spinner :size="26" />
        <p class="dz__title">Analizando capítulos…</p>
        <p class="dz__hint">Leyendo el archivo y detectando los cortes</p>
      </template>
      <template v-else>
        <Icon name="upload" :size="28" class="dz__ic" />
        <p class="dz__title">Arrastra aquí tu .cbz / .cbr</p>
        <p class="dz__hint">o</p>
        <label class="dz__browse">
          Elegir archivo
          <input type="file" accept=".cbz,.cbr,.zip,.rar" class="dz__input" @change="onInputChange" />
        </label>
        <p class="dz__rangehint">Detecta los capítulos solo; no escribe nada hasta que confirmes.</p>
      </template>
    </div>

    <!-- ── Paso 2: previsualización / confirmación ─────────────────────── -->
    <div v-else class="pv">
      <div class="pv__top">
        <input v-model="st.title" type="text" placeholder="Título de la serie…" class="pv__title" />
        <button class="pv__x" data-tip="Cancelar" @click="reset"><Icon name="close" :size="16" /></button>
      </div>

      <div class="pv__meta">
        <span class="pv__file"><Icon name="folder" :size="13" /> {{ st.filename }}</span>
        <span class="pv__badge" :class="`pv__badge--${st.method}`">{{ store.methodLabel }}</span>
        <span class="muted">{{ st.count }} págs · {{ st.chapters.length }} cap.</span>
      </div>

      <!-- Archivo plano: ofrecer anclado por contenido -->
      <div v-if="st.method === 'flat'" class="pv__flat">
        <p><Icon name="spark" :size="13" /> No detecté estructura de capítulos en los nombres. Indica en qué capítulo empieza el tomo y lo detecto por <strong>contenido</strong>, o ajusta los cortes a mano abajo.</p>
        <div class="pv__flatrow">
          <input v-model="anchorStart" type="number" min="0" step="1" placeholder="empieza en cap…" class="pv__numin" />
          <button class="btn-xs btn-xs--accent" :disabled="st.busy || !anchorStart" @click="store.anchor(anchorStart)">
            <Spinner v-if="st.busy" :size="14" tone="light" /> Auto-detectar por contenido
          </button>
        </div>
      </div>

      <!-- Lista de capítulos detectados -->
      <div class="pv__tools">
        <span class="muted">Capítulos detectados — confirma o ajusta:</span>
        <div class="pv__renum">
          <input v-model="renumBase" type="number" step="1" placeholder="nº 1er cap" class="pv__numin pv__numin--sm" />
          <button class="btn-xs" :disabled="!renumBase" @click="store.renumberFrom(renumBase)" data-tip="Renumerar todos los capítulos desde este número">↻ Renumerar</button>
          <button class="btn-xs" @click="showGrid = !showGrid">{{ showGrid ? 'Ocultar' : 'Ajustar' }} cortes</button>
        </div>
      </div>

      <ul class="pv__chaps">
        <li v-for="(c, i) in st.chapters" :key="c.start" class="pv__chap">
          <img :src="store.thumbUrl(c.start)" class="pv__thumb" loading="lazy" decoding="async" alt="" referrerpolicy="no-referrer" />
          <div class="pv__chinfo">
            <label class="pv__chlabel">Cap.
              <input :value="c.chapter" @input="store.setChapterNum(i, $event.target.value)" class="pv__numin pv__numin--sm" />
            </label>
            <span class="muted">{{ c.count }} págs · pág {{ c.start + 1 }}–{{ c.start + c.count }}</span>
          </div>
          <button v-if="i > 0" class="pv__merge" data-tip="Unir con el capítulo anterior" @click="store.toggleCut(c.start)"><Icon name="close" :size="12" /></button>
        </li>
      </ul>

      <!-- Editor de cortes manual (N4) -->
      <div v-if="showGrid" class="pv__grid">
        <p class="pv__gridhint">Haz clic en una página para marcarla como <strong>inicio de capítulo</strong> (✂) o quitar el corte. El borde cian marca portada.</p>
        <div class="pv__cells">
          <button v-for="idx in st.count" :key="idx - 1" class="pv__cell"
                  :class="{ 'is-start': st.chapters.some(c => c.start === idx - 1), 'is-cover': st.cover === idx - 1 }"
                  @click="store.toggleCut(idx - 1)" @contextmenu.prevent="store.setCover(idx - 1)"
                  :data-tip="`Página ${idx} · clic: corte · clic derecho: portada`">
            <img :src="store.thumbUrl(idx - 1)" class="pv__cellimg" loading="lazy" decoding="async" alt="" referrerpolicy="no-referrer" />
            <span v-if="st.chapters.some(c => c.start === idx - 1)" class="pv__cellbadge">✂</span>
            <span v-else-if="st.cover === idx - 1" class="pv__cellbadge pv__cellbadge--cov">★</span>
            <span class="pv__cellnum">{{ idx }}</span>
          </button>
        </div>
      </div>

      <div class="pv__actions">
        <button class="dz__browse" @click="reset">Cancelar</button>
        <button class="dz__go" :disabled="store.committing || !st.title.trim() || !st.chapters.length" @click="doCommit">
          <Spinner v-if="store.committing" :size="14" tone="light" /><Icon v-else name="spark" :size="15" />
          Importar {{ st.chapters.length }} capítulo(s)
        </button>
      </div>
    </div>

    <!-- ── Importados ──────────────────────────────────────────────────── -->
    <h2 class="sect">Importados</h2>
    <div v-if="store.loading" class="grid">
      <Skeleton v-for="n in 4" :key="n" variant="poster" />
    </div>
    <EmptyState v-else-if="!store.items.length" icon="upload" title="Aún no has importado nada."
                hint="Sube un archivo arriba para empezar." />
    <div v-else class="grid">
      <MangaCard v-for="m in store.items" :key="m.id" :manga="m" @open="manga.open(m)" @play="manga.open(m)" />
    </div>
  </div>
</template>

<style scoped>
.view { padding: var(--s-4) var(--s-6) var(--s-8); max-width: var(--content-max); margin: 0 auto; }

.hero { padding: var(--s-5) 0 var(--s-6); }
.hero__eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.hero__tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.hero__title { font-size: var(--fs-3xl); }
.hero__sub { margin-top: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); max-width: 42rem; }

/* ── Dropzone ─────────────────────────────────────────────────────────── */
.dz { display: flex; flex-direction: column; align-items: center; gap: var(--s-2); padding: var(--s-7) var(--s-5); border: 1.5px dashed var(--line-strong); border-radius: var(--r-lg); background: var(--surface); text-align: center; transition: border-color var(--t-fast), background var(--t-fast); }
.dz.is-drag { border-color: var(--azure); background: var(--azure-haze); }
.dz__ic { color: var(--ink-faint); }
.dz.is-drag .dz__ic { color: var(--azure); }
.dz__title { font-size: var(--fs-md); font-weight: 500; }
.dz__hint { font-size: var(--fs-xs); color: var(--ink-faint); }
.dz__browse { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4); border-radius: var(--r-pill); border: 1px solid var(--line); color: var(--azure-bright); font-size: var(--fs-sm); font-weight: 500; cursor: pointer; transition: all var(--t-fast); }
.dz__browse:hover { border-color: var(--azure); background: var(--azure-haze); }
.dz__input { position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; }
.dz__rangehint { font-size: var(--fs-2xs); color: var(--ink-faint); margin-top: var(--s-1); }
.dz__form { display: flex; gap: var(--s-2); width: 100%; max-width: 28rem; margin-top: var(--s-3); }
.dz__go { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4); border-radius: var(--r-md); background: var(--azure); color: #fff; font-size: var(--fs-sm); font-weight: 500; box-shadow: var(--glow-azure); transition: opacity var(--t-fast); white-space: nowrap; }
.dz__go:disabled { opacity: .45; box-shadow: none; }

/* ── Previsualización ─────────────────────────────────────────────────── */
.pv { border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); padding: var(--s-5); }
.pv__top { display: flex; gap: var(--s-2); align-items: center; }
.pv__title { flex: 1; padding: var(--s-2) var(--s-3); border-radius: var(--r-md); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); font-size: var(--fs-md); font-weight: 500; }
.pv__title:focus { outline: none; border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.pv__x { color: var(--ink-faint); padding: var(--s-2); border-radius: var(--r-sm); }
.pv__x:hover { color: var(--coral); background: var(--surface-2); }

.pv__meta { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; margin-top: var(--s-3); font-size: var(--fs-xs); }
.pv__file { display: inline-flex; align-items: center; gap: 4px; color: var(--ink-soft); }
.pv__badge { padding: 2px var(--s-2); border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; background: var(--azure-haze); color: var(--azure-bright); }
.pv__badge--flat { background: color-mix(in oklab, var(--coral) 18%, transparent); color: var(--coral); }
.pv__badge--filenames, .pv__badge--comicinfo { background: color-mix(in oklab, var(--jade) 18%, transparent); color: var(--jade); }

.pv__flat { margin-top: var(--s-4); padding: var(--s-3); border-radius: var(--r-md); background: var(--surface-2); font-size: var(--fs-xs); color: var(--ink-soft); }
.pv__flat p { display: flex; gap: var(--s-2); align-items: flex-start; }
.pv__flatrow { display: flex; gap: var(--s-2); align-items: center; margin-top: var(--s-2); }

.pv__tools { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-4); margin-bottom: var(--s-2); font-size: var(--fs-xs); }
.pv__renum { display: flex; align-items: center; gap: var(--s-2); }
.pv__numin { width: 7rem; padding: var(--s-1) var(--s-2); font-size: var(--fs-xs); border-radius: var(--r-sm); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink); }
.pv__numin--sm { width: 3.6rem; text-align: center; }
.btn-xs { padding: var(--s-1) var(--s-2); border-radius: var(--r-sm); border: 1px solid var(--line); font-size: var(--fs-2xs); color: var(--ink-soft); white-space: nowrap; }
.btn-xs:hover { border-color: var(--azure); color: var(--azure-bright); }
.btn-xs--accent { background: var(--azure); color: #fff; border-color: transparent; display: inline-flex; align-items: center; gap: var(--s-2); }
.btn-xs:disabled { opacity: .45; }

.pv__chaps { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: var(--s-2); }
.pv__chap { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2); border-radius: var(--r-md); background: var(--surface-2); border: 1px solid var(--line); position: relative; }
.pv__thumb { width: 2.6rem; height: 3.7rem; object-fit: cover; border-radius: var(--r-sm); flex-shrink: 0; background: var(--surface); }
.pv__chinfo { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.pv__chlabel { font-size: var(--fs-xs); font-weight: 500; display: inline-flex; align-items: center; gap: 4px; }
.pv__merge { position: absolute; top: 4px; right: 4px; color: var(--ink-faint); padding: 2px; border-radius: var(--r-sm); }
.pv__merge:hover { color: var(--coral); background: var(--surface); }

.pv__grid { margin-top: var(--s-4); padding-top: var(--s-3); border-top: 1px solid var(--line); }
.pv__gridhint { font-size: var(--fs-2xs); color: var(--ink-faint); margin-bottom: var(--s-2); }
.pv__cells { display: grid; grid-template-columns: repeat(auto-fill, minmax(4.5rem, 1fr)); gap: var(--s-2); max-height: 26rem; overflow-y: auto; padding: 2px; }
.pv__cell { position: relative; border-radius: var(--r-sm); overflow: hidden; border: 2px solid transparent; aspect-ratio: 2/3; background: var(--surface-2); cursor: pointer; }
.pv__cell:hover { border-color: var(--line-strong); }
.pv__cell.is-start { border-color: var(--azure); }
.pv__cell.is-cover { border-color: var(--azure-bright); }
.pv__cellimg { width: 100%; height: 100%; object-fit: cover; display: block; }
.pv__cellbadge { position: absolute; top: 2px; left: 2px; background: var(--azure); color: #fff; font-size: 0.625rem; line-height: 1; padding: 2px 4px; border-radius: var(--r-sm); }
.pv__cellbadge--cov { background: var(--azure-bright); }
.pv__cellnum { position: absolute; bottom: 2px; right: 3px; font-size: 0.5625rem; color: #fff; text-shadow: 0 1px 2px #000; }

.pv__actions { display: flex; justify-content: flex-end; gap: var(--s-2); margin-top: var(--s-5); }

/* ── Grid de importados ───────────────────────────────────────────────── */
.sect { font-size: var(--fs-lg); margin: var(--s-7) 0 var(--s-4); }
/* Ancho base propio; el resto de la rejilla (densidad, hueco, móvil) vive en base.css */
.grid { --card-min: 14.0625rem; }

@media (max-width: 540px) {
  .view { padding: var(--s-3) var(--s-4) var(--s-8); }
}
</style>
