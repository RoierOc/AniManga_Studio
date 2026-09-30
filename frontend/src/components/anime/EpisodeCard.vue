<script setup>
import { computed, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { useAnimeUpscaleStore } from '@/stores/animeUpscale'
import { animeEpLabel, animeEpisodeKey, isEpisodePlayable } from '@/lib/anime'
import {
  imgProxy, animeThumb, wasAnimeThumbLoaded, wasAnimeThumbFailed,
  rememberAnimeThumbLoaded, rememberAnimeThumbFailed,
} from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EpisodeMediaInfo from './EpisodeMediaInfo.vue'

const props = defineProps({
  anime: { type: Object, required: true },
  ep: { type: Object, required: true },
  batch: { type: Object, required: true },
})
const store = useAnimeStore()
const a4k = useAnimeUpscaleStore()

// Escalado con Anime4K: `ep.a4k` lo pone el escaneo cuando existe el fichero horneado, y entonces
// `ep.path` YA apunta a él (es el episodio principal) y `ep.original_path` guarda el de siempre.
// ⚠️ La API llama `local_path` a la ruta del fichero, no `path`.
const rutaOriginal = computed(() => props.ep.original_path || props.ep.local_path || '')
const horneando = computed(() => a4k.trabajoDe(rutaOriginal.value))

const playable = computed(() => isEpisodePlayable(props.ep, props.batch))
const primaryLabel = computed(() => {
  const action = downloading.value ? 'Descargando' : playable.value ? 'Reproducir' : 'Buscar torrent para'
  return `${action} ${props.anime.title || 'anime'}, episodio ${props.ep.num}`
})
const downloading = computed(() => {
  if (props.ep.file_index !== undefined && props.ep.file_index !== null) {
    return !!props.ep.in_qbt && props.ep.progress < 100
  }
  return (props.ep.in_qbt && props.ep.progress < 100 && !props.batch.hasBatch) ||
    (!props.batch.hasSelection && props.ep.num > 0 && props.batch.hasBatch && !props.batch.batchDone)
})
const dlPct = computed(() => {
  if (props.ep.file_index !== undefined && props.ep.file_index !== null) return props.ep.progress
  return props.batch.hasBatch ? (props.batch.batchEp?.progress || 0) : props.ep.progress
})
const resumePct = computed(() => {
  if (props.ep.watched || !props.ep.resume_pos || !props.ep.duration || downloading.value) return 0
  return Math.min(100, (props.ep.resume_pos / props.ep.duration) * 100)
})

// Metadatos del episodio (TMDB o MAL de reserva) cargados en bloque al abrir el detalle.
const meta = computed(() => store.epMeta[props.anime.id]?.[animeEpisodeKey(props.ep)] || store.epMeta[props.anime.id]?.[props.ep.num] || null)
const failedLocalThumb = ref('')
const infoOpen = computed(() => store.epInfoOpen === `${props.anime.id}_${animeEpisodeKey(props.ep)}`)
const jikanInfo = computed(() => store.epInfo[`${props.anime.mal_id}_${animeEpisodeKey(props.ep)}`])
// Descripción: la de TMDB si la hay; si no, la sinopsis de MAL (epInfo).
const info = computed(() => {
  if (meta.value?.overview) return { title: meta.value.title, synopsis: meta.value.overview, aired: meta.value.aired }
  return jikanInfo.value
})

const subKey = computed(() => store.subKey(props.anime, props.ep))
const subTask = computed(() => store.subTasks[subKey.value])
const subRunning = computed(() => subTask.value && ['starting', 'injecting', 'translating', 'downloading', 'running'].includes(subTask.value.status))
const subFetching = computed(() => store.subFetching === subKey.value)
// ¿Ya tiene subs en español inyectados por nosotros? Persistente (backend ep.es_injected,
// marcador/sidecar en disco) o recién hecho en esta sesión. No cuenta ES ya incrustados de origen.
const hasES = computed(() => props.ep.es_injected || subTask.value?.status === 'done')

/* Prefiere el fotograma del vídeo; si no puede cargarse, usa el still individual de TMDB.
 * `failedLocalThumb` evita reintentar la misma URL corrupta hasta que cambie su revisión. */
const localThumbSrc = computed(() => playable.value || props.ep.has_thumb
  ? animeThumb(props.anime.id, props.ep.num, props.ep.ep_type, animeEpisodeKey(props.ep))
  : '')
const thumbSrc = computed(() => {
  const local = localThumbSrc.value
  if (local && failedLocalThumb.value !== local && !wasAnimeThumbFailed(local)) return local
  // 400 se quedaba corto: la tarjeta mide ~495 px CSS, así que pedía el peldaño 640 y lo pintaba
  // a 742 físicos. Con el ancho real cae en el 900 y deja de verse blando.
  return meta.value?.still ? imgProxy(meta.value.still, 500, props.anime.cover_rev) : ''
})
function onThumbLoad(e) {
  rememberAnimeThumbLoaded(thumbSrc.value)
  e.target.classList.add('is-loaded')
}
function onThumbError(e) {
  if (localThumbSrc.value && thumbSrc.value === localThumbSrc.value) {
    failedLocalThumb.value = localThumbSrc.value
    rememberAnimeThumbFailed(localThumbSrc.value)
    return
  }
  e.target.style.display = 'none'
}

// Título real del episodio (TMDB/MAL) si lo tenemos; si no, la etiqueta derivada del archivo.
const epTitle = computed(() => {
  if (props.ep.ep_type !== 'special' && meta.value?.title) return meta.value.title
  return animeEpLabel(props.anime, props.ep)
})

// Línea de estado bajo la miniatura (solo cuando aporta algo).
const metaLine = computed(() => {
  if (downloading.value) return { text: `↓ Descargando ${Math.round(dlPct.value)}%`, cls: '' }
  if (!playable.value) return { text: 'Sin descargar', cls: 'ep__meta--muted' }
  // El horneado tarda ~10 min: si sólo se viera al abrir el clic derecho, parecería que no pasa nada.
  if (horneando.value) {
    const t = horneando.value
    if (t.estado === 'horneando') return { text: `✦ Escalando ${Math.round(t.porcentaje || 0)}%`, cls: 'ep__meta--resume' }
    return { text: t.estado === 'esperando' ? '✦ Esperando la GPU' : '✦ En cola', cls: 'ep__meta--muted' }
  }
  if (props.ep.a4k) return { text: 'Anime4K ✦ 2880p', cls: 'ep__meta--done' }
  if (resumePct.value) return { text: `Reanudar · ${Math.round(resumePct.value)}%`, cls: 'ep__meta--resume' }
  if (hasES.value) return { text: 'Subtítulos ES ✓', cls: 'ep__meta--done' }
  return null
})

// Click izquierdo = acción primaria (como YouTube): reproducir si se puede, si no buscar torrent.
function onPrimary() {
  if (playable.value) store.play(props.anime, props.ep)
  else if (!downloading.value) store.openTorrents(props.anime, props.ep.num)
}

// Todas las acciones viven en el clic derecho (menú contextual) — la tarjeta queda limpia.
const cm = ref({ open: false, x: 0, y: 0 })
const mediaInfoOpen = ref(false)
const menuItems = computed(() => {
  const a = props.anime, e = props.ep
  const items = []
  if (playable.value) {
    items.push({ label: 'Reproducir', icon: 'play', action: () => store.play(a, e) })
    items.push({ label: 'Información del archivo', icon: 'film', action: () => { mediaInfoOpen.value = true } })
    // Subtítulos en español (estado según la tarea)
    if (subFetching.value) items.push({ label: 'Buscando subtítulos…', icon: 'globe', disabled: true })
    else if (subRunning.value) items.push({ label: `Traduciendo… ${subTask.value?.progress || 0}%`, icon: 'close', danger: true, action: () => store.cancelTranslate(a, e) })
    else if (hasES.value) {
      items.push({ label: 'Subtítulos en español ✓', icon: 'check', disabled: true })
      items.push({ label: 'Cambiar / re-inyectar ES', icon: 'globe', action: () => store.translateSubs(a, e) })
    }
    else items.push({ label: 'Subtítulos en español', icon: 'globe', action: () => store.translateSubs(a, e) })
    if (a.mal_id || meta.value?.overview) items.push({ label: infoOpen.value ? 'Ocultar descripción' : 'Descripción', icon: 'menu', action: () => store.loadEpInfo(a, e) })
    // Escalar con Anime4K. Sólo para episodios que están en disco: hornear necesita el fichero.
    if (e.in_local && rutaOriginal.value) {
      items.push({ sep: true })
      if (horneando.value) {
        const t = horneando.value
        const txt = t.estado === 'horneando'
          ? `Escalando… ${Math.round(t.porcentaje || 0)}%${t.quedan ? ` · faltan ${Math.ceil(t.quedan / 60)} min` : ''}`
          : (t.estado === 'esperando' ? 'Esperando a la GPU…' : 'En cola para escalar')
        items.push({ label: txt, icon: 'close', danger: true, action: () => a4k.cancelar(t.id) })
      } else if (e.a4k) {
        items.push({ label: 'Escalado con Anime4K ✓', icon: 'check', disabled: true })
        items.push({ label: 'Volver al original (borra el escalado)', icon: 'close', danger: true, action: () => a4k.descartar(rutaOriginal.value) })
      } else if (a4k.disponible) {
        // Una entrada por calidad, con su tiempo, en vez de un diálogo: elegir calidad ES la
        // decisión, y meterla detrás de un modal la convierte en dos pasos para lo mismo.
        // El tiempo va en la etiqueta porque es justo el dato con el que se elige.
        for (const c of a4k.calidades) {
          items.push({
            label: `Escalar · ${c.etiqueta} (~${a4k.minutos(c, e.duration)} min)`,
            icon: 'spark',
            action: () => a4k.hornear(rutaOriginal.value, c.id),
          })
        }
      } else {
        items.push({ label: a4k.motivo || 'Escalado no disponible', icon: 'spark', disabled: true })
      }
    }
    items.push({ sep: true })
    items.push({ label: e.watched ? 'Marcar como no visto' : 'Marcar como visto', icon: 'check', action: () => store.toggleWatched(a, e) })
    if (e.in_local) items.push({ label: 'Cambiar tipo de episodio', icon: 'menu', action: (ev) => store.openEpOverrideMenu(cm.value.evt, a, e) })
    if (!e.in_local) items.push({ label: 'Borrar episodio', icon: 'close', danger: true, action: () => store.deleteEpisode(a, e) })
  } else if (!downloading.value) {
    items.push({ label: 'Buscar torrent', icon: 'search', action: () => store.openTorrents(a, e.num) })
  }
  return items
})
function openMenu(ev) {
  if (!menuItems.value.length) return
  cm.value = { open: true, x: ev.clientX, y: ev.clientY, evt: ev }
}
</script>

<template>
  <div class="ep" :class="{ 'ep--watched': ep.watched, 'ep--dl': downloading, 'ep--missing': !playable && !downloading }"
       @mouseenter.once="store.loadSkip(anime, ep)" @contextmenu.prevent="openMenu">

    <button type="button" class="ep__thumb" :aria-label="primaryLabel" :disabled="downloading" @click="onPrimary">
      <div class="ep__bg" :style="anime.cover ? `background-image:url('${imgProxy(anime.cover, 200)}')` : ''" />
      <img v-if="thumbSrc" class="ep__img" :class="{ 'is-loaded': wasAnimeThumbLoaded(thumbSrc) }" :src="thumbSrc"
           loading="lazy" decoding="async" @load="onThumbLoad" @error="onThumbError" alt="" />

      <!-- Sobre la portada se queda SÓLO el nº (y el "Visto"). El título vive debajo. -->
      <div class="ep__overlay">
        <div class="ep__num">
          <span class="ep__num-k">
            {{ ep.ep_type === 'special' ? 'SP' : 'EP' }}<span v-if="ep.watched" class="ep__num-seen"> · Visto</span>
          </span>
          <span class="ep__num-v">{{ String(ep.num).padStart(2, '0') }}</span>
        </div>
      </div>

      <!-- resume progress bar (partially watched) — como YouTube -->
      <div v-if="resumePct" class="ep__resumebar"><span :style="{ '--progress': resumePct / 100 }" /></div>
      <!-- downloading -->
      <div v-if="downloading" class="ep__dlbar"><span :style="{ '--progress': dlPct / 100 }" /></div>

      <div v-if="playable" class="ep__play"><span class="ep__play-c"><Icon name="play" :size="26" /></span></div>
      <span v-if="subFetching" class="ep__subtag ep__subtag--load"><Spinner :size="10" tone="light" /> Buscando ES…</span>
      <span v-else-if="subRunning" class="ep__subtag">✨ {{ subTask?.progress || 0 }}%</span>
    </button>

    <!-- Título BAJO la miniatura (Crunchyroll/Netflix): misma tipografía, ahora sin competir
         con el arte de la portada. El nº se queda arriba sobre la imagen. -->
    <div class="ep__foot">
      <div class="ep__eptitle" :data-tip="epTitle">{{ epTitle }}</div>
      <div v-if="metaLine" class="ep__meta" :class="metaLine.cls">{{ metaLine.text }}</div>
    </div>

    <!-- expandable episode info (desde el menú → Descripción) -->
    <Transition name="info">
      <div v-if="infoOpen" class="ep__info">
        <div v-if="info === null" class="ep__info-load"><span class="dot" /><span class="dot" /><span class="dot" /></div>
        <template v-else-if="info?.synopsis">
          <div v-if="info.title" class="ep__info-title">{{ info.title }}</div>
          <p class="ep__info-syn">{{ info.synopsis }}</p>
          <div class="ep__info-meta">
            <span>{{ info.aired }}</span>
            <span v-if="info.filler" class="ep__badge ep__badge--filler">Filler</span>
            <span v-if="info.recap" class="ep__badge ep__badge--recap">Recap</span>
          </div>
        </template>
        <div v-else class="ep__info-empty">Sin descripción disponible</div>
      </div>
    </Transition>

    <ContextMenu v-model:open="cm.open" :x="cm.x" :y="cm.y" :items="menuItems" />
    <EpisodeMediaInfo v-if="mediaInfoOpen" :anime-id="anime.id" :episode-key="animeEpisodeKey(ep)"
                      :episode-title="epTitle" @close="mediaInfoOpen = false" />
  </div>
</template>

<style scoped>
/* Tarjeta "al aire" estilo YouTube: sin marco ni fondo, la miniatura es el objeto. */
.ep { position: relative; transition: transform var(--t-base) var(--ease-snap); }
.ep:hover { transform: translateY(-3px); }

.ep__thumb { position: relative; display: block; width: 100%; padding: 0; color: inherit; font: inherit; text-align: left;
  aspect-ratio: 16 / 9; cursor: pointer; overflow: hidden;
  border-radius: var(--r-md); background: var(--surface-2); border: 1px solid var(--line);
  transition: border-color var(--t-base) var(--ease-silk), box-shadow var(--t-base); }
.ep__thumb:focus-visible { outline: 2px solid var(--azure-bright); outline-offset: 3px; }
.ep__thumb:disabled { cursor: default; }
.ep:hover .ep__thumb { border-color: var(--line-strong); box-shadow: var(--shadow-md); }
/* Degradado más corto que antes: ya sólo tiene que dar contraste al nº, no a un título de 2
   líneas. Así se ve más portada. */
.ep__thumb::after { content: ''; position: absolute; inset: 0; z-index: 1; pointer-events: none; background: linear-gradient(0deg, rgba(5,7,13,.88) 0%, rgba(5,7,13,.34) 20%, transparent 44%); }
.ep--dl .ep__thumb { cursor: default; }
.ep__bg { position: absolute; inset: 0; background-size: cover; background-position: center; filter: blur(18px) brightness(.4); transform: scale(1.2); }
.ep__img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk), filter var(--t-base) var(--ease-silk); }
.ep__img.is-loaded { opacity: 1; }
.ep:hover .ep__img { transform: scale(1.06); }

