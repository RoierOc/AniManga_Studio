<script setup>
import { computed, defineAsyncComponent, onErrorCaptured, ref, watch, onMounted, onUnmounted } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useAnimeStore } from '@/stores/anime'
import Icon from '@/components/ui/Icon.vue'
import Sidebar from '@/components/layout/Sidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import TitleBar from '@/components/layout/TitleBar.vue'
import { isNative, onMessage } from '@/lib/nativeBridge'
import { supportsVT } from '@/lib/vt'
import Toaster from '@/components/ui/Toaster.vue'
import ActivityDrawer from '@/components/ui/ActivityDrawer.vue'
import ShortcutsModal from '@/components/ui/ShortcutsModal.vue'
import MangaModal from '@/components/manga/MangaModal.vue'
import Reader from '@/components/manga/Reader.vue'
import PlayerOverlay from '@/components/anime/PlayerOverlay.vue'
import NativePlayerOverlay from '@/components/anime/NativePlayerOverlay.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

// Views are code-split into their own chunks (loaded on demand) to shrink the initial
// bundle — the Anime Studio especially pulls in a lot. LibraryView stays eager since it
// is the most common landing view.
// LibraryHub (Biblioteca: pestañas Descargados/Locales + Importar) es la vista de
// aterrizaje → eager; contiene LibraryView. Explorar (MangaDex/Fuentes) y el resto
// van lazy.
import LibraryHub from '@/views/LibraryHub.vue'
const ExploreView  = defineAsyncComponent(() => import('@/views/ExploreView.vue'))
const WorkshopView = defineAsyncComponent(() => import('@/views/WorkshopView.vue'))
const ActivityView = defineAsyncComponent(() => import('@/views/ActivityView.vue'))
const AnimeStudio  = defineAsyncComponent(() => import('@/views/anime/AnimeStudio.vue'))
const SettingsView = defineAsyncComponent(() => import('@/views/SettingsView.vue'))

const ui = useUiStore()
const manga = useMangaStore()

// Dentro de la shell nativa (WebView2 sin marco) pintamos nuestra propia barra de
// título. La clase en <html> activa el hueco superior (--titlebar-h) global.
const native = isNative()
if (native) document.documentElement.classList.add('native-shell')

// Marca <html> cuando estamos en pantalla completa (ventana borderless del lector/F11 O
// el fullscreen propio del reproductor nativo). La CSS usa `native-fs` para ocultar la
// barra de título SOLO en fullscreen; mientras la ventana esté normal la barra queda por
// encima de los overlays inmersivos (lector/player) para poder arrastrar la ventana.
const _anime = useAnimeStore()
watch(
  () => ui.fullscreen || !!_anime.nativePlayer?.fullscreen,
  (fs) => document.documentElement.classList.toggle('native-fs', fs),
  { immediate: true },
)

