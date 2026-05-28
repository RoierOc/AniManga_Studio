<script setup>
import { computed, ref, watch } from 'vue'
import { useMangaStore } from '@/stores/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangaStore()
const m = computed(() => store.current)
const upState = (ch) => store.upscaled[ch]          // true | 'partial' | undefined

// modal tabs + tomo export form
const tab = ref('chapters')
const sel = ref(new Set())
const volName = ref('')
const fmt = ref('cbz')
const quality = ref(92)
const downscale = ref(false)

watch(m, (v) => { tab.value = 'chapters'; sel.value = new Set(); volName.value = v?.name || ''; }, { immediate: true })

const toggleSel = (ch) => { const s = new Set(sel.value); s.has(ch) ? s.delete(ch) : s.add(ch); sel.value = s }
const allSelected = computed(() => store.chapters.length > 0 && sel.value.size === store.chapters.length)
const selectAll = () => { sel.value = allSelected.value ? new Set() : new Set(store.chapters.map(c => c.chapter)) }

async function doExport() {
  if (!sel.value.size) return
  const chapters = [...sel.value].sort((a, b) => parseFloat(a) - parseFloat(b))
  await store.exportTomo({ chapters, volumeName: volName.value, format: fmt.value, quality: quality.value, downscaleHalf: downscale.value })
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="m" class="ov" @click.self="store.close()">
        <div class="modal">
          <button class="modal__x" @click="store.close()"><Icon name="close" :size="18" /></button>

          <header class="modal__head">
            <img v-if="m.cover" :src="m.cover" class="modal__cover" :alt="m.name" />
            <div v-else class="modal__cover modal__cover--ph"><Icon name="library" :size="30" /></div>
            <div class="modal__info">
              <h2 class="modal__title">{{ m.name }}</h2>
              <p class="modal__sub">
                {{ chapters?.length || store.chapters.length }} capítulos
                <template v-if="m.source_meta?.sourceName"> · {{ m.source_meta.sourceName }}</template>
              </p>
              <div class="modal__legend">
                <span><span class="lg lg--4k" /> 4K</span>
                <span><span class="lg lg--part" /> parcial</span>
                <span><span class="lg lg--orig" /> original</span>
              </div>
            </div>
          </header>

          <div class="modal__tabs">
            <button class="mtab" :class="{ 'is-active': tab === 'chapters' }" @click="tab = 'chapters'">Capítulos</button>
            <button class="mtab" :class="{ 'is-active': tab === 'tomo' }" @click="tab = 'tomo'"><Icon name="library" :size="13" /> Exportar Tomo</button>
          </div>

          <div class="modal__body">
            <div v-if="store.modalLoading" class="center"><Spinner /></div>
            <div v-else-if="!store.chapters.length" class="empty">Sin capítulos descargados.</div>

            <!-- TOMO EXPORT -->
            <div v-else-if="tab === 'tomo'" class="tomo">
              <div class="tomo__form">
                <label class="fld"><span>Nombre del tomo</span><input v-model="volName" type="text" placeholder="Volumen 1" /></label>
                <div class="fld-row">
                  <label class="fld"><span>Formato</span>
                    <select v-model="fmt"><option value="cbz">CBZ</option><option value="cbr">CBR</option></select>
                  </label>
                  <label class="fld"><span>Calidad: {{ quality }}</span><input v-model.number="quality" type="range" min="85" max="100" /></label>
                </div>
                <label class="chk"><input v-model="downscale" type="checkbox" /> Reducir a la mitad (menor tamaño)</label>
                <button class="exportbtn" :disabled="!sel.size" @click="doExport">
                  <Icon name="download" :size="15" /> Exportar {{ sel.size }} {{ sel.size === 1 ? 'capítulo' : 'capítulos' }}
                </button>
              </div>
              <div class="tomo__pick">
                <button class="selall" @click="selectAll">{{ allSelected ? 'Ninguno' : 'Todos' }}</button>
                <ul class="picklist">
                  <li v-for="c in store.sortedChapters" :key="c.chapter" class="pick" :class="{ 'is-sel': sel.has(c.chapter) }" @click="toggleSel(c.chapter)">
                    <span class="pick__box"><Icon v-if="sel.has(c.chapter)" name="check" :size="12" /></span>
                    <span class="pick__num">Cap. {{ c.chapter }}</span>
                    <span v-if="upState(c.chapter) === true" class="pick__4k">4K</span>
                  </li>
                </ul>
              </div>
            </div>

            <ul v-else class="chaps">
              <li v-for="c in store.sortedChapters" :key="c.chapter" class="chap"
                  :class="{ 'chap--4k': upState(c.chapter) === true, 'chap--part': upState(c.chapter) === 'partial' }">
                <button class="chap__read" @click="store.read(c.chapter)">
                  <span class="chap__num">{{ c.chapter }}</span>
                  <span class="chap__pages">{{ c.page_count }} pág.</span>
                  <span v-if="upState(c.chapter) === true" class="chap__tag chap__tag--4k">4K</span>
                  <span v-else-if="upState(c.chapter) === 'partial'" class="chap__tag chap__tag--part">PARCIAL</span>
                </button>

                <div class="chap__actions">
                  <!-- running upscale -->
                  <div v-if="store.chapterTask(c.chapter)" class="chap__prog">
                    <div class="chap__prog-bar">
                      <span :style="{ width: ((store.chapterTask(c.chapter).progress || 0) / (store.chapterTask(c.chapter).total || 1) * 100) + '%' }" />
                    </div>
                    <span class="chap__prog-n">{{ store.chapterTask(c.chapter).progress || 0 }}/{{ store.chapterTask(c.chapter).total || '?' }}</span>
                    <button class="ib ib--danger" title="Cancelar" @click="store.cancelUpscale(c.chapter)"><Icon name="close" :size="13" /></button>
                  </div>
                  <template v-else>
                    <button class="ib" title="Leer original" @click="store.read(c.chapter, 'original')"><Icon name="library" :size="14" /></button>
                    <button v-if="upState(c.chapter) === 'partial'" class="ib ib--warn" title="Reparar upscale" @click="store.repairChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button v-else-if="upState(c.chapter) !== true" class="ib ib--accent" title="Escalar a 4K" @click="store.upscaleChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button class="ib ib--danger" title="Borrar capítulo" @click="store.deleteChapter(c.chapter)"><Icon name="close" :size="14" /></button>
                  </template>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5);
  background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(720px, 100%); max-height: 88vh; display: flex; flex-direction: column;
  background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { color: var(--ink); border-color: var(--line-strong); }

