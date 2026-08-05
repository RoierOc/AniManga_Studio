<script>
/* Ancho al que se PINTA cada miniatura en el monitor más grande que soportamos (2560), medido:
   544 px la ancha (16:9) y 252 la de póster. Vive AQUÍ, con el CSS que lo decide, y no como un
   número suelto en cada vista: la tarjeta ancha se pedía a 340 px en Mi Anime y en Cine, el
   backend redondeaba a 480 y a 2560 se estiraba ×1,13 — una miniatura AMPLIADA, que es lo que
   delata una imagen mal servida. Si mañana cambia `flex-basis`, cambia este número y no tres. */
export const RAIL_W = 560
export const RAIL_POSTER_W = 260
</script>

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
  /* Segunda línea del encabezado («6 sin terminar», «invierno 2026»). Es el contexto que
     convierte un título de riel en una frase: sin ella, «Continuar» no dice cuánto queda. */
  hint: { type: String, default: '' },
  /* Enlace opcional a la derecha del título («Ver calendario ›»). Aparece al pasar por encima
     del riel: en reposo la fila es contenido, no botones. */
  action: { type: Object, default: null },   // { label, fn }
  /* Apaga el arte hasta el hover. Lo usa el riel de «lo dejaste a medias»: son obras que ya
     abandonaste, y a pleno color competirían con lo que sí estás siguiendo. */
  dim: { type: Boolean, default: false },
})
const emit = defineEmits(['play', 'menu', 'enter'])

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
  <section v-if="items.length" class="cw" :class="{ 'is-poster': poster, 'is-dim': dim }">
    <header class="cw__head">
      <h3 class="cw__title">{{ title }}</h3>
      <span v-if="hint" class="cw__hint">{{ hint }}</span>
      <button v-if="action" class="cw__all" @click="action.fn">{{ action.label }} ›</button>
    </header>
    <div class="cw__row" @wheel="onWheel">
      <article v-for="it in items" :key="it.id" class="cwc"
               @click="emit('play', it)" @mouseenter="emit('enter', it)"
               @contextmenu.prevent="emit('menu', { ev: $event, item: it })">
        <div class="cwc__thumb">
          <img v-if="it.thumb" :src="it.thumb" :alt="it.title" loading="lazy" decoding="async" class="cwc__img"
               @load="$event.target.classList.add('is-loaded')"
               @error="$event.target.style.display = 'none'" />
          <div v-else class="cwc__ph">{{ (it.title || '?')[0].toUpperCase() }}</div>
          <div class="cwc__scrim" />
          <div class="cwc__play"><Icon name="play" :size="28" /></div>
          <!-- `tone` marca el DOMINIO (anime/manga/cine/novela). Sólo lo usa la Portada, que es
               la única fila donde se mezclan: sin la etiqueta, un póster de manga y uno de anime
               son indistinguibles y la fila se lee como un revoltijo. -->
          <span v-if="it.badge" class="cwc__ep" :class="it.tone && `is-${it.tone}`">{{ it.badge }}</span>
          <!-- Recordatorio de dónde lo dejaste: las últimas páginas leídas, al pasar por encima.
               Vive aquí y no en un componente aparte porque es una capa DENTRO de la miniatura;
               para cualquier riel que no mande `recap` este bloque no existe. -->
          <div v-if="it.recap?.length" class="cwc__recap">
            <img v-for="(p, i) in it.recap" :key="i" :src="p" alt="" loading="lazy" decoding="async" />
          </div>
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
.cw__head { display: flex; align-items: baseline; gap: var(--s-3); margin-bottom: var(--s-4); }
.cw__title { font-family: var(--font-display); font-size: var(--fs-xl); }
.cw__hint { font-size: var(--fs-xs); color: var(--ink-faint); }
/* En reposo la fila es contenido; el enlace sólo aparece cuando el ratón ya está ahí. Con
   teclado no hay hover, así que `:focus-visible` lo revela igual — si no, sería inalcanzable. */
.cw__all { margin-left: auto; font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright);
  opacity: 0; transition: opacity var(--t-base); }
