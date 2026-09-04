<script setup>
/* Tarjeta de póster COMPARTIDA por anime y series/películas.
 *
 * Es la parte puramente visual extraída de `AnimeCard.vue`: póster 2:3, blur-up, glow del color
 * dominante al hover, scrim, badges e info que se despliega dentro del póster. No sabe NADA de
 * dominio — ni store, ni AniList, ni Sonarr: todo entra por props. Así el diseño se pule en un
 * único sitio y anime y series no divergen (que es justo lo que pasaba antes).
 *
 * `AnimeCard.vue` y `SeriesCard`/`MediaHome` son envoltorios finos que traducen sus datos a estas
 * props.
 */
import { computed, ref } from 'vue'
import { imgProxy, imgThumb } from '@/lib/img'
import { useBoxWidth } from '@/lib/useBoxWidth'
import { coverRGB, vivid } from '@/lib/coverColor'
import { vtTag } from '@/lib/vt'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  cover: { type: String, default: '' },
  title: { type: String, required: true },
  // Etiqueta pequeña arriba a la izquierda (formato: TV, Película, Serie…).
  kindLabel: { type: String, default: '' },
  // Estado arriba a la derecha: { label, color }.
  status: { type: Object, default: null },
  // Aviso sobre el título: { tone: 'live'|'soft', icon?, label } — "NUEVO", cuenta atrás, etc.
  flag: { type: Object, default: null },
  // Progreso textual: { done, total }.
  count: { type: Object, default: null },
  // Puntitos de disponibilidad: 'done' | 'dl' | 'missing'.
  dots: { type: Array, default: () => [] },
  // Etiquetas (géneros) que aparecen al desplegar.
  tags: { type: Array, default: () => [] },
  playLabel: { type: String, default: 'Ver' },
  // En Buscar/Descubrir la acción no es reproducir sino añadir: el icono lo dice ('plus').
  playIcon: { type: String, default: 'play' },
  // Estado de la acción principal, como el botón de anime: jade cuando ya está hecho (y sin
  // volver a ser pulsable — que sí lo fuera era una invitación a duplicar el alta).
  playDone: { type: Boolean, default: false },
  playBusy: { type: Boolean, default: false },
  // Segunda acción del hover. Cada vista pone su verbo: en la biblioteca "Torrents", en
  // Buscar/Descubrir "Info". Si `altLabel` es '', solo se pinta el botón principal.
  altLabel: { type: String, default: 'Info' },
  altIcon: { type: String, default: 'spark' },
  // Las rejillas de biblioteca activan Ctrl/Cmd+clic y Shift+clic sin cambiar el clic normal.
  selectable: { type: Boolean, default: false },
})
const emit = defineEmits(['open', 'play', 'alt', 'select'])

function openCard(ev) {
  if (props.selectable && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) {
    ev.preventDefault()
    emit('select', ev)
    return
  }
  // El póster clickeado "vuela" hasta el hero del detalle (View Transition).
  vtTag(ev.currentTarget?.closest?.('.mcard') || ev.currentTarget, '.mcard__img')
  emit('open')
}

// Glow del color dominante de la portada al hover (perezoso + cacheado). Solo sombra/borde:
// nunca se tiñe texto ni controles (ver [[project_cover_ambient]]).
const glow = ref('')
async function ensureGlow() {
  if (glow.value || !props.cover) return
  // Del micro-thumb de 28 px, no de la portada entera: `coverRGB` reduce a 10×10 para
  // promediar, así que bajar 91 KB para eso era tirar 90. Y es la MISMA url que ya usa el
  // blur-up → sale de la caché del navegador, sin una sola petición extra.
  const rgb = await coverRGB(imgThumb(props.cover))
  if (rgb) { const v = vivid(rgb); glow.value = `${v.r}, ${v.g}, ${v.b}` }
}

const thumb = computed(() => imgThumb(props.cover))

// Ancho de la portada MEDIDO, no adivinado — ver `useBoxWidth`.
const [poster, boxW] = useBoxWidth()
const coverUrl = computed(() => imgProxy(props.cover, boxW.value))
</script>