.modal__head { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); }
.modal__cover { width: 96px; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); }
.modal__info { min-width: 0; padding-right: var(--s-7); }
.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); }
.modal__sub { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: var(--s-1); }
.modal__legend { display: flex; gap: var(--s-3); margin-top: var(--s-3); font-size: var(--fs-2xs); color: var(--ink-faint); }
.modal__legend span { display: inline-flex; align-items: center; gap: 5px; }
.lg { width: 8px; height: 8px; border-radius: 2px; }
.lg--4k { background: var(--cyan); } .lg--part { background: var(--gold); } .lg--orig { background: var(--ink-ghost); }

.modal__tabs { display: flex; gap: var(--s-1); padding: var(--s-2) var(--s-4) 0; border-bottom: 1px solid var(--line); }
.mtab { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm) var(--r-sm) 0 0; font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint); border-bottom: 2px solid transparent; transition: all var(--t-fast); }
.mtab:hover { color: var(--ink); }
.mtab.is-active { color: var(--azure-bright); border-bottom-color: var(--azure); }

.modal__body { overflow-y: auto; padding: var(--s-3); }

/* tomo export */
.tomo { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); padding: var(--s-2); }
.tomo__form { display: flex; flex-direction: column; gap: var(--s-3); }
.fld { display: flex; flex-direction: column; gap: 5px; font-size: var(--fs-xs); color: var(--ink-faint); }
.fld input[type=text], .fld select { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.fld input:focus, .fld select:focus { outline: none; border-color: var(--azure); }
.fld-row { display: flex; gap: var(--s-3); }
.fld-row .fld { flex: 1; }
.chk { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; }
.exportbtn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; margin-top: var(--s-2); padding: var(--s-3); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.exportbtn:hover:not(:disabled) { background: var(--azure-bright); }
.exportbtn:disabled { opacity: .5; cursor: not-allowed; }

.tomo__pick { display: flex; flex-direction: column; min-height: 0; }
.selall { align-self: flex-start; margin-bottom: var(--s-2); font-size: var(--fs-xs); color: var(--azure-bright); }
.picklist { overflow-y: auto; max-height: 320px; display: flex; flex-direction: column; gap: 2px; }
.pick { display: flex; align-items: center; gap: var(--s-2); padding: 6px var(--s-2); border-radius: var(--r-xs); cursor: pointer; transition: background var(--t-fast); }
.pick:hover { background: var(--surface); }
.pick.is-sel { background: var(--azure-haze); }
.pick__box { width: 18px; height: 18px; display: grid; place-items: center; border-radius: 4px; border: 1px solid var(--line-strong); color: #fff; flex-shrink: 0; }
.pick.is-sel .pick__box { background: var(--azure); border-color: var(--azure); }
.pick__num { flex: 1; font-size: var(--fs-sm); }
.pick__4k { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

@media (max-width: 560px) { .tomo { grid-template-columns: 1fr; } }
.center { display: grid; place-items: center; padding: var(--s-7); }
.empty { text-align: center; color: var(--ink-faint); padding: var(--s-7); }

.chaps { display: flex; flex-direction: column; gap: 4px; }
.chap { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid transparent; transition: background var(--t-fast), border-color var(--t-fast); }
.chap:hover { background: var(--surface); border-color: var(--line); }
.chap--4k { border-left: 2px solid var(--cyan); }
.chap--part { border-left: 2px solid var(--gold); }
.chap__read { flex: 1; display: flex; align-items: center; gap: var(--s-3); text-align: left; min-width: 0; }
.chap__num { font-family: var(--font-display); font-weight: 600; font-size: var(--fs-md); min-width: 48px; }
.chap__pages { font-size: var(--fs-xs); color: var(--ink-faint); }
.chap__tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; padding: 1px 6px; border-radius: var(--r-xs); }
.chap__tag--4k { color: var(--cyan); background: var(--cyan-glow); }
.chap__tag--part { color: var(--gold); background: color-mix(in srgb, var(--gold) 16%, transparent); }

.chap__actions { display: flex; align-items: center; gap: 4px; }
.ib { width: 32px; height: 30px; display: grid; place-items: center; border-radius: var(--r-xs); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast); }
.ib:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ib--accent:hover { color: var(--cyan); border-color: var(--cyan-glow); }
.ib--warn:hover { color: var(--gold); border-color: color-mix(in srgb, var(--gold) 40%, transparent); }
.ib--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.chap__prog { display: flex; align-items: center; gap: var(--s-2); }
.chap__prog-bar { width: 80px; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.chap__prog-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.chap__prog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 540px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