/* Visto = portada atenuada (nítida pero apagada), sin blur. Al pasar el ratón recupera vida. */
.ep--watched .ep__img { filter: brightness(.45) saturate(.55); }
.ep--watched:hover .ep__img { filter: brightness(.85) saturate(.9); }

/* Sobre la portada queda sólo el nº (el título se fue debajo, estilo Crunchyroll). */
.ep__overlay { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; display: flex; align-items: flex-end;
  padding: var(--s-3); color: #fff; pointer-events: none; }
.ep__num { flex-shrink: 0; display: flex; flex-direction: column; align-items: flex-start; line-height: 1; }
.ep__num-k { font-family: var(--font-mono); font-size: 0.5625rem; font-weight: 600; color: var(--ice); letter-spacing: .14em;
  line-height: 1; text-shadow: 0 1px 6px rgba(0,0,0,.9); }
.ep__num-seen { color: var(--azure-bright); letter-spacing: .1em; }
.ep__num-v { font-family: var(--font-display); font-weight: 700; font-size: 2.3rem; line-height: .9;
  text-shadow: 0 2px 16px rgba(0,0,0,.95), 0 0 40px rgba(0,0,0,.6); }
.ep--watched .ep__num-v { opacity: .82; }

.ep__resumebar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; height: 3px; background: rgba(0,0,0,.4); }
.ep__resumebar span { position: absolute; left: 0; top: 0; bottom: 0; background: var(--azure-bright); box-shadow: 0 0 8px var(--azure-glow); width: 100%; transform: scaleX(var(--progress, 0)); transform-origin: left; transition: transform .6s var(--ease-silk); }
.ep__dlbar { position: absolute; left: 0; right: 0; bottom: 0; z-index: 3; height: 3px; background: rgba(7,10,18,.5); }
.ep__dlbar span { position: absolute; left: 0; top: 0; bottom: 0; background: linear-gradient(90deg, var(--cyan), var(--azure)); box-shadow: 0 0 8px var(--azure-glow); width: 100%; transform: scaleX(var(--progress, 0)); transform-origin: left; transition: transform .5s var(--ease-silk); }

