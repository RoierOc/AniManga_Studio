<script setup>
/* La cola de Sonarr/Radarr, como SECCIÓN de la vista única de Descargas.
 *
 * No es una vista aparte, y es deliberado: hay UNA sola pantalla de descargas porque todos los
 * torrents pasan por el mismo qBittorrent (ver `stores/ui.js`, grupo General). Lo que esta sección
 * añade es lo que el torrent no puede saber: un fichero al 100 % puede llevar horas atascado
 * IMPORTANDO, y en qBittorrent eso se ve como «completado».
 *
 * Se dibuja sola si hay algo en cola; si no, no ocupa sitio.
 *
 * Sondeo, no SSE: Sonarr no empuja nada. Se para al desmontar y con la pestaña oculta, porque una
 * cola vacía sondeada cada 3 s durante horas es puro gasto.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/lib/format'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const store = useMediaStore()
const ui = useUiStore()
let timer = null

function tick() {
  if (document.visibilityState === 'visible') store.loadQueue()
}
onMounted(() => { store.loadQueue(); timer = setInterval(tick, 3000) })
onUnmounted(() => clearInterval(timer))

const total = computed(() => store.queue.length)
const bajando = computed(() => store.queue.filter(q => (q.pct ?? 0) < 100).length)

/* Sonarr distingue «bajando» de «importando» con `trackedDownloadState`, y esa diferencia es justo
   la que explica un 100 % que no se mueve. Sin traducirla, la fila parece colgada. */
const ESTADO = {
  downloading: 'Bajando', importPending: 'Esperando import', importing: 'Importando',
  importBlocked: 'Import bloqueado', failedPending: 'Fallo pendiente', imported: 'Importado',
}
const estado = (q) => ESTADO[q.state] || (q.status === 'paused' ? 'En pausa' : q.status || '')

async function cancelar(q) {
  const ok = await ui.confirm({
    title: 'Cancelar descarga', danger: true, confirmLabel: 'Cancelar descarga',
    body: `Se cancelará «${q.title}» y se quitará del cliente de torrents.\n` +
          'Lo bajado hasta ahora se pierde.',
  })
  if (ok) store.cancelQueue(q)
}

async function descartar(q) {
  const ok = await ui.confirm({
    title: 'Descartar esta versión', danger: true, confirmLabel: 'Descartar',
    body: `Además de cancelar, esta release no volverá a cogerse automáticamente.\n` +
          'Úsalo cuando el fichero venga mal, no para «probar otra».',
  })
  if (ok) store.cancelQueue(q, { blocklist: true })
}
</script>

