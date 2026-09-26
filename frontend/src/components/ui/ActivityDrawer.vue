<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { isNative, onMessage, send as nativeSend } from '@/lib/nativeBridge'
import { readTaskNotificationsEnabled, taskNotificationUpdate } from '@/lib/taskNotifications'
import Icon from './Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { etaTarea as eta } from '@/lib/eta'
import { imgProxy } from '@/lib/img'
import { retrySse, sseState } from '@/lib/sse'

// Slide-over "glance" panel: everything processing right now, grouped by manga. Reads the
// same store getters as the in-modal progress and the full Activity view → all in lockstep.
const ui = useUiStore()
const store = useMangaStore()
const groups = computed(() => store.processingGroups)
const connectionDown = computed(() => sseState.value === 'down')
let seenTasks = new Map()
let stopTaskWatch
let stopNativeMessages

const KIND = {
  download:  { icon: 'download', color: 'var(--azure)',  label: 'Descarga' },
  upscale:   { icon: 'spark',    color: 'var(--cyan)',   label: '4K' },
  export:    { icon: 'library',  color: 'var(--violet)', label: 'Tomo' },
  translate: { icon: 'globe',    color: 'var(--jade)',   label: 'Traducir' },
  subtitle:  { icon: 'film',     color: 'var(--jade)',   label: 'Subtítulos' },
}
const kind = (k) => KIND[k] || KIND.download
const monogram = (t) => (t || '?').trim().charAt(0).toUpperCase()

function close() { ui.activityOpen = false }
function viewAll() { ui.activityTab = 'active'; ui.goto('activity') }   // goto() also closes the drawer
function retryActivity() { retrySse() }
function onKey(e) { if (e.key === 'Escape' && ui.activityOpen) close() }

onMounted(() => {
  window.addEventListener('keydown', onKey)
  if (!isNative()) return

  stopNativeMessages = onMessage((message) => {
    if (message?.event === 'openActivity') viewAll()
  })
  stopTaskWatch = watch(() => store.normalizedTasks, (tasks) => {
    const foreground = !document.hidden && document.hasFocus()
    const update = taskNotificationUpdate(seenTasks, tasks, readTaskNotificationsEnabled(), foreground)
    seenTasks = update.next
    for (const notification of update.notifications) nativeSend('notify', notification)
  }, { immediate: true })
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  stopTaskWatch?.()
  stopNativeMessages?.()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="adr">
      <div v-if="ui.activityOpen" class="adr">
        <div class="adr__scrim" @click="close" />
        <aside class="adr__panel" role="dialog" aria-label="Actividad">
          <header class="adr__head">
            <!-- Sin tareas se queda quieto: una rueda girando sin nada detrás miente. -->
            <Spinner v-if="store.activeCount" :size="14" />
            <span v-else class="adr__idle" />
            <h3 class="adr__title">
              {{ store.activeCount ? `${store.activeCount} en proceso` : 'Sin tareas activas' }}
            </h3>
            <button class="adr__link" @click="viewAll">Ver todo <Icon name="chevron" :size="13" /></button>
            <button class="adr__x" @click="close" data-tip="Cerrar"><Icon name="close" :size="16" /></button>
          </header>

          <div v-if="connectionDown" class="adr__warn" role="status">
            <Icon name="alert" :size="13" />
            <span>Actividad desconectada; los datos pueden estar desactualizados.</span>
            <button @click="retryActivity">Reintentar</button>
          </div>

          <div class="adr__body">
            <template v-if="groups.length">
              <article v-for="g in groups" :key="g.mangaId" class="grp">
                <div class="grp__head">
                  <span class="grp__cover">
                    <span class="grp__mono">{{ monogram(g.title) }}</span>
                    <img v-if="g.cover" :src="imgProxy(g.cover, 96)" alt="" loading="lazy" decoding="async"
                         @error="$event.target.classList.add('is-fail')"
                         @load="$event.target.classList.remove('is-fail')" />
                  </span>
                  <span class="grp__title">{{ g.title }}</span>
                  <span class="grp__pct" :class="{ 'is-err': g.anyError }">{{ g.pct }}%</span>
                </div>

                <div v-for="t in g.tasks" :key="t.id" class="row">
                  <span class="row__kind" :style="{ color: kind(t.kind).color }">
                    <Icon :name="kind(t.kind).icon" :size="12" />
                  </span>
                  <div class="row__main">
                    <div class="row__top">
                      <span class="row__label">{{ t.label }}</span>
                      <span class="row__pct">{{ t.status === 'error' ? '—' : t.pct + '%' }}</span>
                      <span v-if="eta(t)" class="row__eta">{{ eta(t) }}</span>
                    </div>
                    <div class="row__bar" :class="{ 'is-err': t.status === 'error' }">
                      <span :style="{ width: t.pct + '%', background: kind(t.kind).color }" />
                    </div>
                    <!-- El paso concreto que el backend ya reporta; sin esto el cajón sólo daba
                         un porcentaje mudo. -->
                    <div v-if="t.msg && t.msg !== t.label" class="row__msg">{{ t.msg }}</div>
                  </div>
                  <button class="row__act" data-tip="Cancelar" @click="store.cancelAnyTask(t)">
                    <Icon name="close" :size="13" />
                  </button>
                </div>
              </article>
            </template>

            <div v-else class="adr__empty">
              <Icon name="check" :size="26" />
              <p>Todo tranquilo</p>
              <span>Las descargas, traducciones, 4K y tomos aparecerán aquí mientras se procesan.</span>
            </div>
          </div>

          <footer v-if="store.historyTasks.length" class="adr__foot">
            <button class="adr__histlink" @click="ui.activityTab = 'history'; viewAll()">
              <Icon name="clock" :size="13" /> Ver historial ({{ store.historyTasks.length }})
            </button>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.adr { position: fixed; inset: 0; z-index: 115; }   /* above modal (100), below toasts (120) */
.adr__scrim { position: absolute; inset: 0; background: rgba(7, 10, 18, 0.5); backdrop-filter: blur(2px); }
.adr__panel {
  position: absolute; top: 0; right: 0; bottom: 0; width: min(24rem, 92vw);
  display: flex; flex-direction: column;
  background: var(--glass-strong); backdrop-filter: blur(18px);
  border-left: 1px solid var(--line-2); box-shadow: var(--shadow-lg);
}
.adr__head { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-4) var(--s-4) var(--s-3); border-bottom: 1px solid var(--line); }
.adr__idle { width: 0.875rem; height: 0.875rem; border-radius: 50%; border: 2px solid var(--line-2); flex-shrink: 0; }
.adr__title { flex: 1; font-size: var(--fs-md); font-weight: 600; }
.adr__link { display: inline-flex; align-items: center; gap: 2px; font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); }
.adr__link:hover { color: #fff; }
.adr__x { width: 1.75rem; height: 1.75rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-faint); }
.adr__x:hover { color: var(--ink); }
.adr__warn { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3); color: var(--warn); background: color-mix(in srgb, var(--warn) 8%, transparent); border-bottom: 1px solid color-mix(in srgb, var(--warn) 22%, transparent); font-size: var(--fs-2xs); }
.adr__warn span { flex: 1; min-width: 0; }
.adr__warn button { flex-shrink: 0; color: var(--warn); font-weight: 700; font-size: var(--fs-2xs); }
.adr__warn button:hover { color: var(--ink); text-decoration: underline; }

