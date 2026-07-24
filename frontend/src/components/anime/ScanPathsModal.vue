<script setup>
import { ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useModal } from '@/lib/useModal'

const store = useAnimeStore()

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
              <div v-for="p in store.scan.paths" :key="p" class="pathrow">
                <Icon name="folder" :size="15" /><span class="pathrow__p">{{ p }}</span>
                <button class="pathrow__x" @click="store.removeScanPath(p)"><Icon name="close" :size="13" /></button>
              </div>
              <div class="addrow">
                <input v-model="store.scan.newPath" placeholder="/ruta/a/carpeta de anime…" @keyup.enter="store.addScanPath()" />
                <button class="btn-sm" @click="store.browse('')" title="Explorar"><Icon name="folder" :size="14" /></button>
                <button class="btn-sm btn-sm--accent" @click="store.addScanPath()">Añadir</button>
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
            <div v-else class="folders">
              <div v-for="f in store.scan.folders" :key="f.folder" class="frow" :class="{ 'is-matched': f.mapped_id }">
                <img v-if="f.matched_cover || f.suggestion?.cover" :src="f.matched_cover || f.suggestion.cover" class="frow__cover" alt="" />
                <div v-else class="frow__cover frow__cover--ph"><Icon name="film" :size="14" /></div>
                <div class="frow__info">
                  <div class="frow__name">{{ f.name }}</div>
                  <div v-if="f.mapped_id" class="frow__match">✓ {{ f.matched_title }}</div>
                  <div v-else-if="f.suggestion" class="frow__sug">Sugerencia: {{ f.suggestion.title }}</div>
                  <div v-else class="frow__sug frow__sug--none">Sin coincidencia</div>
                </div>
                <button v-if="f.mapped_id" class="btn-sm" @click="store.unmatchFolder(f)">Desenlazar</button>
                <button v-else-if="f.suggestion" class="btn-sm btn-sm--accent" @click="store.matchFolder(f, f.suggestion)">Enlazar</button>
              </div>
              <p v-if="!store.scan.folders.length" class="empty">Añade una ruta para detectar carpetas de anime.</p>
            </div>
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
.folders { display: flex; flex-direction: column; gap: var(--s-2); }
.frow { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line); }
.frow.is-matched { border-left: 2px solid var(--jade); }
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
