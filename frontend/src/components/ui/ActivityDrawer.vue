<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import Icon from './Icon.vue'

// Slide-over "glance" panel: everything processing right now, grouped by manga. Reads the
// same store getters as the in-modal progress and the full Activity view → all in lockstep.
const ui = useUiStore()
const store = useMangaStore()
const groups = computed(() => store.processingGroups)

const KIND = {
  download:  { icon: 'download', color: 'var(--azure)',  label: 'Descarga' },
  upscale:   { icon: 'spark',    color: 'var(--cyan)',   label: '4K' },
  export:    { icon: 'library',  color: 'var(--violet)', label: 'Tomo' },
  translate: { icon: 'globe',    color: 'var(--jade)',   label: 'Traducir' },
}
const kind = (k) => KIND[k] || KIND.download
const monogram = (t) => (t || '?').trim().charAt(0).toUpperCase()

function close() { ui.activityOpen = false }
function viewAll() { ui.activityTab = 'active'; ui.goto('activity') }   // goto() also closes the drawer
function onKey(e) { if (e.key === 'Escape' && ui.activityOpen) close() }

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="adr">
      <div v-if="ui.activityOpen" class="adr">
        <div class="adr__scrim" @click="close" />
        <aside class="adr__panel" role="dialog" aria-label="Actividad">
          <header class="adr__head">
            <span class="adr__spin" :class="{ 'is-idle': !store.activeCount }" />
            <h3 class="adr__title">
              {{ store.activeCount ? `${store.activeCount} en proceso` : 'Sin tareas activas' }}
            </h3>
            <button class="adr__link" @click="viewAll">Ver todo <Icon name="chevron" :size="13" /></button>
            <button class="adr__x" @click="close" title="Cerrar"><Icon name="close" :size="16" /></button>
          </header>

          <div class="adr__body">
            <template v-if="groups.length">
              <article v-for="g in groups" :key="g.mangaId" class="grp">
                <div class="grp__head">
                  <span class="grp__cover">
                    <img v-if="g.cover" :src="g.cover" alt="" loading="lazy" />
                    <span v-else class="grp__mono">{{ monogram(g.title) }}</span>
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
                    </div>
                    <div class="row__bar" :class="{ 'is-err': t.status === 'error' }">
                      <span :style="{ width: t.pct + '%', background: kind(t.kind).color }" />
                    </div>
                  </div>
                  <button class="row__act" title="Cancelar" @click="store.cancelAnyTask(t)">
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
.adr__spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--line-2); border-top-color: var(--azure); animation: spin .7s linear infinite; flex-shrink: 0; }
.adr__spin.is-idle { border-top-color: var(--line-2); animation: none; }
.adr__title { flex: 1; font-size: var(--fs-md); font-weight: 600; }
.adr__link { display: inline-flex; align-items: center; gap: 2px; font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright); }
.adr__link:hover { color: #fff; }
.adr__x { width: 28px; height: 28px; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-faint); }
.adr__x:hover { color: var(--ink); }

.adr__body { flex: 1; overflow-y: auto; padding: var(--s-3); display: flex; flex-direction: column; gap: var(--s-3); }

.grp { border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); padding: var(--s-3); }
.grp__head { display: flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-2); }
.grp__cover { width: 26px; height: 26px; border-radius: var(--r-xs); overflow: hidden; flex-shrink: 0; background: var(--surface-3); display: grid; place-items: center; }
.grp__cover img { width: 100%; height: 100%; object-fit: cover; }
.grp__mono { font-size: var(--fs-xs); font-weight: 700; color: var(--ink-faint); }
.grp__title { flex: 1; font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.grp__pct { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.grp__pct.is-err { color: var(--coral); }

.row { display: flex; align-items: center; gap: var(--s-2); padding: 6px 0; }
.row__kind { flex-shrink: 0; display: grid; place-items: center; }
.row__main { flex: 1; min-width: 0; }
.row__top { display: flex; justify-content: space-between; gap: var(--s-2); }
.row__label { font-size: var(--fs-xs); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row__pct { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); flex-shrink: 0; }
.row__bar { height: 3px; margin-top: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.row__bar span { display: block; height: 100%; transition: width var(--t-base) var(--ease-silk); }
.row__bar.is-err span { background: var(--coral) !important; width: 100% !important; }
.row__act { width: 24px; height: 24px; display: grid; place-items: center; border-radius: var(--r-xs); color: var(--ink-faint); border: 1px solid var(--line); flex-shrink: 0; transition: all var(--t-fast); }
.row__act:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

.adr__empty { margin: auto; text-align: center; color: var(--ink-faint); display: flex; flex-direction: column; align-items: center; gap: var(--s-2); padding: var(--s-6); }
.adr__empty p { font-size: var(--fs-md); font-weight: 600; color: var(--ink-soft); }
.adr__empty span { font-size: var(--fs-xs); max-width: 16rem; }

.adr__foot { padding: var(--s-3); border-top: 1px solid var(--line); }
.adr__histlink { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--ink-faint); }
.adr__histlink:hover { color: var(--ink); }

.adr-enter-active .adr__panel, .adr-leave-active .adr__panel { transition: transform var(--t-base) var(--ease-snap); }
.adr-enter-active .adr__scrim, .adr-leave-active .adr__scrim { transition: opacity var(--t-base); }
.adr-enter-from .adr__panel, .adr-leave-to .adr__panel { transform: translateX(100%); }
.adr-enter-from .adr__scrim, .adr-leave-to .adr__scrim { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .adr-enter-active .adr__panel, .adr-leave-active .adr__panel { transition: none; }
}
</style>
