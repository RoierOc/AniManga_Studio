<script setup>
/* Traducir subtítulos POR LOTES — escanear idiomas → elegir alcance/política → ejecutar en vivo.
 * Genérico: lo abre AnimeDetail (y MediaDetail en F4) pasándole los episodios ya resueltos. */
import { ref } from 'vue'
import { useSubBatchStore } from '@/stores/subbatch'
import { useModal } from '@/lib/useModal'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

const store = useSubBatchStore()
const boxEl = ref(null)
useModal(() => store.open, () => store.close(), boxEl)

const POLICIES = [
  { id: 'buscar_o_traducir', label: 'Buscar y traducir', hint: 'Premade si existe; si no, IA' },
  { id: 'solo_buscar', label: 'Solo buscar', hint: 'Solo subtítulos ya hechos' },
  { id: 'solo_ia', label: 'Solo IA', hint: 'Traducir siempre con IA (Ollama)' },
]

// Chip de idioma/estado por episodio.
function esChip(e) {
  if (!e.file_present) return { cls: 'is-missing', text: 'Sin archivo' }
  if (e.es_status === 'embedded') return { cls: 'is-es', text: 'ES incluido' }
  if (e.es_status === 'sidecar') return { cls: 'is-es', text: 'ES añadido' }
  if (e.source_langs.length) return { cls: 'is-src', text: e.source_langs.slice(0, 3).join('·').toUpperCase() }
  return { cls: 'is-none', text: 'Sin fuente' }
}

// Etiqueta de la fuente elegida a mano: hay que poder leer de un vistazo QUÉ se va a usar.
const SRC = { jimaku: 'Jimaku', opensubtitles: 'OpenSubtitles', subdl: 'Subdl', subdivx: 'Subdivx', nyaa: 'Nyaa' }
function choiceLabel(c) {
  if (!c) return ''
  if (c.kind === 'premade') return SRC[c.info?.source] || c.info?.source || 'Premade'
  if (c.kind === 'external') return `IA · ${SRC[c.info?.source] || c.info?.source || 'externo'}`
  return `IA · ${(c.language || 'und').toUpperCase().slice(0, 3)}`
}

