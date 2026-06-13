<script setup>
import { computed, ref, watch } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { formatChapter } from '@/lib/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangaStore()
const m = computed(() => store.current)
const upState = (ch) => store.upscaled[ch]          // true | 'partial' | undefined

const LANG_FLAG = { en: '🇬🇧', es: '🇪🇸', 'es-la': '🇲🇽', ja: '🇯🇵', 'pt-br': '🇧🇷', fr: '🇫🇷', ko: '🇰🇷', zh: '🇨🇳', 'zh-hk': '🇭🇰', it: '🇮🇹', de: '🇩🇪', ru: '🇷🇺' }
const flag = (l) => LANG_FLAG[l] || l

// bulk upscale range
const rangeFrom = ref('')
const rangeTo = ref('')
function doRange() { if (rangeFrom.value && rangeTo.value) store.upscaleRange(rangeFrom.value, rangeTo.value) }
// color pages
function detectColors() { store.loadColorPages([...sel.value]) }

// modal tabs + tomo export form
const tab = ref('chapters')
const sel = ref(new Set())
const volName = ref('')
const fmt = ref('cbz')
const quality = ref(92)
const downscale = ref(false)

// management panel
const showManage = ref(false)
const renameVal = ref('')
const coverUrlVal = ref('')
const tomoCover = ref('')      // b64 cover for the exported tomo

watch(m, (v) => {
  tab.value = 'chapters'; sel.value = new Set(); volName.value = v?.name || ''
  showManage.value = false; renameVal.value = v?.name || ''; coverUrlVal.value = ''
  tomoCover.value = ''; rangeFrom.value = ''; rangeTo.value = ''
  if (v) { store.resetMdex(); store.loadHealth(); store.colorPages = []; store.excludedPages = []; store.exportPreview = { pages: 0, est_mb: 0, upscaled_pages: 0, original_pages: 0 } }
  if (v && !Object.keys(store.models).length) store.loadModels()
  if (v) store.loadDestinations()
}, { immediate: true })

// Live tomo estimate: recompute whenever the selection, quality or exclusions change
// while the export tab is open.
watch([sel, quality, tab, () => store.excludedPages.length], () => {
  if (tab.value === 'tomo') store.loadExportPreview([...sel.value], quality.value)
})

function applyVol(vol) {
  const { selected, label } = store.applyMdexVolume(vol)
  if (selected.length) { sel.value = new Set(selected); volName.value = label }
}

function onTomoCover(e) {
  const f = e.target.files?.[0]; if (!f) return
  const r = new FileReader(); r.onload = () => { tomoCover.value = r.result }; r.readAsDataURL(f)
}

async function saveMeta() {
  const ok = await store.editMeta({ newTitle: renameVal.value.trim(), coverUrl: coverUrlVal.value.trim() })
  if (ok) showManage.value = false
}
function onCoverFile(e) {
  const f = e.target.files?.[0]; if (!f) return
  const r = new FileReader()
  r.onload = () => store.editMeta({ coverB64: r.result })
  r.readAsDataURL(f)
}

const toggleSel = (ch) => { const s = new Set(sel.value); s.has(ch) ? s.delete(ch) : s.add(ch); sel.value = s }
const allSelected = computed(() => store.chapters.length > 0 && sel.value.size === store.chapters.length)
const selectAll = () => { sel.value = allSelected.value ? new Set() : new Set(store.chapters.map(c => c.chapter)) }

