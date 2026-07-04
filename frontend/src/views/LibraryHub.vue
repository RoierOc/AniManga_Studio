<script setup>
/* Biblioteca: tu manga en disco, con pestañas Descargados (seguido/descargado)
 * y Locales (CBZ/CBR sueltos), más un botón Importar que abre el Taller. Reúne
 * lo que antes eran tres secciones (Biblioteca, Local, Taller) donde tiene
 * sentido: junto a tu contenido. KeepAlive conserva el estado de cada pestaña. */
import { ref, computed, defineAsyncComponent } from 'vue'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import LibraryView from '@/views/LibraryView.vue'          // eager: vista de aterrizaje
const LocalView = defineAsyncComponent(() => import('@/views/LocalView.vue'))

const ui = useUiStore()
const TAB_KEY = 'manga-library-tab'
const tab = ref(localStorage.getItem(TAB_KEY) === 'local' ? 'local' : 'downloaded')
function setTab(id) { tab.value = id; try { localStorage.setItem(TAB_KEY, id) } catch (_) {} }
const current = computed(() => (tab.value === 'local' ? LocalView : LibraryView))
</script>

<template>
  <div class="libhub">
    <div class="libhub__bar">
      <div class="libtabs" role="tablist">
        <button class="libtabs__t" :class="{ 'is-on': tab === 'downloaded' }"
                role="tab" :aria-selected="tab === 'downloaded'" @click="setTab('downloaded')">
          <Icon name="library" :size="16" /> Descargados
        </button>
        <button class="libtabs__t" :class="{ 'is-on': tab === 'local' }"
                role="tab" :aria-selected="tab === 'local'" @click="setTab('local')">
          <Icon name="folder" :size="16" /> Locales
        </button>
      </div>
      <div class="libhub__right">
        <button class="libhub__import" title="Importar un CBZ/CBR y trocearlo en capítulos" @click="ui.goto('workshop')">
          <Icon name="upload" :size="16" /> Importar
        </button>
      </div>
    </div>

    <KeepAlive>
      <component :is="current" />
    </KeepAlive>
  </div>
</template>

<style scoped>
/* block (NO flex column): la vista interna centra con max-width + margin auto y
 * así llena el ancho como bloque; en flex column se encogía al contenido. */
.libhub { display: block; }
.libhub__bar {
  display: flex; align-items: center; gap: var(--s-3);
  max-width: var(--content-max); margin: 0 auto;   /* alineado con la vista de abajo */
  padding: var(--s-5) var(--s-6) 0;
}
.libtabs { display: flex; gap: 2px; padding: 3px; border-radius: var(--r-md); background: var(--surface-2); }
.libtabs__t {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm);
  font-size: var(--fs-sm); font-weight: 500; color: var(--ink-soft); cursor: pointer;
  transition: color var(--t-fast), background var(--t-fast);
}
.libtabs__t:hover { color: var(--ink); }
.libtabs__t.is-on { color: var(--ink); background: var(--surface); box-shadow: var(--shadow-sm); }
.libtabs__t.is-on :deep(svg) { color: var(--azure-bright); }
.libhub__right { margin-left: auto; display: flex; align-items: center; gap: var(--s-2); }
.libhub__import {
  display: flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm);
  border: 1px solid var(--line); color: var(--ink-soft); cursor: pointer;
  transition: color var(--t-fast), border-color var(--t-fast);
}
.libhub__import:hover { color: var(--ink); border-color: var(--azure); }
@media (max-width: 640px) {
  .libhub__bar { padding: var(--s-4) var(--s-4) 0; }
}
</style>