// Estado en vivo por episodio (durante la ejecución).
const LIVE = {
  processing: { cls: 'is-run', text: 'Procesando…' },
  searching: { cls: 'is-run', text: 'Buscando…' },
  translating: { cls: 'is-run', text: 'Traduciendo…' },
  done: { cls: 'is-ok', text: 'Listo' },
  skipped: { cls: 'is-skip', text: 'Ya tenía' },
  error: { cls: 'is-err', text: 'Sin fuente' },
  cancelled: { cls: 'is-skip', text: 'Cancelado' },
  pending: { cls: '', text: '·' },
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="store.open" class="sbb__ov" @click.self="store.close()">
        <div ref="boxEl" class="sbb" role="dialog" aria-label="Traducir subtítulos por lotes">
          <header class="sbb__head">
            <div>
              <p class="sbb__eyebrow"><Icon name="globe" :size="13" /> SUBTÍTULOS EN ESPAÑOL · LOTE</p>
              <h2 class="sbb__title">{{ store.title }}</h2>
            </div>
            <button class="sbb__x" @click="store.close()"><Icon name="close" :size="18" /></button>
          </header>

          <!-- Escaneando -->
          <div v-if="store.scanning" class="sbb__scan">
            <Skeleton v-for="n in 6" :key="n" height="2.4rem" radius="var(--r-sm)" />
          </div>

          <template v-else>
            <!-- Resumen -->
            <div v-if="store.summary" class="sbb__summary">
              <span><b>{{ store.summary.with_es }}</b> ya en español</span>
              <span class="sbb__dot">·</span>
              <span><b>{{ store.summary.to_process }}</b> a procesar</span>
              <span v-if="store.summary.missing_file" class="sbb__dot">·</span>
              <span v-if="store.summary.missing_file" class="sbb__muted">{{ store.summary.missing_file }} sin archivo</span>
            </div>

            <!-- Configuración (oculta durante la ejecución) -->
            <div v-if="!store.running" class="sbb__cfg">
              <div class="sbb__policies">
                <button v-for="p in POLICIES" :key="p.id" class="sbb__pol" :class="{ 'is-active': store.policy === p.id }"
                        :data-tip="p.hint" @click="store.setPolicy(p.id)">{{ p.label }}</button>
              </div>
              <div class="sbb__sel">
                <button @click="store.selectProcess()">Solo los que faltan</button>
                <button v-if="store.redoable.length" @click="store.selectRedo()">
                  Rehacer los {{ store.redoable.length }} ya hechos
                </button>
                <button @click="store.selectAll()">Todos</button>
                <button @click="store.selectNone()">Ninguno</button>
              </div>
              <label class="sbb__force" data-tip="Vuelve a buscar/traducir aunque el episodio ya tenga español. Sustituye el subtítulo anterior que dejó la app; las pistas del propio archivo no se tocan.">
                <input type="checkbox" :checked="store.force" @change="store.setForce($event.target.checked)" />
                Reinyectar subtítulos (rehacer los que ya tienen)
              </label>
            </div>

            <!-- Tabla de episodios -->
            <div class="sbb__list">
              <div v-for="e in store.episodes" :key="e.episode" class="sbb__row" :class="{ 'is-current': store.batch?.current_episode === e.episode }">
                <label class="sbb__pick" :class="{ 'is-disabled': !e.file_present || store.running }">
                  <input type="checkbox" :disabled="!e.file_present || store.running"
                         :checked="store.selected.has(e.episode)" @change="store.toggle(e.episode)" />
                  <span class="sbb__ep">{{ e.season > 1 ? `T${e.season} ` : '' }}E{{ String(e.episode).padStart(2, '0') }}</span>
                  <span v-if="e.title" class="sbb__eptitle">{{ e.title }}</span>
                </label>
                <!-- En ejecución: estado vivo. En reposo: idioma detectado. -->
                <span v-if="store.running && store.liveOf(e.episode)" class="sbb__chip" :class="LIVE[store.liveOf(e.episode).status]?.cls">
                  <Spinner v-if="['processing','searching','translating'].includes(store.liveOf(e.episode).status)" :size="11" />
                  {{ LIVE[store.liveOf(e.episode).status]?.text || store.liveOf(e.episode).status }}
                  <em v-if="store.liveOf(e.episode).method === 'premade'">premade</em>
                  <em v-else-if="store.liveOf(e.episode).method === 'ia'">IA</em>
                </span>
                <span v-else class="sbb__chip" :class="esChip(e).cls">{{ esChip(e).text }}</span>

                <!-- Fuente elegida a mano para ESTE episodio (manda sobre la política). -->
                <button v-if="!store.running && store.choices[e.episode]" class="sbb__pick-chip"
                        data-tip="Quitar la fuente elegida y volver a la política del lote"
                        @click="store.clearChoice(e.episode)">
                  {{ choiceLabel(store.choices[e.episode]) }} <Icon name="close" :size="11" />
                </button>
                <button v-else-if="!store.running && e.file_present" class="sbb__srcbtn"
                        :disabled="store.picking === e.episode"
                        data-tip="Ver las fuentes de este episodio y elegir cuál usar"
                        @click="store.pickSource(e.episode)">
                  <Spinner v-if="store.picking === e.episode" :size="11" />
                  <template v-else>Fuente…</template>
                </button>
              </div>
            </div>

            <!-- Barra de progreso agregada -->
            <div v-if="store.running && store.batch" class="sbb__prog">
              <div class="sbb__prog-bar"><span :style="{ width: (store.batch.total ? store.batch.done / store.batch.total * 100 : 0) + '%' }" /></div>
              <span class="sbb__prog-lbl">{{ store.batch.done }}/{{ store.batch.total }}</span>
            </div>

            <footer class="sbb__foot">
              <template v-if="store.running">
                <button class="sbb__btn" @click="store.cancel(false)"><Icon name="close" :size="15" /> Detener tras este</button>
                <button class="sbb__btn sbb__btn--danger" @click="store.cancel(true)">Cortar ya</button>
              </template>
              <template v-else>
                <span class="sbb__count">{{ store.selected.size }} seleccionados</span>
                <button class="sbb__btn sbb__btn--primary" :disabled="!store.selected.size" @click="store.start()">
                  <Icon name="globe" :size="16" /> {{ store.force || Object.keys(store.choices).length ? 'Rehacer' : 'Traducir' }}
                  {{ store.selected.size }} episodio{{ store.selected.size === 1 ? '' : 's' }}
                </button>
              </template>
            </footer>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sbb__ov { position: fixed; inset: 0; z-index: var(--z-modal); background: rgba(4,6,12,.62);
  backdrop-filter: blur(6px); display: flex; justify-content: center; align-items: flex-start; padding: 6vh var(--s-4) var(--s-4); }
.sbb { width: min(46rem, 100%); max-height: 86vh; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line-strong); border-radius: var(--r-lg);
  box-shadow: 0 24px 64px -12px rgba(0,0,0,.7); overflow: hidden; }

.sbb__head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-3);
  padding: var(--s-4) var(--s-5); border-bottom: 1px solid var(--line); }
.sbb__eyebrow { display: inline-flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono);
  font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-1); }
.sbb__title { font-size: var(--fs-lg); font-weight: 700; }
.sbb__x { width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm);
  color: var(--ink-soft); border: 1px solid var(--line); flex-shrink: 0; }
.sbb__x:hover { color: var(--ink); border-color: var(--line-strong); }