async function doExport(toDrive = false) {
  if (!sel.value.size) return
  const chapters = [...sel.value].sort((a, b) => parseFloat(a) - parseFloat(b))
  await store.exportTomo({ chapters, volumeName: volName.value, format: fmt.value, quality: quality.value, downscaleHalf: downscale.value, coverB64: tomoCover.value || store.mdex.coverB64, toDrive })
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
              <a v-if="store.mdId" :href="'https://mangadex.org/title/' + store.mdId" target="_blank" rel="noopener" class="mdlink" title="Ver en MangaDex">
                <Icon name="globe" :size="13" /> MangaDex
              </a>
              <div class="modal__legend">
                <span><span class="lg lg--4k" /> 4K</span>
                <span><span class="lg lg--part" /> parcial</span>
                <span><span class="lg lg--orig" /> original</span>
              </div>
              <div class="modal__hacts">
                <button class="hbtn hbtn--accent" @click="store.upscaleAll()"><Icon name="spark" :size="13" /> Escalar todo 4K</button>
                <span class="rangebox">
                  <input v-model="rangeFrom" placeholder="de" inputmode="decimal" />
                  <input v-model="rangeTo" placeholder="a" inputmode="decimal" />
                  <button @click="doRange" title="Escalar rango">4K rango</button>
                </span>
                <button class="hbtn" @click="showManage = !showManage" :class="{ 'is-on': showManage }">Gestionar</button>
                <button class="hbtn" @click="store.scanCorrupt()">Verificar</button>
              </div>
            </div>
          </header>

          <!-- Management panel -->
          <Transition name="info">
            <div v-if="showManage" class="manage">
              <section class="manage__sect">
                <span class="manage__label">Información del manga</span>
                <div class="manage__row">
                  <label class="mf"><span>Renombrar</span><input v-model="renameVal" type="text" /></label>
                  <label class="mf"><span>Portada (URL)</span><input v-model="coverUrlVal" type="text" placeholder="https://…" /></label>
                </div>
                <div class="manage__actions">
                  <button class="delbtn" @click="store.deleteManga()" title="Eliminar este manga de la biblioteca"><Icon name="close" :size="13" /> Eliminar manga</button>
                  <span class="manage__spacer" />
                  <label class="upbtn"><Icon name="library" :size="13" /> Subir portada<input type="file" accept="image/*" @change="onCoverFile" hidden /></label>
                  <button class="savebtn" @click="saveMeta">Guardar cambios</button>
                </div>
              </section>
              <section class="manage__sect manage__sect--up">
                <span class="manage__label">Escalado 4K <em>· se aplica al instante</em></span>
                <div class="manage__row">
                  <label class="mf"><span>Modelo</span>
                    <select :value="store.activeModel" @change="store.setModel($event.target.value)">
                      <option v-for="(label, key) in store.models" :key="key" :value="key">{{ label }}</option>
                    </select>
                  </label>
                  <label class="mf mf--chk"><span>Modo eco <em>(deja correr MPV al escalar)</em></span>
                    <input type="checkbox" :checked="store.eco" @change="store.setEco($event.target.checked)" />
                  </label>
                </div>
              </section>
            </div>
          </Transition>

          <div class="modal__tabs">
            <button class="mtab" :class="{ 'is-active': tab === 'chapters' }" @click="tab = 'chapters'">Capítulos</button>
            <button class="mtab" :class="{ 'is-active': tab === 'tomo' }" @click="tab = 'tomo'"><Icon name="library" :size="13" /> Exportar Tomo</button>
          </div>

          <div class="modal__body">
            <div v-if="store.modalLoading" class="center"><Spinner /></div>
            <div v-else-if="!store.chapters.length && !store.hasSourceMeta" class="empty">Sin capítulos descargados.</div>

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
                <label class="upbtn"><Icon name="library" :size="13" /> {{ tomoCover ? 'Portada elegida ✓' : 'Portada del tomo (opcional)' }}<input type="file" accept="image/*" @change="onTomoCover" hidden /></label>
                <div v-if="sel.size && store.exportPreview.pages" class="tprev">
                  <span class="tprev__main">{{ store.exportPreview.pages }} págs · ~{{ store.exportPreview.est_mb }} MB</span>
                  <span v-if="store.exportPreview.upscaled_pages" class="tprev__tag tprev__tag--4k">{{ store.exportPreview.upscaled_pages }} en 4K</span>
                  <span v-if="store.exportPreview.original_pages" class="tprev__tag">{{ store.exportPreview.original_pages }} original</span>
                </div>
                <button class="exportbtn" :disabled="!sel.size" @click="doExport(false)">
                  <Icon name="download" :size="15" /> Exportar {{ sel.size }} {{ sel.size === 1 ? 'capítulo' : 'capítulos' }}
                </button>
                <button v-if="store.drive.connected" class="exportbtn exportbtn--drive" :disabled="!sel.size" @click="doExport(true)">
                  <Icon name="globe" :size="15" /> Exportar a Google Drive
                </button>
                <div class="dests">
                  <button v-if="!store.drive.connected" class="dlink" @click="store.connectDrive()">Conectar Google Drive</button>
                  <template v-else><span class="dok">Drive: {{ store.drive.email }}</span><button class="dlink" @click="store.disconnectDrive()">Desconectar</button></template>
                  <a v-if="store.webdav.phoneUrl" :href="store.webdav.phoneUrl" target="_blank" class="dlink">Abrir en el móvil ↗</a>
                </div>

                <!-- color pages exclusion -->
                <div class="colors">
                  <button class="btn-xs" :disabled="store.colorLoading || !sel.size" @click="detectColors">
                    <span v-if="store.colorLoading" class="xspin" />Detectar páginas a color
                  </button>
                  <div v-if="store.colorPages.length" class="colors__grid">
                    <button v-for="cp in store.colorPages" :key="cp.filename" class="colorpg" :class="{ 'is-excl': store.excludedPages.includes(cp.filename) }"
                            :title="cp.label + (store.excludedPages.includes(cp.filename) ? ' (excluida)' : '')" @click="store.toggleExclude(cp.filename)">
                      <img :src="cp.url" loading="lazy" alt="" />
                      <span v-if="store.excludedPages.includes(cp.filename)" class="colorpg__x"><Icon name="close" :size="12" /></span>
                    </button>
                  </div>
                  <p v-else-if="!store.colorLoading && store.colorPages.length === 0 && sel.size" class="colors__hint">Pulsa para detectar y excluir páginas a color del tomo.</p>
                </div>
              </div>
              <!-- MangaDex volumes + covers -->
              <div class="mdex">
                <div class="mdex__head">
                  <span>Tomos de MangaDex</span>
                  <button class="btn-xs" :disabled="store.mdex.volumesLoading" @click="store.loadMdexVolumes(); store.loadMdexCovers()">
                    <span v-if="store.mdex.volumesLoading" class="xspin" />{{ store.mdex.volumes.length ? 'Recargar' : 'Cargar' }}
                  </button>
                </div>
                <div v-if="store.mdex.volumes.length" class="mdex__vols">
                  <button v-for="v in store.mdex.volumes" :key="v.volume" class="volchip" @click="applyVol(v)">{{ v.label || ('Tomo ' + v.volume) }}</button>
                </div>
                <div v-if="store.mdex.covers.length" class="mdex__covers">
                  <button v-for="c in store.mdex.covers" :key="c.id || c.url" class="covsel" :class="{ 'is-sel': store.mdex.selectedCover?.url === c.url }" @click="store.selectMdexCover(c)">
                    <img :src="c.url256 || c.url" loading="lazy" alt="" />
                    <span v-if="c.volume && c.volume !== 'none'" class="covsel__v">{{ c.volume }}</span>
                    <span v-if="store.mdex.coverLoadingId === c.id" class="covsel__load"><span class="xspin" /></span>
                  </button>
                </div>
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

            <template v-if="tab === 'chapters' && !store.modalLoading && (store.chapters.length || store.hasSourceMeta)">
            <div class="modal__chhead">
              <span>Capítulos</span>
              <select v-if="store.mdLangs.length > 1" v-model="store.mdLang" class="langsel">
                <option value="">Todos</option>
                <option v-for="l in store.mdLangs" :key="l" :value="l">{{ flag(l) }} {{ l }}</option>
              </select>
            </div>

            <ul class="chaps">
              <template v-for="c in (store.hasSourceMeta ? store.mergedChapters : store.sortedChapters)" :key="c.chapter">
              <li class="chap"
                  :class="{ 'chap--4k': upState(c.chapter) === true, 'chap--part': upState(c.chapter) === 'partial', 'chap--src': c._sourceId || c._mdChapterId, 'chap--md': !!c._mdChapterId }">
                <!-- Downloaded chapter: clickable to read -->
                <button v-if="!c._sourceId && !c._mdChapterId" class="chap__read" @click="store.read(c.chapter)">
                  <span v-if="store.isChapterRead(c.chapter)" class="chap__read-dot" title="Leído" />
                  <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                  <span class="chap__pages">{{ c.page_count }} pág.</span>
                  <span v-if="upState(c.chapter) === true" class="chap__tag chap__tag--4k">4K</span>
                  <span v-else-if="upState(c.chapter) === 'partial'" class="chap__tag chap__tag--part" :title="store.health[c.chapter] ? `Faltan ${store.health[c.chapter].missing_upscaled} págs.` : ''">PARCIAL<template v-if="store.health[c.chapter]?.missing_upscaled"> ·{{ store.health[c.chapter].missing_upscaled }}</template></span>
                </button>
                <!-- Source/MD chapter: not clickable, show download info -->
                <div v-else class="chap__read">
                  <span v-if="c._mdLang" class="chap__flag" :title="c._mdLang">{{ flag(c._mdLang) }}</span>
                  <span class="chap__num">{{ formatChapter(c.chapter) }}</span>
                  <span class="chap__pages" v-if="c._sourceId">vía {{ store.current.source_meta?.sourceName }}</span>
                  <span class="chap__pages" v-else-if="c._mdLang">{{ c._mdGroup || 'MangaDex' }}<template v-if="c.page_count"> · {{ c.page_count }} pág.</template></span>
                  <span class="chap__pages" v-else>MangaDex</span>
                </div>

                <div class="chap__actions">
                  <!-- Source / MD chapter: download button with spinner -->
                  <template v-if="c._sourceId || c._mdChapterId">
                    <span v-if="c._mdGroup || c._mdTitle" class="chap__srcmeta">{{ c._mdGroup || c._scanlator }}<template v-if="c._mdTitle"> · {{ c._mdTitle }}</template></span>
                    <template v-if="store.downloadByChapter[c.chapter]">
                      <div class="chap__dlprog">
                        <svg class="dl-ring" viewBox="0 0 24 24">
                          <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                          <circle class="dl-ring__fill" cx="12" cy="12" r="9"
                            :style="{ strokeDashoffset: 56.5 - (56.5 * (store.downloadByChapter[c.chapter].pct / 100)) }" />
                        </svg>
                        <span class="chap__dlprog-n" v-if="store.downloadByChapter[c.chapter].total">{{ store.downloadByChapter[c.chapter].pct }}%</span>
                      </div>
                    </template>
                    <button v-else class="chap__dlbtn"
                      @click="c._sourceId ? store.downloadSourceChapter(c) : store.downloadMdChapter(c)">
                      <Icon name="download" :size="14" /> Descargar
                    </button>
                  </template>
                  <!-- running upscale -->
                  <template v-else>
                  <div v-if="store.upscaleByChapter[c.chapter]" class="chap__dlprog">
                    <svg class="dl-ring" viewBox="0 0 24 24">
                      <circle class="dl-ring__track" cx="12" cy="12" r="9" />
                      <circle class="dl-ring__fill" cx="12" cy="12" r="9" :style="{ strokeDashoffset: 56.5 - (56.5 * (store.upscaleByChapter[c.chapter].pct / 100)) }" />
                    </svg>
                    <span class="chap__dlprog-n" v-if="store.upscaleByChapter[c.chapter].total">{{ store.upscaleByChapter[c.chapter].pct }}%</span>
                    <button class="ib ib--danger" title="Cancelar" @click="store.cancelUpscale(c.chapter)"><Icon name="close" :size="13" /></button>
                  </div>
                  <template v-else>
                    <button class="ib" title="Comparar versiones (scanlations)" :class="{ 'ib--accent': store.scanCmp.open && store.scanCmp.chapter === c.chapter }" @click="store.openComparePanel(c.chapter)"><Icon name="globe" :size="14" /></button>
                    <button class="ib" title="Leer original" @click="store.read(c.chapter, 'original')"><Icon name="library" :size="14" /></button>
                    <button v-if="upState(c.chapter) === 'partial'" class="ib ib--warn" title="Reparar upscale" @click="store.repairChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button v-else-if="upState(c.chapter) !== true" class="ib ib--accent" title="Escalar a 4K" @click="store.upscaleChapter(c.chapter)"><Icon name="spark" :size="14" /></button>
                    <button class="ib ib--danger" title="Borrar capítulo" @click="store.deleteChapter(c.chapter)"><Icon name="close" :size="14" /></button>
                  </template>
                  </template>
                </div>

                <!-- scanlation variants compare panel -->
                <div v-if="store.scanCmp.open && store.scanCmp.chapter === c.chapter" class="cmpvar">
                  <div v-if="store.scanCmp.loading" class="cmpvar__load"><Spinner :size="14" /></div>
                  <template v-else-if="store.scanCmp.variants.length">
                    <span class="cmpvar__lbl">Comparar con variante descargada:</span>
                    <button v-for="v in store.scanCmp.variants" :key="v.dir" class="cmpvar__item" @click="store.readCompareSources(c.chapter, v.dir)">
                      {{ v.group }} <em>{{ v.lang }}</em> · {{ v.page_count }} pág.
                    </button>
                  </template>
                  <span v-else class="cmpvar__none">No hay variantes descargadas en <code>_compare/</code> para este capítulo.</span>
                </div>
              </li>
              </template>
            </ul>
            </template>
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
  background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { color: var(--ink); border-color: var(--line-strong); }

.modal__head { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); flex-shrink: 0; }
/* align-self:flex-start stops the flex row from stretching the cover to the (taller)
   info column's height. Keep the natural aspect ratio (width fixed, height auto) so the
   cover is shown whole — no cropping the sides, no distortion. */