<template>
  <!-- Nada en cola y nada que contar: la sección no existe. Un bloque vacío permanente en la
       vista de descargas de anime sería ruido para quien no usa Cine. -->
  <div v-if="store.queue.length || store.queueError || Object.keys(store.queueErrors).length" class="mdl">
    <header class="mdl__head">
      <h2 class="mdl__h1">Series y películas</h2>
      <p v-if="total" class="mdl__sub">{{ bajando }} en curso · {{ total }} en cola</p>
    </header>

    <p v-if="Object.keys(store.queueErrors).length" class="mdl__warn">
      <Icon name="alert" :size="14" />
      {{ Object.keys(store.queueErrors).join(' y ') }} no respondió: su cola no se está mostrando.
    </p>

    <ErrorState v-if="store.queueError" title="No se pudo leer la cola de Sonarr/Radarr."
                :detail="store.queueError" @retry="store.loadQueue()" />

    <ul v-else class="q">
      <li v-for="q in store.queue" :key="q.app + q.id" class="qi" :class="{ 'is-warn': q.warning }">
        <div class="qi__cov">
          <img v-if="q.poster" :src="imgProxy(q.poster, 120)" :alt="q.title" loading="lazy" decoding="async" />
        </div>

        <div class="qi__main">
          <p class="qi__title">
            {{ q.title }}
            <span v-if="q.detail" class="qi__det">{{ q.detail }}</span>
          </p>
          <p class="qi__rel">{{ q.release }}</p>

          <!-- Sin tamaño conocido la barra va INDETERMINADA: pintar un 0 % sería afirmar algo
               que no sabemos, y es justo cuando el usuario mira si va o no va. -->
          <div class="qi__bar" :class="{ 'is-unknown': q.pct == null }">
            <span :style="q.pct != null ? { width: q.pct + '%' } : null" />
          </div>

          <p class="qi__meta">
            <span class="qi__state">{{ estado(q) }}</span>
            <template v-if="q.pct != null"> · {{ q.pct.toFixed(0) }} %</template>
            <template v-if="q.size"> · {{ formatBytes(q.size - (q.sizeleft || 0)) }} de {{ formatBytes(q.size) }}</template>
            <template v-if="q.timeleft"> · faltan {{ q.timeleft }}</template>
            <template v-if="q.indexer"> · {{ q.indexer }}</template>
          </p>
          <p v-if="q.warning" class="qi__warn"><Icon name="alert" :size="12" /> {{ q.warning }}</p>
        </div>

        <div class="qi__acts">
          <button class="qi__icon" data-tip="Cancelar la descarga" @click="cancelar(q)">
            <Icon name="close" :size="14" />
          </button>
          <button class="qi__icon qi__icon--danger" data-tip="Cancelar y no volver a coger esta versión"
                  @click="descartar(q)">
            <Icon name="trash" :size="14" />
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.mdl { display: block; margin-bottom: var(--s-6); }
.mdl__head { display: flex; align-items: baseline; gap: var(--s-3); margin-bottom: var(--s-3); }
.mdl__h1 { font-family: var(--font-display); font-size: var(--fs-lg); }
.mdl__sub { font-size: var(--fs-sm); color: var(--ink-faint); }
.mdl__warn { display: flex; align-items: center; gap: var(--s-2); margin: 0 0 var(--s-3);
  color: var(--warn); font-size: var(--fs-sm); }

.q { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--s-2); }
.qi { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.qi.is-warn { border-color: color-mix(in srgb, var(--gold) 45%, transparent); }
.qi__cov { flex: none; width: 2.75rem; aspect-ratio: 2/3; border-radius: var(--r-xs); overflow: hidden;
  background: var(--surface-3); }
.qi__cov img { width: 100%; height: 100%; object-fit: cover; }
.qi__main { min-width: 0; flex: 1; }
.qi__title { margin: 0; font-size: var(--fs-sm); font-weight: 600; color: var(--ink); }
.qi__det { margin-left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--azure-bright); }
.qi__rel { margin: 1px 0 0; font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--ink-ghost); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qi__bar { height: 4px; margin: var(--s-2) 0 var(--s-1); border-radius: 2px;
  background: var(--surface-3); overflow: hidden; }
.qi__bar span { display: block; height: 100%; background: var(--azure-bright);
  box-shadow: 0 0 8px var(--azure-glow); transition: width .5s var(--ease-silk); }
/* Indeterminada: una franja que recorre la barra. Dice «está pasando algo» sin decir cuánto. */
.qi__bar.is-unknown span { width: 35%; animation: qslide 1.4s var(--ease-silk) infinite; }
@keyframes qslide { from { transform: translateX(-100%) } to { transform: translateX(300%) } }
.qi__meta { margin: 0; font-size: var(--fs-xs); color: var(--ink-faint); }
.qi__state { color: var(--ink-soft); font-weight: 600; }
.qi__warn { display: flex; align-items: center; gap: 4px; margin: var(--s-1) 0 0;
  font-size: var(--fs-xs); color: var(--gold); }
.qi__acts { flex: none; display: flex; gap: var(--s-2); }
.qi__icon { display: grid; place-items: center; width: 2.1rem; height: 2.1rem; border-radius: var(--r-sm);
  background: var(--surface-2); border: 1px solid var(--line); color: var(--ink-faint);
  cursor: pointer; transition: all var(--t-fast); }
.qi__icon:hover { color: var(--ink); border-color: var(--line-strong); }
.qi__icon--danger:hover { color: var(--danger); border-color: var(--danger); }

@media (max-width: 640px) {
  .qi { flex-wrap: wrap; }
  .qi__acts { width: 100%; justify-content: flex-end; }
}
</style>