.sbb__scan { padding: var(--s-4) var(--s-5); display: flex; flex-direction: column; gap: var(--s-2); }
.sbb__summary { padding: var(--s-3) var(--s-5); display: flex; gap: var(--s-2); align-items: center;
  font-size: var(--fs-sm); color: var(--ink-soft); border-bottom: 1px solid var(--line); }
.sbb__summary b { color: var(--ink); }
.sbb__dot { color: var(--ink-faint); }
.sbb__muted { color: var(--ink-faint); }

.sbb__cfg { display: flex; flex-wrap: wrap; gap: var(--s-3); justify-content: space-between; align-items: center;
  padding: var(--s-3) var(--s-5); border-bottom: 1px solid var(--line); }
.sbb__policies { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.sbb__pol { padding: var(--s-2) var(--s-3); border-radius: var(--r-pill); font-size: var(--fs-xs); font-weight: 600;
  color: var(--ink-soft); background: var(--base); border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.sbb__pol:hover { color: var(--ink); border-color: var(--line-strong); }
.sbb__pol.is-active { color: #fff; background: var(--azure); border-color: transparent; }
.sbb__sel { display: flex; gap: var(--s-3); }
.sbb__sel button { font-size: var(--fs-xs); color: var(--ink-faint); text-decoration: underline; }
.sbb__sel button:hover { color: var(--azure-bright); }
.sbb__force { flex-basis: 100%; display: flex; align-items: center; gap: var(--s-2);
  font-size: var(--fs-xs); color: var(--ink-soft); cursor: pointer; }
.sbb__force input { accent-color: var(--azure); width: .875rem; height: .875rem; }
.sbb__force:hover { color: var(--ink); }

.sbb__list { overflow-y: auto; padding: var(--s-2) var(--s-3); flex: 1; }
.sbb__row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid transparent; }
.sbb__row.is-current { border-color: var(--azure); background: var(--azure-haze); }
.sbb__pick { display: flex; align-items: center; gap: var(--s-3); min-width: 0; cursor: pointer; flex: 1; }
.sbb__pick.is-disabled { cursor: default; opacity: .5; }
.sbb__pick input { accent-color: var(--azure); width: 1rem; height: 1rem; flex-shrink: 0; }
.sbb__ep { font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; color: var(--ink); flex-shrink: 0; }
.sbb__eptitle { font-size: var(--fs-sm); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.sbb__chip { display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0; font-size: var(--fs-2xs); font-weight: 600;
  padding: 3px 0.5rem; border-radius: var(--r-pill); border: 1px solid var(--line); color: var(--ink-faint); }
.sbb__chip em { font-style: normal; opacity: .7; }
.sbb__chip.is-es { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); background: color-mix(in srgb, var(--jade) 12%, transparent); }
.sbb__chip.is-src { color: var(--azure-bright); border-color: color-mix(in srgb, var(--azure) 40%, transparent); }
.sbb__chip.is-none, .sbb__chip.is-missing { color: var(--ink-faint); }
.sbb__chip.is-run { color: var(--azure-bright); border-color: var(--azure); }
.sbb__chip.is-ok { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); }
.sbb__chip.is-skip { color: var(--ink-faint); }
.sbb__chip.is-err { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, transparent); }

.sbb__srcbtn, .sbb__pick-chip { display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  font-size: var(--fs-2xs); font-weight: 600; padding: 3px 0.5rem; border-radius: var(--r-pill);
  border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.sbb__srcbtn { color: var(--ink-faint); }
.sbb__srcbtn:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.sbb__pick-chip { color: var(--azure-bright); border-color: var(--azure);
  background: color-mix(in srgb, var(--azure) 12%, transparent); }
.sbb__pick-chip:hover { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 50%, transparent); }

.sbb__prog { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-5); border-top: 1px solid var(--line); }
.sbb__prog-bar { flex: 1; height: 4px; border-radius: 2px; background: var(--surface-2); overflow: hidden; }
.sbb__prog-bar span { display: block; height: 100%; background: var(--azure-bright); box-shadow: var(--glow-azure); transition: width var(--t-base); }
.sbb__prog-lbl { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); }

.sbb__foot { display: flex; align-items: center; justify-content: flex-end; gap: var(--s-3);
  padding: var(--s-3) var(--s-5); border-top: 1px solid var(--line); }
.sbb__count { margin-right: auto; font-size: var(--fs-xs); color: var(--ink-faint); }
.sbb__btn { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4);
  border-radius: var(--r-pill); font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.sbb__btn:hover:not(:disabled) { border-color: var(--line-strong); }
.sbb__btn--primary { background: var(--azure); border-color: transparent; color: #fff; }
.sbb__btn--primary:hover:not(:disabled) { background: var(--azure-bright); box-shadow: var(--glow-azure); }
.sbb__btn--primary:disabled { opacity: .5; cursor: default; }
.sbb__btn--danger { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, transparent); }
</style>
