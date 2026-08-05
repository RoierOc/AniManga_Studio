<script setup>
/* Hero de Mi Anime — ENVOLTORIO de `components/media/MediaHero.vue`.
 *
 * Antes era una copia de 365 líneas con el mismo markup y el mismo CSS que MediaHero (misma
 * historia que MangaCard/MediaCard): cada retoque visual había que hacerlo dos veces y las dos
 * versiones divergieron. Aquí queda SÓLO lo que es dominio de anime — qué texto lleva cada tipo de
 * destacado, qué hace el botón según se pueda reproducir o haya que buscar torrents — y el pintado
 * es el compartido. La API pública no cambia: `bleed` + evento `tint`.
 */
import { computed, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { animeFormatLabel, SEASON_ES } from '@/lib/anime'
import { relativeTime } from '@/lib/format'
import MediaHero from '@/components/media/MediaHero.vue'

defineProps({ bleed: { type: Boolean, default: false } })
const emit = defineEmits(['tint'])

const store = useAnimeStore()

const EYEBROW = {
  new: 'NUEVO EPISODIO', downloaded: 'LISTO PARA VER', continue: 'SIGUE VIENDO',
  seasonal: 'TEMPORADA', recommendation: 'RECOMENDADO',
}

function epLabel(it) {
  if (it.kind === 'seasonal' || it.kind === 'recommendation') {
    const na = store.nextAiring[it.anime.al_id || it.anime.id]
    return na ? `Ep ${na.episode} · Próximamente` : 'Próximamente'
  }
  if (it.kind === 'new') return `Capítulo nuevo · Ep ${it.ep?.num}`
  return `Episodio ${it.ep?.num}`
}

function seasonLabel(a) {
  if (a?.season && a?.season_year) return `${SEASON_ES[a.season] || a.season} ${a.season_year}`
  return a?.season_year ? String(a.season_year) : ''
}

function freshLabel(it) {
  if (it.kind === 'new' && it.aired_at) return `Emitido ${relativeTime(it.aired_at)}`
  if (it.kind === 'downloaded' && it.ts) return `Descargado ${relativeTime(it.ts)}`
  return ''
}

// «Se puede ver AHORA» = hay archivo descargado. Sin él, el primario busca en vez de reproducir:
// un botón de play que no reproduce es peor que no tenerlo.
function canPlay(it) { return !['seasonal', 'recommendation'].includes(it.kind) && it.hasFile }

function progressOf(it) {
  const ep = it.ep
  if (!ep?.resume_pos || !ep?.duration) return 0
  return Math.min(100, (ep.resume_pos / ep.duration) * 100)
}

function actionsFor(it) {
  const play = canPlay(it)
  const isNew = it.kind === 'new'
  const isRec = it.kind === 'recommendation'
  const isFuture = it.kind === 'seasonal' || isRec
  return [
    {
      primary: true,
      icon: play ? 'play' : (isRec ? 'spark' : 'search'),
      label: play ? (progressOf(it) ? 'Reanudar' : 'Ver episodio')
        : isRec ? 'Información' : isNew ? `Buscar Ep ${it.ep?.num}` : 'Buscar torrents',
      run: () => {
        if (play) store.play(it.anime, it.ep)
        else if (isRec) store.openPreview(it.anime)
        else store.openTorrents(it.anime, isNew ? it.ep?.num : null)
      },
    },
    {
      icon: 'spark',
      label: isFuture ? '+ Mi Anime' : 'Información',
      run: () => (isFuture ? store.addToLibrary(it.anime) : store.openDetail(it.anime)),
    },
  ]
}

const items = computed(() => store.heroItems.map((it) => {
  const a = it.anime || {}
  return {
    id: a.id,
    art: a.banner || '',                        // sólo arte ANCHO: si no hay, el hero difumina
    artFallback: [a.cover_xl, a.cover].filter(Boolean),
    logo: a.logo || '',
    overline: EYEBROW[it.kind] || '',
    title: a.title,
    meta: [
      epLabel(it),
      seasonLabel(a),
      animeFormatLabel(a.format),
      freshLabel(it) ? { text: freshLabel(it), chip: true } : null,
    ].filter(Boolean),
    tags: Array.isArray(a.genres) ? a.genres.slice(0, 4) : [],
    progress: progressOf(it),
    actions: actionsFor(it),
    /* Pinchar el título abre la ficha, igual que en Inicio. Mismo criterio que el botón
       secundario de `actionsFor`: para lo que aún NO está en tu biblioteca (temporada y
       recomendaciones) ese botón es «+ Mi Anime», no «Información» — así que ahí tampoco se
       ofrece el gesto, en vez de llevarte a una ficha de algo que no tienes. */
    titleAction: (it.kind === 'seasonal' || it.kind === 'recommendation')
      ? null : () => store.openDetail(a),
  }
}))

// Las recomendaciones llegan sin arte de TMDB: pedirlo al entrar, no al llegarles el turno en el
// carrusel (si no, la diapositiva aparece sin imagen y se rellena a la vista).
watch(() => store.heroItems, (list) => {
  list.forEach(it => { if (it.kind === 'recommendation' && it.anime?.al_id) store.enrichPreview(it.anime.al_id) })
}, { immediate: true })
</script>

<template>
  <MediaHero :items="items" :bleed="bleed" @tint="c => emit('tint', c)" />
</template>