.modal__cover { width: 132px; height: auto; align-self: flex-start; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); width: 132px; aspect-ratio: 2/3; }
.modal__info { min-width: 0; padding-right: var(--s-7); }
.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); }
.modal__sub { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: var(--s-1); }
.mdlink { display: inline-flex; align-items: center; gap: 5px; margin-top: var(--s-2); font-size: var(--fs-xs); font-weight: 500; color: var(--violet); text-decoration: none; padding: 4px 10px; border-radius: var(--r-sm); border: 1px solid color-mix(in srgb, var(--violet) 25%, transparent); transition: all var(--t-fast); }
.mdlink:hover { background: color-mix(in srgb, var(--violet) 10%, transparent); border-color: var(--violet); }
.modal__legend { display: flex; gap: var(--s-3); margin-top: var(--s-3); font-size: var(--fs-2xs); color: var(--ink-faint); }
.modal__legend span { display: inline-flex; align-items: center; gap: 5px; }
.lg { width: 8px; height: 8px; border-radius: 2px; }
.lg--4k { background: var(--cyan); } .lg--part { background: var(--gold); } .lg--orig { background: var(--ink-ghost); }

.modal__hacts { display: flex; gap: var(--s-2); margin-top: var(--s-3); }
.hbtn { display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.hbtn:hover { color: var(--ink); border-color: var(--line-strong); }
.hbtn.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.hbtn--accent { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 30%, transparent); }
.hbtn--accent:hover { background: var(--cyan-glow); color: #d6fffb; }
.mf--chk { flex-direction: row; align-items: center; justify-content: space-between; }
.mf--chk input { width: auto; }
.manage { padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); background: var(--base); overflow: hidden; flex-shrink: 0; }
/* expand/collapse animation for the management panel */
.info-enter-active, .info-leave-active { transition: max-height var(--t-base) var(--ease-silk), opacity var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { max-height: 0; opacity: 0; }
.info-enter-to, .info-leave-from { max-height: 340px; opacity: 1; }
.manage__sect { display: flex; flex-direction: column; gap: var(--s-2); }
.manage__sect + .manage__sect { margin-top: var(--s-3); padding-top: var(--s-3); border-top: 1px solid var(--line); }
.manage__label { font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); text-transform: uppercase; color: var(--ink-faint); }
.manage__label em { font-style: normal; text-transform: none; letter-spacing: 0; color: var(--ink-ghost); }
.manage__sect--up .manage__label { color: var(--cyan); }
.mf em { font-style: normal; color: var(--ink-ghost); }
.manage__row { display: flex; gap: var(--s-3); }
.mf { flex: 1; display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-xs); color: var(--ink-faint); }
.mf input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.mf input:focus { outline: none; border-color: var(--azure); }
.mf select {
  padding: var(--s-2) 30px var(--s-2) var(--s-3); border-radius: var(--r-sm);
  background-color: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm);
  cursor: pointer; appearance: none; -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239aa7bd' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.mf select:hover { border-color: var(--line-strong); }
