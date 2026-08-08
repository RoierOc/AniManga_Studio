<script setup>
import { computed, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useModal } from '@/lib/useModal'
// Las portadas de AniList van por el proxy como en el RESTO de la app: pedidas en crudo, unas
// cuantas no llegan y la fila se queda con el hueco. Era el «no tienen imágenes».
import { imgProxy } from '@/lib/img'

const store = useAnimeStore()
const enlazadas = computed(() => store.scan.folders.filter(f => f.mapped_id).length)

// La sugerencia de cada fila se pide cuando la fila ENTRA EN PANTALLA, no al abrir el modal:
// son una llamada a AniList por carpeta y no tiene sentido gastarlas en filas que nadie mira.
const vVisible = {
  mounted(el, binding) {
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { binding.value(); obs.disconnect() }
    }, { rootMargin: '200px' })   // un poco antes de asomar, para que llegue ya puesta
    obs.observe(el)
    el._obs = obs
  },
  unmounted(el) { el._obs?.disconnect() },
}

// Escape cierra, el foco no se escapa por detrás y el fondo no scrollea.
const modalEl = ref(null)
useModal(() => store.scan.show, () => store.scan.show = false, modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="store.scan.show" class="ov" @click.self="store.scan.show = false">
        <div class="modal" ref="modalEl">
          <button class="modal__x" @click="store.scan.show = false"><Icon name="close" :size="18" /></button>
          <header class="modal__head"><h2>Carpetas de anime local</h2><p>Escanea carpetas y enlázalas con AniList para añadirlas a tu biblioteca.</p></header>

          <div class="modal__body">
            <!-- paths -->
            <div class="paths">
              <div v-for="p in store.scan.paths" :key="p.path" class="pathrow" :class="{ 'is-gone': !p.exists }">
                <Icon name="folder" :size="15" /><span class="pathrow__p">{{ p.path }}</span>
                <span v-if="!p.exists" class="pathrow__gone" data-tip="La carpeta no responde: disco desconectado o movida">no disponible</span>
                <button class="pathrow__x" @click="store.removeScanPath(p.path)"><Icon name="close" :size="13" /></button>
              </div>
              <div class="addrow">
                <input v-model="store.scan.newPath" placeholder="/ruta/a/carpeta de anime…" @keyup.enter="store.addScanPath()" />
                <button class="btn-sm" @click="store.browse('')" data-tip="Explorar"><Icon name="folder" :size="14" /></button>
                <button class="btn-sm btn-sm--accent" :disabled="store.scan.adding" @click="store.addScanPath()">
                  <Spinner v-if="store.scan.adding" :size="13" /><template v-else>Añadir</template>
                </button>
              </div>
            </div>

            <!-- folder browser -->
            <div v-if="store.scan.browseOpen" class="browser">
              <div class="browser__bar">
                <button v-if="store.scan.browseParent" class="btn-sm" @click="store.browse(store.scan.browseParent)">↑ Subir</button>
                <span class="browser__path">{{ store.scan.browseWin || store.scan.browsePath || '/' }}</span>
                <button class="btn-sm btn-sm--accent" v-if="store.scan.browsePath" @click="store.addScanPath(store.scan.browsePath)">Usar esta</button>
                <button class="btn-sm" @click="store.scan.browseOpen = false">Cerrar</button>
              </div>
              <div class="browser__list">
                <button v-for="it in store.scan.browseItems" :key="it.path" class="browser__item" @click="store.browse(it.path)">
                  <Icon name="folder" :size="14" /> {{ it.name }}
                </button>
              </div>
            </div>

            <!-- detected folders -->
            <div v-if="store.scan.loading" class="center"><Spinner /></div>
            <template v-else>
            <div v-if="store.scan.folders.length" class="tally">
              <span><b>{{ store.scan.folders.length }}</b> carpetas · <b>{{ enlazadas }}</b> enlazadas</span>
              <span v-if="store.scan.suggesting" class="tally__sug">
                <Spinner :size="12" /> buscando {{ store.scan.suggesting }} sugerencias…
              </span>
            </div>
            <div class="folders">
              <div v-for="f in store.scan.folders" :key="f.folder" class="frow"
                   :class="{ 'is-matched': f.mapped_id, 'is-fresh': f._justMatched }"
                   v-visible="() => store.suggestFor(f)">
                <div class="frow__top">
                  <img v-if="(f.matched_cover || f.suggestion?.cover) && f._img !== false"
                       :src="imgProxy(f.matched_cover || f.suggestion.cover, 160)" class="frow__cover"
                       alt="" loading="lazy" @error="f._img = false" />
                  <div v-else class="frow__cover frow__cover--ph"><Icon name="film" :size="14" /></div>
                  <div class="frow__info">
                    <div class="frow__name">{{ f.name }}</div>
                    <div v-if="f.mapped_id" class="frow__match">✓ {{ f.matched_title }}</div>
                    <div v-else-if="f._sugLoading" class="frow__sug frow__sug--load"><Spinner :size="11" /> buscando sugerencia…</div>
                    <div v-else-if="f.suggestion" class="frow__sug">Sugerencia: {{ f.suggestion.title }}</div>
                    <div v-else class="frow__sug frow__sug--none">Sin coincidencia — búscala abajo</div>
                  </div>
                  <button v-if="f.mapped_id" class="btn-sm" @click="store.unmatchFolder(f)">Desenlazar</button>
                  <button v-else-if="f.suggestion" class="btn-sm btn-sm--accent" @click="store.matchFolder(f, f.suggestion)">Enlazar</button>
                </div>

                <!-- Búsqueda a mano: la sugerencia falla con nombres de release y con carpetas
                     que no se llaman como la serie. Sin esto no hay forma de enlazarlas. -->
                <div v-if="!f.mapped_id" class="frow__search">
                  <input v-model="f._q" :placeholder="f.suggestion ? 'Buscar otra…' : 'Buscar la serie en AniList…'"
                         @keyup.enter="store.searchScanFolder(f)" />
                  <button class="btn-sm" :disabled="f._searching" @click="store.searchScanFolder(f)"
                          data-tip="Buscar en AniList" aria-label="Buscar en AniList">
                    <Spinner v-if="f._searching" :size="13" /><Icon v-else name="search" :size="14" />
                  </button>
                </div>
                <div v-if="f._results?.length" class="frow__results">
                  <button v-for="r in f._results" :key="r.id" class="res" @click="store.matchFolder(f, r)">
                    <!-- Una URL que no carga es lo mismo que no tener portada: hueco con icono,
                         no un espacio en blanco que parece un fallo de la app. -->
                    <img v-if="r.cover && r._img !== false" :src="imgProxy(r.cover, 120)" class="res__cover"
                         alt="" loading="lazy" @error="r._img = false" />
                    <div v-else class="res__cover res__cover--ph"><Icon name="film" :size="12" /></div>
                    <div class="res__info">
                      <div class="res__t">{{ r.title }}</div>
                      <!-- El título de dos temporadas se parece demasiado: lo que las separa de
                           verdad es el año y el número de episodios. -->
                      <div class="res__meta">{{ [r.format, r.season, r.episodes ? r.episodes + ' ep' : ''].filter(Boolean).join(' · ') }}</div>
                    </div>
                  </button>
                </div>
              </div>
              <p v-if="!store.scan.folders.length" class="empty">Añade una ruta para detectar carpetas de anime.</p>
            </div>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(42.5rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.modal__head { padding: var(--s-5) var(--s-5) var(--s-3); border-bottom: 1px solid var(--line); }
.modal__head h2 { font-size: var(--fs-lg); }
.modal__head p { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: 2px; }
.modal__body { overflow-y: auto; padding: var(--s-4) var(--s-5); display: flex; flex-direction: column; gap: var(--s-4); }
.paths { display: flex; flex-direction: column; gap: var(--s-2); }
.pathrow { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); font-size: var(--fs-sm); color: var(--ink-soft); }
.pathrow__p { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pathrow.is-gone { border-color: color-mix(in srgb, var(--coral) 45%, var(--line)); }
.pathrow.is-gone .pathrow__p { opacity: .55; text-decoration: line-through; }
.pathrow__gone { font-size: var(--fs-2xs); font-weight: 600; color: var(--coral); white-space: nowrap; }
.pathrow__x { color: var(--ink-faint); }
.pathrow__x:hover { color: var(--coral); }
.addrow { display: flex; gap: var(--s-2); }
.addrow input { flex: 1; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.addrow input:focus { outline: none; border-color: var(--azure); }
.btn-sm { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); white-space: nowrap; }
.btn-sm:hover { color: var(--ink); border-color: var(--line-strong); }
.btn-sm--accent { background: var(--azure); color: #fff; border-color: transparent; }
.btn-sm--accent:hover { background: var(--azure-bright); }
.browser { border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--base); overflow: hidden; }
.browser__bar { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-bottom: 1px solid var(--line); }
.browser__path { flex: 1; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.browser__list { max-height: 12.5rem; overflow-y: auto; padding: var(--s-2); display: flex; flex-direction: column; gap: 2px; }
.browser__item { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2); border-radius: var(--r-xs); font-size: var(--fs-sm); color: var(--ink-soft); text-align: left; }
.browser__item:hover { background: var(--surface); color: var(--ink); }
.center { display: grid; place-items: center; padding: var(--s-5); }
.tally { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); font-size: var(--fs-xs); color: var(--ink-faint); padding-bottom: var(--s-1); }
.tally b { color: var(--ink); font-variant-numeric: tabular-nums; }
.tally__sug { display: inline-flex; align-items: center; gap: var(--s-2); }
.folders { display: flex; flex-direction: column; gap: var(--s-2); }
.frow__sug--load { display: inline-flex; align-items: center; gap: var(--s-2); }
.frow { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); }
.frow__top { display: flex; align-items: center; gap: var(--s-3); }
.frow__search { display: flex; gap: var(--s-2); }
.frow__search input { flex: 1; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-xs); }
.frow__search input:focus { outline: none; border-color: var(--azure); }
.frow__search .btn-sm { display: grid; place-items: center; min-width: 2.25rem; }
.frow__results { display: flex; flex-direction: column; gap: 2px; max-height: 13rem; overflow-y: auto; }
.res { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2); border-radius: var(--r-xs); text-align: left; font-size: var(--fs-xs); color: var(--ink-soft); }
.res:hover { background: var(--base); color: var(--ink); }
.res__cover { width: 1.75rem; height: 2.5rem; object-fit: cover; border-radius: var(--r-xs); flex-shrink: 0; }
.res__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); }
.res__info { flex: 1; min-width: 0; }
.res__t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.res__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
/* El verde entra con transición para que se VEA el cambio; el destello dura lo justo para que el
   ojo lo pille sin quedarse dando la nota. `prefers-reduced-motion` lo apaga. */
.frow { transition: border-color var(--t-base), background var(--t-base); }
.frow.is-matched { border-left: 2px solid var(--jade); }
.frow.is-fresh { animation: fresh 1.2s var(--ease-snap); }
@keyframes fresh {
  0%   { background: color-mix(in srgb, var(--jade) 26%, var(--surface)); transform: scale(1.012); }
  40%  { background: color-mix(in srgb, var(--jade) 14%, var(--surface)); transform: scale(1); }
  100% { background: var(--surface); }
}
.frow__match { animation: pop var(--t-base) var(--ease-snap); }
@keyframes pop { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .frow.is-fresh, .frow__match { animation: none; }
}
.frow__cover { width: 2.25rem; height: 3.125rem; object-fit: cover; border-radius: var(--r-xs); flex-shrink: 0; }
.frow__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); }
.frow__info { flex: 1; min-width: 0; }
.frow__name { font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.frow__match { font-size: var(--fs-xs); color: var(--jade); }
.frow__sug { font-size: var(--fs-xs); color: var(--ink-faint); }
.frow__sug--none { opacity: .6; }
.empty { color: var(--ink-faint); text-align: center; padding: var(--s-5); }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }
</style>
