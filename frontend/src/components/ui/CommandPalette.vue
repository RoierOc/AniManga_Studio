<script setup>
/* Búsqueda universal + paleta de comandos (Ctrl/⌘+K).
 *
 * Abrir un manga costaba tres pasos (ir a Biblioteca → scroll → clic) aun sabiendo exactamente
 * qué querías. Esto lo deja en uno. NO añade estado nuevo de dominio: lee lo que los stores ya
 * tienen en memoria y sólo pide `/api/library` (una vez, perezosamente) porque los mangas locales
 * viven dentro de `LibraryView` y no en un store.
 *
 * Los "comandos" salen de `VIEWS`, la MISMA fuente que pinta el sidebar: una sección nueva
 * aparece aquí sola, sin tocar este archivo.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { useMediaStore } from '@/stores/media'
import { useMangaStore } from '@/stores/manga'
import { imgProxy } from '@/lib/img'
import { useModal } from '@/lib/useModal'
import Icon from './Icon.vue'

const ui = useUiStore()
const anime = useAnimeStore()
const media = useMediaStore()
const manga = useMangaStore()

const open = ref(false)
const q = ref('')
const sel = ref(0)
const inputEl = ref(null)
const boxEl = ref(null)
const listEl = ref(null)

// Biblioteca de manga: una sola petición, la primera vez que se abre la paleta.
const mangaLib = ref([])
let libLoaded = false
async function ensureLib() {
  if (libLoaded) return
  libLoaded = true
  try { mangaLib.value = (await api.get('/api/library')) || [] } catch (_) { libLoaded = false }
}

// ── Comandos: derivados del sidebar, no una lista paralela que se quede vieja ──
const commands = computed(() => {
  const out = []
  for (const g of VIEWS) {
    for (const it of g.items) {
      out.push({
        kind: 'cmd', id: `${it.id}:${it.sub || ''}`, icon: it.icon,
        title: it.label, hint: `Ir a · ${g.group}`,
        run: () => { if (it.sub) _setSub(it.id, it.sub); ui.pushNav(it.id) },
      })
    }
  }
  out.push(
    { kind: 'cmd', id: 'settings', icon: 'settings', title: 'Ajustes', hint: 'Ir a', run: () => ui.pushNav('settings') },
    { kind: 'cmd', id: 'workshop', icon: 'upload', title: 'Importar CBZ/CBR', hint: 'Taller', run: () => ui.pushNav('workshop') },
    { kind: 'cmd', id: 'kitchen', icon: 'palette', title: 'Cocina del diseño', hint: 'Sistema de diseño', run: () => ui.pushNav('kitchen') },
    { kind: 'cmd', id: 'shortcuts', icon: 'spark', title: 'Atajos de teclado', hint: 'Ayuda', run: () => { ui.showShortcuts = true } },
  )
  return out
})

// El sub-tab de anime/media es estado del store, no de la ruta: se fija antes de navegar.
function _setSub(view, sub) {
  if (view === 'anime') anime.sub = sub
  else if (view === 'media') media.setSub(sub)
}

// ── Contenido ────────────────────────────────────────────────────────────
const content = computed(() => {
  const out = []
  for (const m of mangaLib.value) {
    out.push({
      kind: 'manga', id: `mg:${m.id}`, title: m.name, cover: m.cover,
      hint: `Manga · ${m.chapter_count || 0} capítulos`,
      run: () => manga.open(m),
    })
  }
  for (const a of anime.library) {
    out.push({
      kind: 'anime', id: `an:${a.id}`, title: a.title, cover: a.cover,
      hint: 'Anime', run: () => { ui.pushNav('anime'); anime.openDetail(a) },
    })
  }
  for (const it of media.all) {
    out.push({
      kind: 'media', id: `md:${it.kind}:${it.id}`, title: it.title, cover: it.poster,
      hint: it.kind === 'movie' ? 'Película' : 'Serie',
      run: () => { ui.pushNav('media'); media.openDetail(it) },
    })
  }
  return out
})

/* Ranking barato y suficiente: prefijo > palabra > subcadena. Sin fuzzy — con bibliotecas de
 * cientos (no miles) de títulos, un fuzzy sólo añade coincidencias raras arriba. */
function score(title, needle) {
  const t = (title || '').toLowerCase()
  const i = t.indexOf(needle)
  if (i < 0) return -1
  if (i === 0) return 0
  return t[i - 1] === ' ' ? 1 : 2
}

const results = computed(() => {
  const needle = q.value.trim().toLowerCase()
  if (!needle) return commands.value.slice(0, 8)
  const hits = []
  for (const r of [...content.value, ...commands.value]) {
    const s = score(r.title, needle)
    if (s >= 0) hits.push({ r, s })
  }
  hits.sort((a, b) => a.s - b.s || a.r.title.length - b.r.title.length)
  return hits.slice(0, 30).map(h => h.r)
})

watch(results, () => { sel.value = 0 })

