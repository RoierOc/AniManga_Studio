<script setup>
/* Tarjeta de anime = `MediaCard` (todo lo visual) + la lógica de dominio de anime.
 *
 * El pintado vive en `components/media/MediaCard.vue`, compartido con series/películas: así el
 * póster, el glow, los badges y el despliegue al hover se pulen UNA vez y las dos secciones de la
 * app no divergen. Aquí solo queda lo que es específicamente de anime: emisión (NUEVO / cuenta
 * atrás), episodios descargados y géneros.
 *
 * La API pública NO cambia: mismas props (`anime`) y mismos eventos (`open`, `play`).
 */
import { computed, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { ANIME_STATUS, animeFormatLabel, fmtCountdown } from '@/lib/anime'
import MediaCard from '@/components/media/MediaCard.vue'

const props = defineProps({ anime: { type: Object, required: true } })
const emit = defineEmits(['open', 'play'])

const store = useAnimeStore()

const total = computed(() => props.anime.total_episodes || 0)
const done = computed(() => props.anime.downloaded_count || 0)
const status = computed(() => ANIME_STATUS[props.anime.status] || null)
const genres = computed(() => (props.anime.genres || []).slice(0, 3))

// Airing awareness — usa el mismo snapshot que el hero (store.airing keyed por al_id).
const airInfo = computed(() => store.airing[props.anime.al_id] || null)

// "NUEVO": el último episodio emitido ya está descargado y sin ver → listo para verse ya.
const hasNew = computed(() => {
  const inf = airInfo.value
  if (!inf?.last_episode) return false
  return (props.anime.episodes || []).some(e => e.num === inf.last_episode
    && (e.in_local || (e.in_qbt && e.progress >= 100)) && !e.watched)
})

// Cuenta regresiva al próximo episodio (series en emisión) — "Ep 5 · en 2d 3h".
const countdown = computed(() => {
  const inf = airInfo.value
  if (!inf?.next_airing_at || !inf?.next_episode) return null
  const c = fmtCountdown(inf.next_airing_at, store.nowSec)
  return c ? { ...c, episode: inf.next_episode } : null
})
const cdLabel = computed(() => {
  const c = countdown.value
  if (!c) return ''
  if (c.d > 0) return `${c.d}d ${c.h}h`
  if (c.h > 0) return `${c.h}h ${c.m}m`
  return `${c.m} min`
})

// "NUEVO" (episodio listo) tiene prioridad sobre la cuenta regresiva.
const flag = computed(() => {
  if (hasNew.value) return { tone: 'live', label: 'NUEVO' }
  if (countdown.value) return { tone: 'soft', icon: 'clock', label: `Ep ${countdown.value.episode} · ${cdLabel.value}` }
  return null
})

// Disponibilidad de los 12 primeros episodios: verde = descargado, cian = bajando, apagado = falta.
const dots = computed(() =>
  (props.anime.episodes || [])
    .filter(e => e.num > 0)
    .slice(0, 12)
    .map(e => {
      if (e.in_local || (e.in_qbt && e.progress >= 100)) return 'done'
      if (e.in_qbt && e.progress < 100) return 'dl'
      return 'missing'
    })
)
</script>

<template>
  <MediaCard
    :cover="anime.cover"
    :title="anime.title"
    :kind-label="animeFormatLabel(anime.format)"
    :status="status"
    :flag="flag"
    :count="{ done, total }"
    :dots="dots"
    :tags="genres"
    @open="emit('open', anime)"
    @play="emit('play', anime)"
  />
</template>
