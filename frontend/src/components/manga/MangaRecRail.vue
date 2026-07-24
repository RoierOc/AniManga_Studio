<script setup>
import { computed } from 'vue'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Skeleton from '@/components/ui/Skeleton.vue'

// Riel horizontal de mangas recomendados (AniList). Reutilizable: la vista de
// biblioteca lo usa para "Para ti" y el modal para "Similares a este". Un clic
// abre la búsqueda del título en Explorar para descubrir/añadir la serie.
const props = defineProps({
  items:   { type: Array,  default: () => [] },
  loading: { type: Boolean, default: false },
  title:   { type: String, default: 'Recomendados' },
  subtitle:{ type: String, default: '' },
  // 'rail' = riel horizontal (biblioteca); 'grid' = cuadrícula que crece hacia abajo (pestaña del modal).
  layout:  { type: String, default: 'rail' },
})
const emit = defineEmits(['select'])

const has = computed(() => props.loading || props.items.length > 0)
const fmtLabel = (f) => ({ MANGA: 'Manga', MANHWA: 'Manhwa', MANHUA: 'Manhua', NOVEL: 'Novela', ONE_SHOT: 'One-shot' }[f] || '')
</script>

<template>
  <section v-if="has" class="rec">
    <header class="rec__head">
      <div>
        <h3 class="rec__title">{{ title }}</h3>
        <p v-if="subtitle" class="rec__sub">{{ subtitle }}</p>
      </div>
    </header>

    <div v-if="loading" class="rec__rail" :class="{ 'rec__rail--grid': layout === 'grid' }">
      <Skeleton v-for="n in (layout === 'grid' ? 12 : 6)" :key="n" variant="poster" class="rc" />
    </div>

    <div v-else class="rec__rail" :class="{ 'rec__rail--grid': layout === 'grid' }">
      <button v-for="r in items" :key="r.al_id" class="rc" @click="emit('select', r)"
              :title="`Buscar «${r.title}» en Explorar`">
        <div class="rc__cov">
          <img v-if="r.cover" :src="imgProxy(r.cover, 160)" alt="" loading="lazy" />
          <span v-else class="rc__mono">{{ (r.title || '?').charAt(0) }}</span>
          <span v-if="r.score" class="rc__score"><Icon name="spark" :size="10" /> {{ Math.round(r.score) }}</span>
          <span class="rc__go"><Icon name="search" :size="14" /></span>
        </div>
        <p class="rc__name">{{ r.title }}</p>
        <p class="rc__meta">
          <span v-if="fmtLabel(r.format)">{{ fmtLabel(r.format) }}</span>
          <span v-if="r.genres && r.genres.length" class="rc__gen">{{ r.genres[0] }}</span>
        </p>
      </button>
    </div>
  </section>
</template>

<style scoped>
.rec { margin: var(--s-5) 0; }
.rec__head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: var(--s-3); }
.rec__title { font-size: var(--fs-lg); font-weight: 700; color: var(--ink); }
.rec__sub { font-size: var(--fs-xs); color: var(--ink-soft); margin-top: 2px; }
.rec__rail {
  display: grid; grid-auto-flow: column; grid-auto-columns: 8.5rem;
  gap: var(--s-3); overflow-x: auto; overflow-y: hidden; scroll-snap-type: x proximity;
  padding-bottom: var(--s-2); scrollbar-width: thin;
}
/* Cuadrícula: crece hacia ABAJO en filas (pestaña Recomendados del modal). */
.rec__rail--grid {
  grid-auto-flow: row; grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr));
  grid-auto-columns: unset; overflow-x: visible; scroll-snap-type: none;
}
.rc {
  scroll-snap-align: start; display: flex; flex-direction: column; gap: 0.375rem;
  background: none; border: none; padding: 0; text-align: left; cursor: pointer;
}
.rc__cov {
  position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden;
  background: var(--surface-2); border: 1px solid var(--line);
  display: grid; place-items: center;
  transition: border-color var(--t-base), box-shadow var(--t-base), transform var(--t-base);
}
.rc:hover .rc__cov { border-color: var(--accent); box-shadow: 0 6px 20px rgba(0,0,0,.3); transform: translateY(-2px); }
.rc__cov img { width: 100%; height: 100%; object-fit: cover; }
.rc__mono { font-size: 2rem; font-weight: 800; color: var(--ink-faint); }
.rc__score {
  position: absolute; top: 0.3125rem; left: 0.3125rem; display: inline-flex; align-items: center; gap: 2px;
  padding: 2px 0.375rem; border-radius: var(--r-pill); font-size: 0.625rem; font-weight: 700;
  color: #fff; background: rgba(0,0,0,.62); backdrop-filter: blur(4px);
}
.rc__go {
  position: absolute; inset: 0; display: grid; place-items: center;
  background: rgba(10,14,24,.55); color: #fff; opacity: 0; transition: opacity var(--t-base);
}
.rc:hover .rc__go { opacity: 1; }
.rc__name {
  font-size: var(--fs-xs); font-weight: 600; color: var(--ink); line-height: var(--lh-snug);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.rc__meta { display: flex; gap: 0.375rem; font-size: 0.625rem; color: var(--ink-soft); }
.rc__gen { color: var(--ink-faint); }
</style>
