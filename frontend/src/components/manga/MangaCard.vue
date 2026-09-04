<script setup>
/* Tarjeta de manga = `MediaCard` (todo lo visual) + la traducción de dominio, igual que hizo
 * `AnimeCard.vue`. Antes duplicaba póster/glow/shine a mano y NO tenía acciones al hover ni el
 * despliegue de info: el dominio fundacional era el peor presentado. Con la primitiva compartida
 * hereda gratis todo lo que se pula ahí.
 *
 * API: props (`manga`, `updates`) + eventos `open`/`play` (Continuar). El click de tarjeta emite
 * `open` (antes era @click nativo → las vistas se actualizaron). */
import { computed } from 'vue'
import { MANGA_STATUS } from '@/lib/manga'
import { genero } from '@/lib/etiquetas'
import { useMangaStore } from '@/stores/manga'
import MediaCard from '@/components/media/MediaCard.vue'

const props = defineProps({
  manga: { type: Object, required: true },
  updates: { type: Number, default: 0 },
  selectable: { type: Boolean, default: false },
})
const emit = defineEmits(['open', 'play', 'select'])

const store = useMangaStore()

const status = computed(() => MANGA_STATUS[props.manga.status] || null)
const kind = computed(() => (props.manga.kind === 'novel' ? 'NOVELA' : 'MANGA'))
const flag = computed(() =>
  props.updates ? { tone: 'live', label: `+${props.updates} nuevos` } : null)

/* Leídos / total. La tarjeta enseñaba sólo el total de capítulos, que no dice nada accionable:
   lo que quieres saber de un vistazo es CUÁNTOS TE FALTAN. `MediaCard` ya pinta `done/total`
   igual que en anime, así que basta con darle los números correctos. */
const read = computed(() => store.readCountOf(props.manga.id))
const total = computed(() => props.manga.chapter_count || 0)
const pending = computed(() => Math.max(0, total.value - read.value))

/* Progreso primero (es lo accionable), géneros después: dicen QUÉ es la obra de un vistazo, que
   es justo lo que la tarjeta de manga no contaba y la de anime sí. Llegan de `/api/anilist/
   genres_by_id` (LibraryView), así que sin al_id la tarjeta se queda como estaba. */
const tags = computed(() => [
  pending.value ? `${pending.value} sin leer` : (total.value ? 'Al día' : ''),
  props.manga.upscaled ? `${props.manga.upscaled} en 4K` : '',
  ...(props.manga.genres || []).slice(0, 3).map(genero),
].filter(Boolean))
</script>

<template>
  <MediaCard
    :cover="manga.cover"
    :title="manga.name"
    :kind-label="kind"
    :status="status"
    :flag="flag"
    :count="total ? { done: read, total } : null"
    :tags="tags"
    :selectable="selectable"
    play-label="Continuar"
    alt-label=""
    @open="emit('open')"
    @play="emit('play')"
    @select="emit('select', $event)"
  />
</template>