<template>
  <article class="mcard" :class="{ 'has-glow': glow }" :style="glow ? { '--cardglow': glow } : {}"
           tabindex="0" @click="openCard" @keydown.enter="openCard" @mouseenter="ensureGlow" @focus="ensureGlow">
    <div class="mcard__poster" ref="poster">
      <img v-if="thumb" :src="thumb" class="blurup" aria-hidden="true" alt="" />
      <img v-if="cover" :src="coverUrl" :alt="title" loading="lazy" decoding="async" class="mcard__img"
           @load="$event.target.classList.add('is-loaded')" />
      <div v-else class="mcard__ph">{{ (title || '?')[0].toUpperCase() }}</div>
      <div class="mcard__scrim" />
      <span class="mcard__shine" />

      <span v-if="kindLabel" class="mcard__fmt">{{ kindLabel }}</span>
      <span v-if="status" class="mcard__status" :style="{ '--c': status.color }">{{ status.label }}</span>

      <!-- Título e info se revelan al hover DENTRO del póster: sin reflow ni solape con vecinas -->
      <div class="mcard__overlay">
        <span v-if="flag" class="mcard__flag" :class="`mcard__flag--${flag.tone || 'soft'}`">
          <span v-if="flag.tone === 'live'" class="mcard__flagdot" />
          <Icon v-else-if="flag.icon" :name="flag.icon" :size="11" />
          {{ flag.label }}
        </span>
        <h3 class="mcard__title">{{ title }}</h3>
        <div v-if="count || dots.length" class="mcard__bottom">
          <span v-if="count" class="mcard__eps">
            <span class="mcard__count">{{ count.done }}</span>
            <span v-if="count.total" class="mcard__total"> / {{ count.total }}</span>
          </span>
          <div class="mcard__dots">
            <span v-for="(d, i) in dots" :key="i" class="md" :class="`md--${d}`" />
          </div>
        </div>

        <!-- Hover-expand: crece hacia arriba dentro del póster (overflow:hidden lo recorta) -->
        <div class="mcard__extra">
          <div v-if="tags.length" class="mcard__tags">
            <span v-for="t in tags" :key="t" class="mcard__t">{{ t }}</span>
          </div>
          <div class="mcard__acts">
            <button class="mcard__act mcard__act--play" :class="{ 'is-done': playDone, 'is-busy': playBusy }"
                    :disabled="playDone || playBusy" @click.stop="emit('play')">
              <Icon :name="playIcon" :size="14" /> {{ playLabel }}
            </button>
            <button v-if="altLabel" class="mcard__act" @click.stop="emit('alt')">
              <Icon :name="altIcon" :size="13" /> {{ altLabel }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.mcard { position: relative; cursor: pointer; outline: none; transition: transform var(--t-base) var(--ease-snap); }
.mcard:hover, .mcard:focus-visible, .mcard:focus-within { transform: translateY(-6px); z-index: 6; }

.mcard__poster {
  position: relative; aspect-ratio: 2 / 3; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface-2); border: 1px solid var(--line); box-shadow: var(--shadow-sm);
  transition: box-shadow var(--t-base) var(--ease-silk), border-color var(--t-base);
}
.mcard:hover .mcard__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.mcard.has-glow:hover .mcard__poster, .mcard.has-glow:focus-visible .mcard__poster {
  border-color: rgba(var(--cardglow), .55);
  box-shadow: var(--shadow-lg), 0 0 0 1px rgba(var(--cardglow), .55), 0 8px 34px rgba(var(--cardglow), .45);
}

.mcard__img { width: 100%; height: 100%; object-fit: cover; opacity: 0;
  transition: opacity var(--t-slow), transform var(--t-cine) var(--ease-silk), filter var(--t-base) var(--ease-silk); }
.mcard__img.is-loaded { opacity: 1; }
.mcard__ph { display: grid; place-items: center; height: 100%; font-size: 2rem; color: var(--ink-soft); }
/* Al expandir la info, el póster se difumina y oscurece: el texto queda legible sobre CUALQUIER
   portada, no solo las oscuras. */
.mcard:hover .mcard__img, .mcard:focus-within .mcard__img {
  transform: scale(1.07); filter: blur(9px) brightness(.45) saturate(1.15); }
.mcard__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.4) 0%, transparent 26%, transparent 48%, rgba(5,7,13,.95) 100%); }
.mcard__shine {
  position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(112deg, transparent 35%, rgba(168,200,255,.14) 48%, transparent 60%);
  transform: translateX(-120%);
}
.mcard:hover .mcard__shine { animation: shine .8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }

.mcard__fmt {
  position: absolute; top: var(--s-2); left: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600;
  padding: 2px 0.4375rem; border-radius: var(--r-xs); color: var(--ink);
  background: rgba(7,10,18,.6); backdrop-filter: blur(6px); letter-spacing: .03em;
}
.mcard__status {
  position: absolute; top: var(--s-2); right: var(--s-2);
  font-size: var(--fs-2xs); font-weight: 600; padding: 2px 0.5rem; border-radius: var(--r-pill);
  color: var(--c); background: color-mix(in srgb, var(--c) 16%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 40%, transparent); backdrop-filter: blur(6px);
}

.mcard__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3);
  background: linear-gradient(180deg, transparent 0%, rgba(5,7,13,.5) 40%, rgba(5,7,13,.95) 100%); }
.mcard__flag {
  display: inline-flex; align-items: center; gap: 4px; margin-bottom: 0.3125rem;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .03em;
  padding: 2px 0.5rem; border-radius: var(--r-pill); backdrop-filter: blur(6px);
}
.mcard__flag--live { color: var(--jade); background: color-mix(in srgb, var(--jade) 20%, rgba(7,10,18,.6)); border: 1px solid color-mix(in srgb, var(--jade) 45%, transparent); }
.mcard__flagdot { width: 0.375rem; height: 0.375rem; border-radius: 50%; background: var(--jade); box-shadow: 0 0 6px var(--jade); animation: pulse-live 1.8s var(--ease-drift) infinite; }
.mcard__flag--soft { color: var(--ice); background: rgba(7,10,18,.6); border: 1px solid rgba(255,255,255,.16); }
.mcard__flag--soft :deep(svg) { color: var(--cyan); }
.mcard__title {
  font-family: var(--font-body); font-weight: 600; font-size: var(--fs-sm); line-height: var(--lh-snug); color: #fff;
  text-shadow: 0 1px 6px rgba(0,0,0,.65);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.mcard__bottom { display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-top: 0.375rem; }
.mcard__eps { font-size: var(--fs-xs); white-space: nowrap; text-shadow: 0 1px 4px rgba(0,0,0,.6); }
.mcard__count { color: var(--azure-bright); font-weight: 700; }
.mcard__total { color: var(--ink-soft); opacity: .7; }
.mcard__dots { display: flex; flex-wrap: wrap; gap: 3px; justify-content: flex-end; }
.md { width: 0.3125rem; height: 0.3125rem; border-radius: 50%; background: rgba(255,255,255,.16); }
.md--done    { background: var(--azure-bright); }
.md--dl      { background: var(--cyan); animation: pulse-live 1.6s var(--ease-drift) infinite; }
.md--missing { background: rgba(255,255,255,.14); }

.mcard__extra {
  max-height: 0; opacity: 0; overflow: hidden;
  transition: max-height var(--t-base) var(--ease-silk), opacity var(--t-base) var(--ease-silk), margin-top var(--t-base) var(--ease-silk);
}
.mcard:hover .mcard__extra, .mcard:focus-within .mcard__extra { max-height: 8rem; opacity: 1; margin-top: var(--s-2); }
.mcard__tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: var(--s-3); }
.mcard__t { font-size: var(--fs-2xs); padding: 2px 0.5rem; border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--azure) 28%, rgba(7,10,18,.5)); color: var(--ice); backdrop-filter: blur(4px); }
.mcard__acts { display: flex; gap: var(--s-2); }
.mcard__act { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 0.3125rem;
  padding: var(--s-2); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600;
  color: #fff; border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.08);
  backdrop-filter: blur(6px); transition: all var(--t-fast); }
.mcard__act:hover { border-color: rgba(255,255,255,.5); background: rgba(255,255,255,.16); }
.mcard__act--play { background: var(--azure); border-color: transparent; }
.mcard__act--play:hover:not(:disabled) { background: var(--azure-bright); box-shadow: var(--glow-azure); }
/* Mismo jade que el "En Mi Anime" de AnimeDetail: el estado se reconoce por color, no por texto. */
.mcard__act--play.is-done { background: color-mix(in srgb, var(--jade) 16%, transparent);
  border-color: color-mix(in srgb, var(--jade) 45%, transparent); color: var(--jade); cursor: default; }
.mcard__act--play.is-busy { opacity: .7; cursor: progress; }
.mcard__act--play.is-busy :deep(svg) { animation: mcard-spin 1s linear infinite; }
@keyframes mcard-spin { to { transform: rotate(360deg); } }
</style>