.cw:hover .cw__all, .cw__all:focus-visible { opacity: 1; }
.cw__all:hover { color: #fff; }
.cw__row { display: flex; gap: var(--s-4); overflow-x: auto; padding-bottom: var(--s-3); }
/* `flex-grow` con tope: cuando hay pocos elementos reparten el hueco libre en vez de quedarse
   apelotonados a la izquierda; cuando hay muchos no hay hueco que repartir, así que `grow` no
   hace nada y la fila desborda y se recorre, como siempre. `flex-shrink: 0` es lo que garantiza
   ese desbordamiento — sin él se encogerían para caber todas. */
/* `min-width: 0` NO es decorativo: el mínimo automático de un ítem flex es su tamaño min-content,
   y el título va en una línea con `nowrap`, así que un título largo empujaba la tarjeta por encima
   de su base. Medido en el riel de anime: 334 px las normales y 434 px tres de ellas, con la
   miniatura a 244 px en vez de 188. Con `width` fijo no se notaba porque nada podía crecer. */
.cwc { flex: 1 0 20rem; min-width: 0; max-width: 26rem; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.cwc:hover { transform: translateY(-5px); }
.cwc__thumb { position: relative; aspect-ratio: 16/9; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface-2); border: 1px solid var(--line); }
.cwc:hover .cwc__thumb { border-color: var(--azure-glow); box-shadow: var(--shadow-lg); }
/* Modo póster (manga/novelas): 2:3, la forma de una portada. Forzar una portada a 16:9 la
   recortaría por la mitad.
   El ancho subió de 8.5rem porque MEDIDO era el error de jerarquía más claro de la app: en
   Biblioteca la tarjeta de «Continuar leyendo» medía 142 px y las de la rejilla de abajo 300 —
   lo que estás leyendo era la mitad de grande que lo que no has empezado. En Mi Anime, la vista
   de referencia, pasa justo al revés (riel 334 px, rejilla 300). */
/* MEDIDO a 2560 (1-ago-2026): con 15rem la tarjeta del riel salía 315×539 y la de la rejilla de
   abajo 336×504 — el riel era lo MÁS ALTO de la pantalla, y con sólo dos obras en curso parecía
   una rejilla truncada, no un riel. Se baja a un punto intermedio, NO al 8.5rem de antes (que era
   el error contrario: 142 px contra 300). Ahora ~252×415: se lee como riel y sigue por encima de
   la mitad de la tarjeta de rejilla. */
.is-poster .cwc { flex-basis: 10rem; max-width: 12rem; }
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
/* Un color por dominio, del set frío de la paleta (nada de naranja). El fondo se mantiene oscuro
   y sólo cambia la tinta: la etiqueta informa sin competir con la portada. */
.cwc__ep.is-anime  { color: var(--azure-bright); }
.cwc__ep.is-manga  { color: var(--rose); }
.cwc__ep.is-cine   { color: var(--violet); }
.cwc__ep.is-novela { color: var(--jade); }
.cwc__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.4); }
.cwc__bar span { display: block; height: 100%; background: var(--azure-bright);
  box-shadow: 0 0 6px var(--azure-glow); transition: width .4s var(--ease-silk); }
/* Riel apagado: el arte pierde color hasta que te interesas por él. La transición es lo que
   hace que se lea como «dormido», no como «roto». */
.is-dim .cwc__thumb img { filter: saturate(.6) brightness(.78); transition: filter var(--t-base) var(--ease-silk); }
.is-dim .cwc:hover .cwc__thumb img { filter: none; }

.cwc__recap { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 2px;
  padding: var(--s-2); opacity: 0; transition: opacity var(--t-base) var(--ease-silk);
  background: linear-gradient(to top, rgba(7,10,18,.94), transparent); }
.cwc:hover .cwc__recap { opacity: 1; }
.cwc__recap img { flex: 1; min-width: 0; aspect-ratio: 2/3; object-fit: cover;
  border-radius: var(--r-xs); border: 1px solid var(--line-2); background: var(--surface-3); }

.cwc__title { margin-top: var(--s-2); font-size: var(--fs-sm); font-weight: 600; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; color: var(--ink); }
.cwc__epnum { font-size: var(--fs-xs); color: var(--ink-faint); }
</style>