function choose(r) {
  if (!r) return
  close()
  r.run()
}

function close() { open.value = false }

async function show() {
  open.value = true
  q.value = ''
  sel.value = 0
  ensureLib()
  await nextTick()
  inputEl.value?.focus()
}

// Mantiene visible la fila seleccionada al moverse con el teclado.
watch(sel, async () => {
  await nextTick()
  listEl.value?.querySelector('.cp__row.is-sel')?.scrollIntoView({ block: 'nearest' })
})

function onGlobalKey(e) {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    open.value ? close() : show()
  }
}
function onBoxKey(e) {
  const n = results.value.length
  if (e.key === 'ArrowDown') { e.preventDefault(); sel.value = n ? (sel.value + 1) % n : 0 }
  else if (e.key === 'ArrowUp') { e.preventDefault(); sel.value = n ? (sel.value - 1 + n) % n : 0 }
  else if (e.key === 'Enter') { e.preventDefault(); choose(results.value[sel.value]) }
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))
useModal(() => open.value, close, boxEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="open" class="cp__ov" @click.self="close">
        <div ref="boxEl" class="cp" role="dialog" aria-label="Búsqueda y comandos">
          <div class="cp__search">
            <Icon name="search" :size="17" class="cp__icon" />
            <input ref="inputEl" v-model="q" class="cp__input" type="text" spellcheck="false"
                   placeholder="Buscar manga, anime, series… o una sección" @keydown="onBoxKey" />
            <kbd class="cp__kbd">esc</kbd>
          </div>

          <div v-if="results.length" ref="listEl" class="cp__list">
            <button v-for="(r, i) in results" :key="r.id" class="cp__row" :class="{ 'is-sel': i === sel }"
                    @click="choose(r)" @mousemove="sel = i">
              <img v-if="r.cover" class="cp__cover" :src="imgProxy(r.cover, 48)" alt="" />
              <span v-else class="cp__glyph"><Icon :name="r.icon || 'library'" :size="15" /></span>
              <span class="cp__txt">
                <span class="cp__title">{{ r.title }}</span>
                <span class="cp__hint">{{ r.hint }}</span>
              </span>
              <Icon name="chevron" :size="14" class="cp__go" />
            </button>
          </div>
          <p v-else class="cp__none">Nada coincide con «{{ q }}».</p>

          <footer class="cp__foot">
            <span><kbd>↑</kbd><kbd>↓</kbd> moverse</span>
            <span><kbd>↵</kbd> abrir</span>
            <span><kbd>ctrl</kbd><kbd>K</kbd> abrir/cerrar</span>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cp__ov {
  position: fixed; inset: 0; z-index: var(--z-toast);
  background: rgba(4, 6, 12, .62); backdrop-filter: blur(6px);
  display: flex; justify-content: center; align-items: flex-start;
  padding: 12vh var(--s-4) var(--s-4);
}
.cp {
  width: min(40rem, 100%); max-height: 68vh; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line-strong); border-radius: var(--r-lg);
  box-shadow: 0 24px 64px -12px rgba(0, 0, 0, .7); overflow: hidden;
}
.cp__search { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4) var(--s-5);
  border-bottom: 1px solid var(--line); }
.cp__icon { color: var(--azure); flex-shrink: 0; }
.cp__input { flex: 1; min-width: 0; background: none; border: 0; outline: none;
  color: var(--ink); font-size: var(--fs-base); }
.cp__input::placeholder { color: var(--ink-faint); }
.cp__kbd, .cp__foot kbd { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint);
  padding: 2px 0.4375rem; border-radius: var(--r-xs); border: 1px solid var(--line); background: var(--base); }

.cp__list { overflow-y: auto; padding: var(--s-2); }
.cp__row { width: 100%; display: flex; align-items: center; gap: var(--s-3);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); text-align: left;
  border: 1px solid transparent; cursor: pointer; }
.cp__row.is-sel { background: var(--azure-haze); border-color: color-mix(in srgb, var(--azure) 30%, transparent); }
.cp__cover { width: 1.75rem; height: 2.625rem; object-fit: cover; border-radius: var(--r-xs); flex-shrink: 0; }
.cp__glyph { width: 1.75rem; height: 2.625rem; display: grid; place-items: center; flex-shrink: 0;
  color: var(--azure); border-radius: var(--r-xs); background: var(--base); }
.cp__txt { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.cp__title { font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cp__hint { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cp__go { color: var(--ink-faint); opacity: 0; flex-shrink: 0; }
.cp__row.is-sel .cp__go { opacity: 1; }
.cp__none { padding: var(--s-7) var(--s-5); text-align: center; color: var(--ink-faint); font-size: var(--fs-sm); }

.cp__foot { display: flex; gap: var(--s-5); padding: var(--s-2) var(--s-5);
  border-top: 1px solid var(--line); font-size: var(--fs-2xs); color: var(--ink-faint); }
.cp__foot span { display: inline-flex; align-items: center; gap: 4px; }
</style>