.ep__play { position: absolute; inset: 0; z-index: 4; display: grid; place-items: center; opacity: 0; transition: opacity var(--t-base); }
.ep__play-c { display: grid; place-items: center; width: 3rem; height: 3rem; border-radius: 50%; color: #fff;
  background: rgba(7,10,18,.55); border: 1px solid rgba(255,255,255,.35); backdrop-filter: blur(8px);
  transform: scale(.8); transition: transform var(--t-base) var(--ease-snap), background var(--t-fast); }
.ep__play-c :deep(svg) { margin-left: 2px; }
.ep__thumb:hover .ep__play { opacity: 1; }
.ep__thumb:hover .ep__play-c { transform: scale(1); }
.ep__thumb:hover .ep__play-c:hover { background: var(--azure); border-color: transparent; }
.ep__subtag { position: absolute; top: var(--s-2); right: var(--s-2); z-index: 4; display: inline-flex; align-items: center; gap: 0.3125rem;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; color: #fff; padding: 2px 0.4375rem; border-radius: var(--r-pill);
  background: rgba(7,10,18,.65); backdrop-filter: blur(6px); }
.ep__subtag--load { color: var(--ice); }

/* Pie de la tarjeta: título + estado. Mismo font-display de siempre (al usuario le gusta la
   tipografía), pero ya sobre fondo sólido: sin sombras y con el color de tinta del tema. */
