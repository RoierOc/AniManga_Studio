<script setup>
/* Tarjeta de Obra (Fase 3) — presentacional, compartida por el grid de búsqueda y los rieles
 * de tendencia. Clic → emite select. Botón rápido "+" (aparece al pasar el ratón) para añadir a
 * biblioteca sin abrir la ficha. */
import { computed } from 'vue'
import { useDiscoveryStore } from '@/stores/discovery'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({ work: { type: Object, required: true } })
const emit = defineEmits(['select'])
const disco = useDiscoveryStore()

const TYPE_LABEL = { manga: 'Manga', manhwa: 'Manhwa', manhua: 'Manhua', novel: 'Novela', other: 'Otro' }
// Sin `&w=`: ese parámetro del proxy es SOLO para el micro-thumb del blur-up (clampa a 64px).
// La portada ya viene dimensionada del CDN de MangaBaka (~700px), nítida.
const cover = computed(() => imgProxy(props.work.cover || '', 260))
const added = computed(() => disco.inLibrary(props.work))

function quickAdd() { disco.addToLibrary(props.work) }
</script>

<template>
  <article class="wc" tabindex="0" @click="emit('select', work)" @keydown.enter="emit('select', work)">
    <div class="wc__poster">
      <img v-if="work.cover" :src="cover" :alt="work.title" loading="lazy"
           @load="$event.target.classList.add('is-loaded')" class="wc__img" />
      <div v-else class="wc__ph"><Icon name="library" :size="24" /></div>
      <div class="wc__scrim" />
      <span class="wc__type">{{ TYPE_LABEL[work.type] || 'Obra' }}</span>
      <span v-if="work.rating" class="wc__score"><Icon name="spark" :size="10" /> {{ work.rating }}</span>
      <button class="wc__add" :class="{ 'is-added': added }"
              :title="added ? 'En tu biblioteca' : 'Añadir a biblioteca'"
              :aria-label="added ? 'En tu biblioteca' : 'Añadir a biblioteca'"
              @click.stop="quickAdd">
        <Icon :name="added ? 'check' : 'plus'" :size="15" />
      </button>
      <div class="wc__overlay">
        <h3 class="wc__title">{{ work.title }}</h3>
        <span v-if="work.year" class="wc__sub">{{ work.year }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.wc { cursor: pointer; border-radius: var(--r-md); }
.wc__poster { position: relative; aspect-ratio: 3 / 4.3; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface); box-shadow: var(--shadow-sm, var(--shadow-md)); }
.wc__img { width: 100%; height: 100%; object-fit: cover; opacity: 0;
  transition: opacity var(--t-base), transform var(--t-base) var(--ease-silk); }
.wc__img.is-loaded { opacity: 1; }
.wc:hover .wc__img.is-loaded { transform: scale(1.05); }
.wc__ph { width: 100%; height: 100%; display: grid; place-items: center; color: var(--ink-faint); }
.wc__scrim { position: absolute; inset: 0; background: linear-gradient(0deg, rgba(7,10,18,.92) 4%, rgba(7,10,18,.1) 40%, transparent 66%); }
.wc__type { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs);
  font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--ink); background: rgba(7,10,18,.72);
  padding: 2px 0.4375rem; border-radius: var(--r-xs); backdrop-filter: blur(4px); }
.wc__score { position: absolute; top: var(--s-2); right: var(--s-2); display: inline-flex; align-items: center; gap: 3px;
  font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; color: var(--cyan); background: rgba(7,10,18,.72);
  padding: 2px 0.4375rem; border-radius: var(--r-xs); backdrop-filter: blur(4px); }
/* Quick-add: aparece al pasar el ratón; si ya está en biblioteca se queda visible en jade. */
.wc__add { position: absolute; right: var(--s-2); bottom: var(--s-2); z-index: 2; width: 1.875rem; height: 1.875rem;
  display: grid; place-items: center; border-radius: 50%; color: #fff; background: var(--azure);
  border: 1px solid rgba(255,255,255,.25); box-shadow: var(--shadow-md);
  opacity: 0; transform: translateY(4px); transition: opacity var(--t-fast), transform var(--t-fast), background var(--t-fast); }
.wc:hover .wc__add, .wc:focus-within .wc__add { opacity: 1; transform: none; }
.wc__add:hover { background: var(--azure-bright); }
.wc__add.is-added { opacity: 1; transform: none; color: var(--jade); background: color-mix(in srgb, var(--jade) 22%, rgba(7,10,18,.7)); }
.wc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); pointer-events: none; }
.wc__title { font-size: var(--fs-sm); font-weight: 650; line-height: 1.25; color: #fff;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.wc__sub { font-size: var(--fs-2xs); color: var(--ice, var(--ink-soft)); }
@media (prefers-reduced-motion: reduce) { .wc__add { transition: none; } }
</style>
