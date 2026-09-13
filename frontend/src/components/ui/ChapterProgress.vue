<script setup>
/* Progreso de UNA tarea de descarga de capítulo, en vivo.
 *
 * Existe porque el progreso se pintaba SÓLO en el modal de MangaDex: bajando desde una fuente
 * (Mihon/Suwayomi) salía una ruedecita mientras volvía el POST —o sea, un segundo— y después
 * nada, aunque el backend publicaba `progress`/`total` igual de bien por las dos vías.
 *
 * Recibe el `task_id` que DEVUELVE el POST, no uno reconstruido a mano: el backend normaliza el
 * número de capítulo (`normalize_chapter`) y una fuente puede llamarlo "Chapter 012", que no
 * casaría nunca con el id armado en el cliente. */
import { computed } from 'vue'
import { useMangaStore } from '@/stores/manga'
import { tareaActiva } from '@/lib/manga'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  taskId: { type: String, default: '' },
  // Estado inyectado a mano. Sólo lo usa la Cocina del diseño: el mapa `downloads` lo reescribe
  // el SSE entero cada 500 ms, así que una muestra metida ahí desaparecería sola.
  task: { type: Object, default: null },
})
const manga = useMangaStore()

const t = computed(() => props.task || tareaActiva(manga.downloads, props.taskId))
const pct = computed(() => {
  const v = t.value
  return v && v.total > 0 ? Math.round((v.progress || 0) / v.total * 100) : 0
})
</script>

<template>
  <div v-if="t" class="dlp">
    <Spinner :size="13" />
    <span class="dlp__n">
      <template v-if="t.total > 0">{{ t.progress || 0 }}/{{ t.total }}</template>
      <template v-else>{{ t.status === 'starting' ? 'Empezando…' : 'Descargando…' }}</template>
    </span>
    <span v-if="t.total > 0" class="dlp__bar"><i :style="{ width: pct + '%' }" /></span>
  </div>
</template>

<style scoped>
.dlp { display: inline-flex; align-items: center; gap: var(--s-2); flex-shrink: 0; }
.dlp__n { font-size: var(--fs-xs); color: var(--ink-soft); font-variant-numeric: tabular-nums; }
.dlp__bar { display: block; width: 3.5rem; height: 3px; border-radius: 2px; background: var(--line-2); overflow: hidden; }
.dlp__bar i { display: block; height: 100%; background: var(--azure-bright); transition: width var(--t-fast); }
</style>