.ep__foot { padding: var(--s-2) var(--s-1) 0; }
.ep__eptitle { font-family: var(--font-display); font-weight: 600; font-size: var(--fs-sm); line-height: 1.3;
  color: var(--ink); transition: color var(--t-fast);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ep:hover .ep__eptitle { color: var(--azure-bright); }
.ep--watched .ep__eptitle { color: var(--ink-faint); }
.ep__meta { margin-top: 3px; font-size: var(--fs-2xs); font-family: var(--font-mono); color: var(--cyan); }
.ep__meta--muted { color: var(--ink-faint); }
.ep__meta--resume { color: var(--azure-bright); }
.ep__meta--done { color: var(--ink-faint); }

.ep__info { margin-top: var(--s-2); padding: var(--s-3); border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--base); font-size: var(--fs-xs); line-height: 1.55; }
.ep__info-title { font-weight: 600; color: var(--ink); margin-bottom: 4px; font-size: var(--fs-sm); }
.ep__info-syn { color: var(--ink-soft); }
.ep__info-meta { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-2); color: var(--ink-faint); }
.ep__badge { padding: 1px 0.375rem; border-radius: var(--r-xs); font-weight: 600; font-size: var(--fs-2xs); }
.ep__badge--filler { background: color-mix(in srgb, var(--gold) 18%, transparent); color: var(--gold); }
.ep__badge--recap { background: var(--azure-haze); color: var(--azure-bright); }
.ep__info-empty { color: var(--ink-faint); font-style: italic; }
.ep__info-load { display: flex; gap: 0.3125rem; justify-content: center; padding: var(--s-2); }
.ep__info-load .dot { width: 0.3125rem; height: 0.3125rem; border-radius: 50%; background: var(--azure); animation: pulse-live 1s var(--ease-drift) infinite; }
.ep__info-load .dot:nth-child(2) { animation-delay: .15s; }
.ep__info-load .dot:nth-child(3) { animation-delay: .3s; }

.info-enter-active, .info-leave-active { transition: all var(--t-base) var(--ease-silk); overflow: hidden; }
.info-enter-from, .info-leave-to { opacity: 0; max-height: 0; }
.info-enter-to, .info-leave-from { max-height: 18.75rem; }
</style>
