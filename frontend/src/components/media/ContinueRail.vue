<script setup>
/* Riel "Seguir viendo" compartido por anime y series/películas.
 *
 * Estaba embebido dentro de `AnimeLibrary.vue` (bloque `.cw`), así que no se podía reutilizar.
 * Aquí es un componente tonto: cada item es `{ id, thumb, title, subtitle, badge, progress }` y
 * el consumidor decide qué hacer al pulsar (`play`) o al clic derecho (`menu`).
 */
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({
  title: { type: String, default: 'Seguir viendo' },
  items: { type: Array, default: () => [] },
  /* Un episodio tiene fotograma (16:9); un manga tiene PORTADA (2:3). No es un capricho: forzar
     una portada a 16:9 la recorta por la mitad. `poster` cambia la forma y el ancho de la tarjeta,
     y con eso el mismo riel sirve para las dos secciones. */
  poster: { type: Boolean, default: false },
  /* Rueda del ratón → scroll horizontal. APAGADO por defecto: secuestraba la rueda al pasar por
     encima del riel, así que bajar por la página con el ratón te empujaba de lado sin querer.
     El riel se recorre igual con su barra (`overflow-x: auto`, y la barra se ve: 10 px estilados
     en base.css) y con el gesto horizontal del trackpad, que nunca pasó por aquí. */
  wheelScroll: { type: Boolean, default: false },
})
const emit = defineEmits(['play', 'menu'])

function onWheel(e) {
  if (!props.wheelScroll) return
  const el = e.currentTarget
  if (el.scrollWidth <= el.clientWidth) return
  if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return   // deja pasar el gesto horizontal del trackpad
  el.scrollLeft += e.deltaY
  e.preventDefault()
}
</script>

<template>
  <section v-if="items.length" class="cw" :class="{ 'is-poster': poster }">
    <h3 class="cw__title">{{ title }}</h3>
    <div class="cw__row" @wheel="onWheel">
      <article v-for="it in items" :key="it.id" class="cwc"
               @click="emit('play', it)" @contextmenu.prevent="emit('menu', { ev: $event, item: it })">
        <div class="cwc__thumb">
          <img v-if="it.thumb" :src="it.thumb" :alt="it.title" loading="lazy" decoding="async" class="cwc__img"
               @load="$event.target.classList.add('is-loaded')"
               @error="$event.target.style.display = 'none'" />
          <div v-else class="cwc__ph">{{ (it.title || '?')[0].toUpperCase() }}</div>
          <div class="cwc__scrim" />
          <div class="cwc__play"><Icon name="play" :size="28" /></div>
          <span v-if="it.badge" class="cwc__ep">{{ it.badge }}</span>
          <div v-if="it.progress" class="cwc__bar"><span :style="{ width: Math.min(100, it.progress) + '%' }" /></div>
        </div>
        <div class="cwc__title">{{ it.title }}</div>
        <div class="cwc__epnum">{{ it.subtitle }}</div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.cw { margin-bottom: var(--s-8); }
.cw__title { font-family: var(--font-display); font-size: var(--fs-xl); margin-bottom: var(--s-4); }
.cw__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-3); }
.cwc { flex-shrink: 0; width: 20rem; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.cwc:hover { transform: translateY(-5px); }
.cwc__thumb { position: relative; aspect-ratio: 16/9; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface-2); border: 1px solid var(--line); }
.cwc:hover .cwc__thumb { border-color: var(--azure-glow); box-shadow: var(--shadow-lg); }
/* Modo póster (manga/novelas): tarjeta estrecha y 2:3, la forma de una portada. Forzar una
   portada a 16:9 la recortaría por la mitad. */
.is-poster .cwc { width: 8.5rem; }
.is-poster .cwc__thumb { aspect-ratio: 2/3; }
.cwc__thumb img { width: 100%; height: 100%; object-fit: cover; }
.cwc__img { opacity: 0; transition: opacity var(--t-slow); }
.cwc__img.is-loaded { opacity: 1; }
.cwc__ph { position: absolute; inset: 0; display: grid; place-items: center;
  font-family: var(--font-display); font-size: 3rem; color: var(--ink-ghost); }
.cwc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(7,10,18,.1), rgba(5,7,13,.65)); }
.cwc__play { position: absolute; inset: 0; display: grid; place-items: center; color: #fff;
  opacity: 0; transition: opacity var(--t-base); }
.cwc:hover .cwc__play { opacity: 1; }
.cwc__play :deep(svg) { filter: drop-shadow(0 2px 8px rgba(0,0,0,.6)); }
.cwc__ep { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono);
  font-size: var(--fs-2xs); font-weight: 700; padding: 2px 0.4375rem; border-radius: var(--r-xs);
  background: rgba(7,10,18,.7); color: var(--ice); }
.cwc__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.4); }
.cwc__bar span { display: block; height: 100%; background: var(--azure-bright);
  box-shadow: 0 0 6px var(--azure-glow); transition: width .4s var(--ease-silk); }
.cwc__title { margin-top: var(--s-2); font-size: var(--fs-sm); font-weight: 600; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; color: var(--ink); }
.cwc__epnum { font-size: var(--fs-xs); color: var(--ink-faint); }
</style>
