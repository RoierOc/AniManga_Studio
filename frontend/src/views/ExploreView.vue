<script setup>
/* Explorar: unifica MangaDex y Fuentes (Suwayomi) en una sola vista, pero son
 * cosas MUY distintas → se eligen con un selector claro arriba y se conserva
 * TODO lo de cada una (KeepAlive mantiene el estado al cambiar de una a otra). */
import { computed, defineAsyncComponent } from 'vue'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'

const MangaDexView = defineAsyncComponent(() => import('@/views/MangaDexView.vue'))
const SourcesView  = defineAsyncComponent(() => import('@/views/SourcesView.vue'))

const TABS = [
  { id: 'mangadex', label: 'MangaDex', icon: 'search', desc: 'Catálogo oficial' },
  { id: 'sources',  label: 'Fuentes',  icon: 'globe',  desc: 'Extensiones (Suwayomi)' },
]
// En el store (`ui.tabs.exp`), no en un ref local: así el historial la ve y "atrás" vuelve a ella.
const ui = useUiStore()
const tab = computed(() => ui.tabs.exp)
const setTab = (id) => ui.setTab('exp', id)
const current = computed(() => (tab.value === 'sources' ? SourcesView : MangaDexView))
</script>

<template>
  <div class="explore">
    <div class="srcpick" role="tablist" aria-label="Fuente de búsqueda">
      <button v-for="t in TABS" :key="t.id" class="srcpick__opt" :class="{ 'is-on': tab === t.id }"
              role="tab" :aria-selected="tab === t.id" @click="setTab(t.id)">
        <Icon :name="t.icon" :size="18" class="srcpick__ic" />
        <span class="srcpick__txt">
          <b>{{ t.label }}</b>
          <small>{{ t.desc }}</small>
        </span>
      </button>
    </div>

    <KeepAlive>
      <component :is="current" />
    </KeepAlive>
  </div>
</template>

<style scoped>
/* block (NO flex column): así la vista interna, que centra con max-width +
 * margin auto, llena el ancho como bloque; en flex column se encogía al contenido. */
.explore { display: block; }
/* selector de fuente: dos cajas grandes y diferenciadas (no una pestaña sutil);
 * mismo ancho/centrado que la vista de abajo para que quede alineado */
.srcpick {
  display: flex; gap: var(--s-3); flex-wrap: wrap;
  max-width: var(--content-max); margin: 0 auto;
  padding: var(--s-5) var(--s-6) 0;
}
.srcpick__opt {
  display: flex; align-items: center; gap: var(--s-3);
  padding: var(--s-3) var(--s-4); border-radius: var(--r-md);
  border: 1px solid var(--line); background: var(--surface);
  color: var(--ink-soft); cursor: pointer; min-width: 13rem;
  transition: border-color var(--t-fast), background var(--t-fast), color var(--t-fast);
}
.srcpick__opt:hover { color: var(--ink); border-color: var(--line-strong); }
.srcpick__opt.is-on { color: var(--ink); border-color: var(--azure); background: var(--azure-haze); }
.srcpick__opt.is-on .srcpick__ic { color: var(--azure-bright); }
.srcpick__ic { flex: none; }
.srcpick__txt { display: flex; flex-direction: column; line-height: 1.2; text-align: left; }
.srcpick__txt b { font-size: var(--fs-sm); font-weight: 600; }
.srcpick__txt small { font-size: var(--fs-2xs); color: var(--ink-faint); }
@media (max-width: 640px) {
  .srcpick { padding: var(--s-4) var(--s-4) 0; }
  .srcpick__opt { min-width: 0; flex: 1; }
}
</style>