.adr__body { flex: 1; overflow-y: auto; padding: var(--s-3); display: flex; flex-direction: column; gap: var(--s-3); }

.grp { border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); padding: var(--s-3); }
.grp__head { display: flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-2); }
.grp__cover { width: 1.625rem; height: 1.625rem; border-radius: var(--r-xs); overflow: hidden; flex-shrink: 0; background: var(--surface-3); display: grid; place-items: center; }
/* Inicial DEBAJO de la portada, no `v-else`: un 404 de /thumb la descubre en vez de dejar
   el icono de imagen rota. */
.grp__cover > * { grid-area: 1 / 1; }
.grp__cover img { width: 100%; height: 100%; object-fit: cover; }
.grp__cover img.is-fail { display: none; }
.grp__mono { font-size: var(--fs-xs); font-weight: 700; color: var(--ink-faint); }
.grp__title { flex: 1; font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.grp__pct { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.grp__pct.is-err { color: var(--coral); }

.row { display: flex; align-items: center; gap: var(--s-2); padding: 0.375rem 0; }
.row__kind { flex-shrink: 0; display: grid; place-items: center; }
.row__main { flex: 1; min-width: 0; }
.row__top { display: flex; justify-content: space-between; gap: var(--s-2); }
.row__label { font-size: var(--fs-xs); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__pct { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); flex-shrink: 0; }
.row__eta { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-ghost); flex-shrink: 0; }
.row__msg { font-size: var(--fs-2xs); color: var(--ink-faint); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__bar { height: 3px; margin-top: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.row__bar span { display: block; height: 100%; transition: width var(--t-base) var(--ease-silk); }
.row__bar.is-err span { background: var(--coral) !important; width: 100% !important; }
.row__act { width: 1.5rem; height: 1.5rem; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); border: 1px solid var(--line); flex-shrink: 0; transition: all var(--t-fast); }
.row__act:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.adr__empty { margin: auto; text-align: center; color: var(--ink-faint); display: flex; flex-direction: column; align-items: center; gap: var(--s-2); padding: var(--s-6); }
.adr__empty p { font-size: var(--fs-md); font-weight: 600; color: var(--ink-soft); }
.adr__empty span { font-size: var(--fs-xs); max-width: 16rem; }

.adr__foot { padding: var(--s-3); border-top: 1px solid var(--line); }
.adr__histlink { display: inline-flex; align-items: center; gap: 0.375rem; font-size: var(--fs-xs); color: var(--ink-faint); }
.adr__histlink:hover { color: var(--ink); }

.adr-enter-active .adr__panel, .adr-leave-active .adr__panel { transition: transform var(--t-base) var(--ease-snap); }
.adr-enter-active .adr__scrim, .adr-leave-active .adr__scrim { transition: opacity var(--t-base); }
.adr-enter-from .adr__panel, .adr-leave-to .adr__panel { transform: translateX(100%); }
.adr-enter-from .adr__scrim, .adr-leave-to .adr__scrim { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .adr-enter-active .adr__panel, .adr-leave-active .adr__panel { transition: none; }
}
</style>