function onGlobalKey(e) {
  // F11: pantalla completa, en cualquier contexto. Si hay player nativo abierto
  // alterna el suyo; si no, el fullscreen unificado (lector/ventana). Se captura
  // siempre (incluso en inputs) para no ceder al fullscreen del navegador/SO.
  if (e.key === 'F11') {
    e.preventDefault()
    try {
      const a = useAnimeStore()
      if (a.nativePlayer) { a.toggleNativeFullscreen(); return }
    } catch {}
    ui.toggleFullscreen()
    return
  }
  const tag = (e.target?.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return
  if (e.key === '?') { e.preventDefault(); ui.showShortcuts = !ui.showShortcuts }
  else if (e.key === 'Escape' && ui.showShortcuts) ui.showShortcuts = false
}

// Botones laterales del ratón → historial (atrás/adelante). En la shell nativa el
// evento llega por IPC desde Rust; en navegador los botones ya navegan de serie.
let _offNav = null
function onNavigate(d) {
  if (!d || d.event !== 'navigate') return
  if (d.dir === 'back') window.history.back()
  else if (d.dir === 'forward') window.history.forward()
}
// El usuario puede salir del fullscreen del navegador con Esc/F11 del SO → sincroniza.
function onFsChange() { ui._syncFullscreen(!!document.fullscreenElement) }
onMounted(() => {
  ui.initNav()   // capture the landing view as the first history entry (before any nav),
                 // so browser back/forward traverses the whole app, not just details.
  manga.init()
  ui.refreshHiddenStatus()   // sincroniza el modo Biblioteca Oculta con el backend
  const anime = useAnimeStore()
  anime.reportCaps()  // qué códecs decodifica este navegador → log backend
  // gancho de depuración local (app 100% local): permite drivear los stores
  // desde CDP/consola para diagnosticar el player sin tocar la UI
  window.__stores = { anime, manga, ui }
  window.addEventListener('keydown', onGlobalKey)
  document.addEventListener('fullscreenchange', onFsChange)
  _offNav = onMessage(onNavigate)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  document.removeEventListener('fullscreenchange', onFsChange)
  if (_offNav) _offNav()
})

// Every sidebar view has a real component below; the PlaceholderView is only a
// defensive fallback and should never render in normal use.
const meta = computed(() => VIEWS.flatMap(g => g.items).find(i => i.id === ui.currentView))

const VIEW_COMPONENTS = { library: LibraryHub, explore: ExploreView, workshop: WorkshopView, activity: ActivityView, anime: AnimeStudio, settings: SettingsView }
const activeComponent = computed(() => VIEW_COMPONENTS[ui.currentView] || null)

// Error boundary: a render error in any view/modal shows a recoverable panel instead of
// a blank white screen. "Reintentar" remounts the view; "Recargar" does a hard reload.
const crash = ref(null)
const crashKey = ref(0)
onErrorCaptured((err, _inst, info) => {
  console.error('[boundary]', info, err)
  crash.value = { message: String(err?.message || err), info }
  return false   // stop propagation
})
function retry() {
  crash.value = null
  crashKey.value++   // force a fresh remount of the content
}
function reload() { window.location.reload() }
// Navigating away from a broken view clears the error.
watch(() => ui.currentView, () => { crash.value = null })
</script>

<template>
  <div class="shell">
    <TitleBar v-if="native" />
    <Sidebar />
    <div
      v-if="ui.sidebarMobileOpen"
      class="scrim-mobile"
      @click="ui.sidebarMobileOpen = false"
    />

    <div class="shell__main">
      <TopBar />
      <main class="shell__content">
        <div v-if="crash" class="crash">
          <div class="crash__glyph"><Icon name="spark" :size="38" /></div>
          <h2>Algo salió mal en esta vista</h2>
          <p class="crash__msg">{{ crash.message }}</p>
          <div class="crash__actions">
            <button class="crash__btn crash__btn--accent" @click="retry"><Icon name="spark" :size="14" /> Reintentar</button>
            <button class="crash__btn" @click="reload">Recargar la app</button>
          </div>
          <p class="crash__hint">O elige otra sección en la barra lateral.</p>
        </div>
        <!-- Con View Transitions el swap entre secciones lo anima el navegador
             (crossfade + morph de elementos compartidos como el póster); la
             <Transition out-in> de Vue retrasaría el montaje y la captura del
             "después" saldría vacía. Sin soporte, swap Vue como antes. -->
        <template v-else-if="supportsVT">
          <component :is="activeComponent" v-if="activeComponent" :key="ui.currentView + '_' + crashKey" />
          <PlaceholderView v-else :key="ui.currentView" :label="meta?.label" :icon="meta?.icon" />
        </template>
        <Transition v-else name="view" mode="out-in">
          <component :is="activeComponent" v-if="activeComponent" :key="ui.currentView + '_' + crashKey" />
          <PlaceholderView v-else :key="ui.currentView" :label="meta?.label" :icon="meta?.icon" />
        </Transition>
      </main>
    </div>

    <MangaModal />
    <Reader />
    <PlayerOverlay />
    <NativePlayerOverlay />
    <ActivityDrawer />
    <ShortcutsModal />
    <Toaster />
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.shell__main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.shell__content { flex: 1; }

.scrim-mobile {
  position: fixed; inset: 0; z-index: 35;
  background: rgba(7, 10, 18, 0.6); backdrop-filter: blur(2px);
  animation: fade var(--t-base);
}

/* error boundary fallback */
.crash { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 70vh; text-align: center; gap: var(--s-3); padding: var(--s-6); }
.crash__glyph { width: 84px; height: 84px; display: grid; place-items: center; border-radius: var(--r-lg); color: var(--coral); background: color-mix(in srgb, var(--coral) 12%, transparent); border: 1px solid color-mix(in srgb, var(--coral) 30%, transparent); }
.crash h2 { font-size: var(--fs-2xl); }
.crash__msg { color: var(--ink-faint); font-family: var(--font-mono); font-size: var(--fs-xs); max-width: 560px; word-break: break-word; }
.crash__actions { display: flex; gap: var(--s-3); margin-top: var(--s-2); }
.crash__btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-3) var(--s-5); border-radius: var(--r-md); font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.crash__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.crash__btn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.crash__btn--accent:hover { background: var(--azure-bright); color: #fff; }
.crash__hint { color: var(--ink-ghost); font-size: var(--fs-xs); margin-top: var(--s-2); }

/* view crossfade + lift */
.view-enter-active { transition: all var(--t-slow) var(--ease-silk); }
.view-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.view-enter-from { opacity: 0; transform: translateY(10px); }
.view-leave-to   { opacity: 0; transform: translateY(-6px); }
</style>