.mf select:focus { outline: none; border-color: var(--azure); box-shadow: 0 0 0 3px var(--azure-haze); }
.mf select option { background: var(--surface-2); color: var(--ink); }
.manage__actions { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); }
.manage__spacer { flex: 1; }
.delbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500; color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); background: transparent; transition: all var(--t-fast); }
.delbtn:hover { background: color-mix(in srgb, var(--coral) 12%, transparent); border-color: var(--coral); }
.upbtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); cursor: pointer; }
.upbtn:hover { color: var(--ink); }
.savebtn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }
.savebtn:hover { background: var(--azure-bright); }

.modal__tabs { display: flex; gap: var(--s-1); padding: var(--s-2) var(--s-4) 0; border-bottom: 1px solid var(--line); flex-shrink: 0; }
.mtab { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4); border-radius: var(--r-sm) var(--r-sm) 0 0; font-size: var(--fs-sm); font-weight: 500; color: var(--ink-faint); border-bottom: 2px solid transparent; transition: all var(--t-fast); }
.mtab:hover { color: var(--ink); }
.mtab.is-active { color: var(--azure-bright); border-bottom-color: var(--azure); }

/* flex-basis auto (not 0): the modal has max-height, not a fixed height, so basis:0
   would collapse this scroll region to 0 and hide the chapters. auto lets it size to
   content and only shrink+scroll once the modal hits its max-height. */
