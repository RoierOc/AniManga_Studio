<script setup>
import { computed, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import { useModal } from '@/lib/useModal'

const store = useAnimeStore()
const m = computed(() => store.subTrackModal)
const SRC = { jimaku: 'Jimaku', opensubtitles: 'OpenSubtitles', subdl: 'Subdl', subdivx: 'Subdivx', nyaa: 'Nyaa' }

// Fuentes que REVENTARON (no las que no tenían nada). MEDIDO: Subdivx devuelve 403 en el 100 % de
// las búsquedas y hasta ahora eso se veía exactamente igual que "no hay subtítulos".
const broken = computed(() => (m.value?.sourcesReport || []).filter(r => r.status === 'error'))

// Escape cierra, el foco no se escapa por detrás y el fondo no scrollea.
const modalEl = ref(null)
useModal(() => !!m.value, () => store.subTrackModal = null, modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="m" class="ov" @click.self="store.subTrackModal = null">
        <div class="modal" ref="modalEl">
          <button class="modal__x" @click="store.subTrackModal = null"><Icon name="close" :size="18" /></button>
          <header class="modal__head">
            <h2>{{ m.onPick ? 'Elegir fuente para este episodio' : 'Subtítulos en español' }}</h2>
            <!-- Una película no tiene episodio: mostrar "Episodio 0" era el precio de reusar
                 este modal, y no hace falta pagarlo. -->
            <p>{{ m.anime.title }}<template v-if="m.ep.num > 0"> · Episodio {{ m.ep.num }}</template></p>
          </header>

          <div class="modal__body">
            <!-- Español que YA viene dentro del archivo. Va primero y sin botón a propósito: no
                 hay nada que hacer, el player lo elige solo por `slang`. Antes salía en la lista
                 de "traducir" y te invitaba a traducir español a español. -->
            <section v-if="m.embeddedSpanish?.length">
              <h3 class="sec"><Icon name="check" :size="13" /> Ya en el archivo · no hay que hacer nada</h3>
              <div v-for="t in m.embeddedSpanish" :key="'emb' + t.sub_index" class="trk trk--have">
                <span class="trk__lang">{{ (t.language || 'spa').toUpperCase().slice(0,3) }}</span>
                <span class="trk__name">{{ t.title || `Pista ${t.sub_index + 1}` }} <em>{{ t.codec }}</em></span>
                <span class="trk__src">incrustado</span>
              </div>
              <p class="note">El reproductor la selecciona sola (prioriza latino). Traducir con IA
                crearía otra pista, probablemente peor que ésta.</p>
            </section>

            <!-- Ready-made Spanish subs: inject directly -->
            <section v-if="m.spanishTracks.length">
              <h3 class="sec"><Icon name="check" :size="13" /> Ya en español · listos para usar</h3>
              <button v-for="(t, i) in m.spanishTracks" :key="'s' + i" class="trk trk--ready"
                      @click="m.onPick ? m.onPick({ kind: 'premade', info: t }) : store.directInject(m.anime, m.ep, t)">
                <span class="trk__lang">ES</span>
                <span class="trk__name">{{ t.title || t.release_name || 'Subtítulo español' }}</span>
                <span class="trk__src">{{ SRC[t.source] || t.source }}</span>
                <Icon name="download" :size="14" class="trk__go" />
              </button>
            </section>

            <!-- Embedded tracks: translate to Spanish -->
            <section v-if="m.tracks.length">
              <h3 class="sec"><Icon name="spark" :size="13" /> Traducir pista incrustada (IA)</h3>
              <!-- OJO: se manda `sub_index` (índice ENTRE SUBTÍTULOS: 0,1,2…), NO `index` (índice
                   absoluto del stream en el MKV: 2,3,4…). Mandar `index` hacía que pedir la pista
                   inglesa (index 2) tradujera la ÁRABE (sub_index 2) — ver PENDING_BUGS. -->
              <button v-for="t in m.tracks" :key="'t' + t.sub_index" class="trk"
                      @click="m.onPick ? m.onPick({ kind: 'track', sub_index: t.sub_index, language: t.language }) : store.startTranslate(m.anime, m.ep, t.sub_index)">
                <span class="trk__lang">{{ (t.language || 'und').toUpperCase().slice(0,3) }}</span>
                <span class="trk__name">{{ t.title || `Pista ${t.sub_index + 1}` }} <em>{{ t.codec }}</em></span>
                <span class="trk__act">{{ m.onPick ? 'Usar →' : 'Traducir →' }}</span>
              </button>
            </section>

            <!-- External subs: download + translate -->
            <section v-if="m.externalTracks.length">
              <h3 class="sec"><Icon name="globe" :size="13" /> Externos (traducir IA)</h3>
              <button v-for="(t, i) in m.externalTracks" :key="'e' + i" class="trk"
                      @click="m.onPick ? m.onPick({ kind: 'external', info: t }) : store.startTranslate(m.anime, m.ep, 0, t)">
                <span class="trk__lang">{{ (t.language || 'en').toUpperCase().slice(0,2) }}</span>
                <span class="trk__name">{{ t.title }}</span>
                <span class="trk__src">{{ SRC[t.source] || t.source }}</span>
                <span class="trk__act">{{ m.onPick ? 'Usar →' : 'Traducir →' }}</span>
              </button>
            </section>

            <!-- Una fuente caída NO es una fuente vacía: puede haber subtítulo y no lo estamos
                 viendo. Se dice, con el motivo, para que se pueda reintentar más tarde. -->
            <p v-for="b in broken" :key="b.source" class="broken">
              <Icon name="alert" :size="12" />
              {{ SRC[b.source] || b.source }} no respondió<template v-if="b.error"> · {{ b.error }}</template>
            </p>
            <p v-if="m.missingKeys.length" class="missing">Fuentes sin API key: {{ m.missingKeys.join(', ') }}</p>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(35rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.modal__head { padding: var(--s-5) var(--s-5) var(--s-3); border-bottom: 1px solid var(--line); }
.modal__head h2 { font-size: var(--fs-lg); }
.modal__head p { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: 2px; }
.modal__body { overflow-y: auto; padding: var(--s-4) var(--s-5); display: flex; flex-direction: column; gap: var(--s-4); }
.sec { display: flex; align-items: center; gap: 0.375rem; font-size: var(--fs-xs); font-family: var(--font-mono); letter-spacing: .04em; color: var(--ink-faint); margin-bottom: var(--s-2); text-transform: uppercase; }
.trk { display: flex; align-items: center; gap: var(--s-3); width: 100%; padding: var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line); background: var(--surface); margin-bottom: var(--s-2); transition: all var(--t-fast); text-align: left; }
.trk:hover { border-color: var(--azure); background: var(--surface-2); }
.trk--ready:hover { border-color: var(--jade); }
/* Sin :hover ni cursor: es informativo, no un botón. */
.trk--have { border-color: color-mix(in srgb, var(--jade) 30%, var(--line)); cursor: default; }
.trk--have .trk__lang { background: color-mix(in srgb, var(--jade) 20%, transparent); color: var(--jade); }
.note { font-size: var(--fs-2xs); color: var(--ink-faint); line-height: 1.45; margin-top: 2px; }
.trk__lang { font-family: var(--font-mono); font-size: 0.5625rem; font-weight: 700; padding: 2px 0.375rem; border-radius: var(--r-xs); background: var(--azure-haze); color: var(--azure-bright); flex-shrink: 0; }
.trk--ready .trk__lang { background: color-mix(in srgb, var(--jade) 20%, transparent); color: var(--jade); }
.trk__name { flex: 1; font-size: var(--fs-sm); min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.trk__name em { color: var(--ink-faint); font-style: normal; font-size: var(--fs-2xs); }
.trk__src { font-size: var(--fs-2xs); color: var(--ink-faint); flex-shrink: 0; }
.trk__act { font-size: var(--fs-xs); color: var(--azure-bright); font-weight: 600; flex-shrink: 0; }
.trk__go { color: var(--jade); flex-shrink: 0; }
.missing { font-size: var(--fs-2xs); color: var(--ink-faint); text-align: center; padding-top: var(--s-2); }
.broken { display: flex; align-items: center; justify-content: center; gap: 0.375rem; font-size: var(--fs-2xs);
  color: var(--warn); text-align: center; }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }
</style>
