<script setup>
/* Vitrina: la app, quieta y en pantalla completa, se apaga y deja la BIBLIOTECA expuesta.
 *
 * No hay hero nuevo: es `MediaHero` (arte a sangre, Ken Burns, cascada de portadas, logo de TMDB,
 * precarga de vecinos) llevado a 100vh y sin controles. La única lógica propia es CUÁNDO aparece
 * y de dónde salen las obras.
 *
 * Sólo se despierta con la ventana en pantalla completa y nada abierto encima (lector, player):
 * si estás leyendo o viendo un episodio, "quieto" es lo normal, no ausencia.
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import MediaHero from '@/components/media/MediaHero.vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useAnimeStore } from '@/stores/anime'
import { useMangaStore } from '@/stores/manga'

const IDLE_MS = 120000

const ui = useUiStore()
const anime = useAnimeStore()
const manga = useMangaStore()

const on = ref(false)
const items = ref([])
const now = ref(new Date())
let idleTimer = null
let clockTimer = null

// Con algo abierto encima la quietud no significa nada: estás leyendo o viendo.
const eligible = computed(() => ui.fullscreen && !manga.reader && !anime.player && !anime.nativePlayer)

// 'es', no el locale del SO: si no, el reloj sale en 12 h americano. Ver Schedule.vue.
const hhmm = computed(() => now.value.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }))

/* El arte sale de lo que ya sirve la app; se pide UNA vez, la primera que salta la vitrina.
 * `art` es sólo arte ANCHO (el hero difumina el póster vertical él solo, no hay que estirarlo). */
async function loadItems() {
  if (items.value.length) return
  const [an, mg] = await Promise.all([
    api.get('/api/anime/library').catch(() => []),
    api.get('/api/library/overview').catch(() => []),
  ])
  const fromAnime = (an || []).map(a => ({
    id: 'a' + (a.al_id || a.id), art: a.banner || '',
    artFallback: [a.cover_xl, a.cover].filter(Boolean),
    logo: a.logo || '', overline: 'DE TU BIBLIOTECA', title: a.title || a.name,
    meta: [a.season_year, a.format].filter(Boolean).map(String),
    tags: Array.isArray(a.genres) ? a.genres.slice(0, 3) : [],
  }))
  const fromManga = (mg || []).map(m => ({
    id: 'm' + m.id, art: '', artFallback: [m.cover].filter(Boolean),
    overline: 'MANGA', title: m.name,
    meta: [m.chapter_count ? `${m.chapter_count} capítulos` : ''].filter(Boolean),
  }))
  const pool = [...fromAnime, ...fromManga].filter(it => it.art || it.artFallback?.length)
  for (let i = pool.length - 1; i > 0; i--) {   // barajar: la vitrina no es una lista alfabética
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  items.value = pool.slice(0, 40)
}

async function show() {
  await loadItems()
  if (!items.value.length || !eligible.value) return
  now.value = new Date()
  clockTimer = setInterval(() => { now.value = new Date() }, 20000)
  on.value = true
}

let armedAt = 0
function wake() {
  if (on.value) { on.value = false; clearInterval(clockTimer); arm(); return }
  if (Date.now() - armedAt < 2000) return   // el ratón dispara 60 eventos/s: no rearmar en cada uno
  arm()
}

function arm() {
  armedAt = Date.now()
  clearTimeout(idleTimer)
  idleTimer = eligible.value ? setTimeout(show, IDLE_MS) : null
}

const EVENTS = ['mousemove', 'mousedown', 'wheel', 'keydown', 'touchstart']
watch(eligible, (ok) => {
  for (const e of EVENTS) {
    if (ok) window.addEventListener(e, wake, { passive: true })
    else window.removeEventListener(e, wake)
  }
  if (!ok) { on.value = false; clearInterval(clockTimer); clearTimeout(idleTimer); idleTimer = null }
  else arm()
}, { immediate: true })

onUnmounted(() => {
  for (const e of EVENTS) window.removeEventListener(e, wake)
  clearTimeout(idleTimer); clearInterval(clockTimer)
})
</script>

<template>
  <Transition name="showcase">
    <div v-if="on" class="sc" @click="wake">
      <MediaHero :items="items" bleed />
      <div class="sc__clock">{{ hhmm }}</div>
      <p class="sc__hint">Mueve el ratón para volver</p>
    </div>
  </Transition>
</template>

<style scoped>
.sc { position: fixed; inset: 0; z-index: 200; background: #05070d; cursor: none; overflow: hidden; }
/* El hero es una franja dentro de una vista; aquí ES la vista. */
.sc :deep(.hero) { height: 100vh; margin: 0; }
.sc :deep(.hero__inner) { padding-bottom: clamp(4rem, 12vh, 9rem); }
/* Sin controles: no se navega una vitrina, se mira. */
.sc :deep(.hero__arr), .sc :deep(.hero__dots) { display: none; }

.sc__clock { position: absolute; top: clamp(2rem, 6vh, 4.5rem); right: clamp(2rem, 5vw, 5rem);
  font-size: clamp(3rem, 7vw, 6rem); font-weight: 250; letter-spacing: -.04em; line-height: 1;
  color: rgba(255,255,255,.9); text-shadow: 0 0.125rem 2rem rgba(0,0,0,.6); }
.sc__hint { position: absolute; bottom: var(--s-5); left: 0; right: 0; text-align: center;
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: .12em;
  text-transform: uppercase; color: rgba(255,255,255,.28); }

.showcase-enter-active { transition: opacity 1.4s var(--ease-silk); }
.showcase-leave-active { transition: opacity .35s var(--ease-silk); }
.showcase-enter-from, .showcase-leave-to { opacity: 0; }
</style>