.modal__body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--s-3); }

/* tomo export */
.tomo { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4); padding: var(--s-2); align-items: start; }
.tomo__form { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; gap: var(--s-3); }
.mdex { grid-column: 1; grid-row: 2; border-top: 1px solid var(--line); padding-top: var(--s-3); }
.tomo__pick { grid-column: 2; grid-row: 1 / span 2; }

.mdex__head { display: flex; align-items: center; justify-content: space-between; font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: var(--s-2); }
.btn-xs { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: var(--r-sm); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); }
.btn-xs:hover { background: var(--azure-haze); }
.xspin { width: 11px; height: 11px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; display: inline-block; }
.mdex__vols { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: var(--s-3); }
.volchip { padding: 4px 10px; border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--violet); border: 1px solid color-mix(in srgb, var(--violet) 30%, transparent); transition: all var(--t-fast); }
.volchip:hover { background: color-mix(in srgb, var(--violet) 14%, transparent); }
.mdex__covers { display: grid; grid-template-columns: repeat(auto-fill, minmax(3rem, 1fr)); gap: 6px; max-height: 180px; overflow-y: auto; }
.covsel { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.covsel img { width: 100%; height: 100%; object-fit: cover; }
.covsel.is-sel { border-color: var(--azure); }
.covsel__v { position: absolute; bottom: 0; left: 0; right: 0; font-family: var(--font-mono); font-size: 8px; text-align: center; background: rgba(7,10,18,.75); color: var(--ice); }
.covsel__load { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(7,10,18,.6); }

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
.exportbtn--drive { background: transparent; color: var(--azure-bright); border: 1px solid var(--azure); margin-top: var(--s-1); }
.exportbtn--drive:hover:not(:disabled) { background: var(--azure-haze); color: var(--azure-bright); }
.rangebox { display: inline-flex; align-items: center; gap: 3px; }
.rangebox input { width: 42px; padding: 4px 6px; border-radius: var(--r-xs); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-2xs); text-align: center; }
.rangebox button { padding: 4px 8px; border-radius: var(--r-xs); font-size: var(--fs-2xs); font-weight: 600; color: var(--cyan); border: 1px solid color-mix(in srgb, var(--cyan) 30%, transparent); }
.rangebox button:hover { background: var(--cyan-glow); color: #d6fffb; }
.chap__read-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--jade); flex-shrink: 0; }
.chap__flag { font-size: 1.05rem; line-height: 1; flex-shrink: 0; }
.colors { margin-top: var(--s-2); }
.colors__hint { font-size: var(--fs-2xs); color: var(--ink-faint); margin-top: var(--s-1); }
.colors__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(2.75rem, 1fr)); gap: 5px; margin-top: var(--s-2); max-height: 150px; overflow-y: auto; }
.colorpg { position: relative; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden; border: 2px solid transparent; }
.colorpg img { width: 100%; height: 100%; object-fit: cover; }
.colorpg.is-excl { border-color: var(--coral); }
.colorpg.is-excl img { opacity: .4; }
.colorpg__x { position: absolute; inset: 0; display: grid; place-items: center; color: var(--coral); background: rgba(7,10,18,.4); }
.tprev { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-top: var(--s-1); font-size: var(--fs-xs); color: var(--ink-soft); }
.tprev__main { font-family: var(--font-mono); color: var(--ink); }
.tprev__tag { font-size: var(--fs-2xs); padding: 1px 7px; border-radius: var(--r-pill); color: var(--ink-faint); border: 1px solid var(--line-2); }
.tprev__tag--4k { color: var(--cyan); border-color: var(--cyan-glow); }
.dests { display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center; margin-top: var(--s-2); font-size: var(--fs-xs); }
.dlink { color: var(--azure-bright); }
.dok { color: var(--jade); }

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
.modal__chhead { display: flex; align-items: center; justify-content: space-between; padding: var(--s-3) var(--s-3); font-weight: 600; font-size: var(--fs-sm); border-bottom: 1px solid var(--line); }
.langsel { padding: 4px 8px; border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-xs); }
.langsel:focus { outline: none; border-color: var(--azure); }
.chap { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid transparent; transition: background var(--t-fast), border-color var(--t-fast); }
.chap:hover { background: var(--surface); border-color: var(--line); }
.chap--4k { border-left: 2px solid var(--cyan); }
.chap--part { border-left: 2px solid var(--gold); }
.chap--src { border-left: 2px solid var(--violet); opacity: .85; }
.chap--md { border-left: 2px solid var(--coral); opacity: .85; }
.chap--src .chap__read, .chap--md .chap__read { cursor: default; }
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
.chap__dlbtn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); border: 1px solid var(--azure); background: transparent; transition: all var(--t-fast); }
.chap__dlbtn:hover:not(:disabled) { background: var(--azure-haze); color: #fff; }
.chap__dlbtn:disabled { opacity: .5; cursor: not-allowed; }
.chap__srcmeta { font-size: var(--fs-2xs); color: var(--ink-ghost); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chap__dlprog { display: inline-flex; align-items: center; gap: var(--s-2); padding: 4px 10px; border-radius: var(--r-sm); background: var(--azure-haze); border: 1px solid var(--azure); }
.chap__dlprog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure-bright); min-width: 28px; }
.dl-ring { width: 16px; height: 16px; flex-shrink: 0; }
.dl-ring__track { fill: none; stroke: var(--surface-3); stroke-width: 3; }
.dl-ring__fill { fill: none; stroke: var(--azure); stroke-width: 3; stroke-linecap: round; stroke-dasharray: 56.5; transform: rotate(-90deg); transform-origin: 12px 12px; transition: stroke-dashoffset .4s var(--ease-silk); }

.chap__prog { display: flex; align-items: center; gap: var(--s-2); }
.chap__prog-bar { width: 80px; height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.chap__prog-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base); }
.chap__prog-n { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--cyan); }
.cmpvar { width: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); margin-top: 2px; border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line); }
.cmpvar__load { padding: var(--s-1); }
.cmpvar__lbl { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cmpvar__item { padding: 4px 10px; border-radius: var(--r-pill); font-size: var(--fs-2xs); color: var(--violet); border: 1px solid color-mix(in srgb, var(--violet) 30%, transparent); }
.cmpvar__item em { font-style: normal; color: var(--ink-faint); }
.cmpvar__item:hover { background: color-mix(in srgb, var(--violet) 14%, transparent); }
.cmpvar__none { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cmpvar__none code { font-family: var(--font-mono); }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 540px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
