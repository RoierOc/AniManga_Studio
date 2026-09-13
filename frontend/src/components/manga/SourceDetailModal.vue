<script setup>
import { computed, ref } from 'vue'
import { useSourcesStore } from '@/stores/sources'
import { useMangaStore } from '@/stores/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ChapterProgress from '@/components/ui/ChapterProgress.vue'
import { useModal } from '@/lib/useModal'
import { tareaActiva } from '@/lib/manga'
import { genero } from '@/lib/etiquetas'

const store = useSourcesStore()
const manga = useMangaStore()   // el progreso en vivo vive en su mapa `downloads` (SSE)
const bajando = (ch) => !!tareaActiva(manga.downloads, store.dlTask[ch.id])
const d = computed(() => store.detail)
const isInLib = computed(() => store.inLibrary(d.value?.sourceId, d.value?.id))
const genres = computed(() => {
  const g = d.value?.genre
  return Array.isArray(g) ? g : (typeof g === 'string' ? g.split(',').map(x => x.trim()).filter(Boolean) : [])
})

// Escape cierra, el foco no se escapa por detrás y el fondo no scrollea.
const modalEl = ref(null)
useModal(() => !!d.value, () => store.closeDetail(), modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="d" class="ov" @click.self="store.closeDetail()">
        <div class="modal" ref="modalEl">
          <button class="modal__x" @click="store.closeDetail()"><Icon name="close" :size="18" /></button>
          <header class="modal__head">
            <img v-if="d.thumbnailUrl" :src="d.thumbnailUrl" class="modal__cover" :alt="d.title" referrerpolicy="no-referrer" />
            <div v-else class="modal__cover modal__cover--ph"><Icon name="globe" :size="28" /></div>
            <div class="modal__info">
              <span class="modal__src">{{ d.sourceName }}<template v-if="d.sourceLang"> · {{ d.sourceLang }}</template></span>
              <h2 class="modal__title">{{ d.title }}</h2>
              <p v-if="d.author" class="modal__by">{{ d.author }}</p>
              <div class="modal__acts">
                <button class="abtn" :class="{ 'abtn--added': isInLib }" @click="store.addToLibrary()" :disabled="isInLib">
                  <Icon :name="isInLib ? 'check' : 'heart'" :size="13" />
                  {{ isInLib ? 'En biblioteca' : 'Añadir' }}
                </button>
              </div>
              <div v-if="genres.length" class="modal__genres">
                <span v-for="g in genres.slice(0, 5)" :key="g" class="g">{{ genero(g) }}</span>
              </div>
            </div>
          </header>

          <p v-if="d.description" class="modal__desc">{{ d.description }}</p>

          <div class="modal__chapters">
            <div class="modal__chhead">Capítulos <span v-if="store.chapters.length" class="muted">({{ store.chapters.length }})</span></div>
            <div v-if="store.detailLoading" class="center"><Spinner /></div>
            <div v-else-if="!store.chapters.length" class="empty">Sin capítulos.</div>
            <ul v-else class="chaps">
              <li v-for="ch in store.chapters" :key="ch.id" class="chap" :class="{ 'is-read': ch.isRead }">
                <div class="chap__main">
                  <span class="chap__name">{{ ch.name }}</span>
                  <span class="chap__sub">{{ ch.scanlator || '' }}<template v-if="ch.pageCount && ch.pageCount > 0"> · {{ ch.pageCount }} pág.</template></span>
                </div>
                <ChapterProgress v-if="bajando(ch)" :task-id="store.dlTask[ch.id]" class="chap__prog" />
                <div v-else-if="store.downloading[ch.id]" class="chap__prog"><Spinner :size="13" /></div>
                <template v-else>
                  <button class="chap__dl chap__dl--ghost" :disabled="store.reading[ch.id]" @click="store.readChapter(ch)">
                    <Spinner v-if="store.reading[ch.id]" :size="13" /><Icon v-else name="library" :size="14" /> Leer
                  </button>
                  <button class="chap__dl" @click="store.downloadChapter(ch)"><Icon name="download" :size="14" /> Descargar</button>
                </template>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(45rem, 100%); max-height: 88vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 3; width: 2.125rem; height: 2.125rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.modal__x:hover { color: var(--ink); }
.modal__head { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); }
.modal__cover { width: 6.875rem; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); }
.modal__info { min-width: 0; padding-right: var(--s-6); }
.modal__src { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--azure); letter-spacing: .04em; }
.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); margin: 2px 0; }
.modal__by { color: var(--ink-soft); font-size: var(--fs-sm); }
.modal__acts { display: flex; gap: var(--s-2); margin-top: var(--s-3); }
.abtn { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.375rem 0.875rem; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); background: transparent; transition: all var(--t-fast); }
.abtn:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.abtn--added { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 30%, transparent); background: color-mix(in srgb, var(--jade) 8%, transparent); }
.abtn:disabled { cursor: default; opacity: .85; }
.modal__genres { display: flex; flex-wrap: wrap; gap: var(--s-1); margin-top: var(--s-3); }
.g { font-size: var(--fs-2xs); padding: 2px 0.5rem; border-radius: var(--r-pill); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink-soft); }
.modal__desc { padding: var(--s-3) var(--s-5); font-size: var(--fs-sm); color: var(--ink-soft); line-height: var(--lh-body); max-height: 6.25rem; overflow-y: auto; }
.modal__chapters { flex: 1; overflow: hidden; display: flex; flex-direction: column; border-top: 1px solid var(--line); }
.modal__chhead { padding: var(--s-3) var(--s-5); font-weight: 600; }
.muted { color: var(--ink-faint); font-weight: 400; }
.center { display: grid; place-items: center; padding: var(--s-6); }
.empty { text-align: center; color: var(--ink-faint); padding: var(--s-6); }
.chaps { overflow-y: auto; padding: 0 var(--s-3) var(--s-3); }
.chap { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); transition: background var(--t-fast); }
.chap:hover { background: var(--surface); }
.chap.is-read { opacity: .55; }
.chap__main { flex: 1; min-width: 0; }
.chap__name { font-size: var(--fs-sm); font-weight: 500; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chap__sub { font-size: var(--fs-2xs); color: var(--ink-faint); }
.chap__dl { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.chap__dl:hover { color: #fff; background: var(--azure); border-color: transparent; }
.chap__dl--ghost { color: var(--azure-bright); border-color: var(--azure); background: transparent; }
.chap__dl--ghost:hover { background: var(--azure-haze); color: var(--azure-bright); }
.chap__prog { flex-shrink: 0; }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }
@media (max-width: 560px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
