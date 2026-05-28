<script setup>
import { computed } from 'vue'
import Icon from '@/components/ui/Icon.vue'

const props = defineProps({ manga: { type: Object, required: true } })
const hasCover = computed(() => !!props.manga.cover)
const upscaled = computed(() => (props.manga.upscaled || 0) > 0)
const initials = computed(() =>
  (props.manga.name || '?').replace(/[\[\]_]/g, ' ').trim().slice(0, 2).toUpperCase()
)
</script>

<template>
  <article class="card" tabindex="0">
    <div class="card__poster">
      <img v-if="hasCover" :src="manga.cover" :alt="manga.name" loading="lazy" class="card__img"
           @load="$event.target.classList.add('is-loaded')" @error="$event.target.style.display='none'" />
      <div v-else class="card__fallback"><span>{{ initials }}</span></div>

      <div class="card__scrim" />
      <div class="card__shine" />

      <!-- HUD badges -->
      <div class="card__badges">
        <span v-if="upscaled" class="badge badge--up">
          <Icon name="spark" :size="11" /> 4K · {{ manga.upscaled }}
        </span>
      </div>

      <div class="card__hover">
        <button class="card__open"><Icon name="play" :size="18" /></button>
      </div>
    </div>

    <div class="card__meta">
      <h3 class="card__title">{{ manga.name }}</h3>
      <div class="card__stats">
        <span>{{ manga.chapter_count || 0 }} cap.</span>
        <span class="dot" />
        <span>{{ manga.image_count || 0 }} pág.</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  position: relative;
  border-radius: var(--r-md);
  cursor: pointer;
  outline: none;
  transition: transform var(--t-base) var(--ease-snap);
}
.card:hover, .card:focus-visible { transform: translateY(-6px); }

.card__poster {
  position: relative;
  aspect-ratio: 2 / 3;
  border-radius: var(--r-md);
  overflow: hidden;
  background: var(--surface-2);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-sm);
  transition: box-shadow var(--t-base) var(--ease-silk), border-color var(--t-base);
}
.card:hover .card__poster {
  border-color: var(--azure-glow);
  box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow);
}

.card__img { width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--t-slow) var(--ease-silk), transform var(--t-cine) var(--ease-silk); }
.card__img.is-loaded { opacity: 1; }
.card:hover .card__img { transform: scale(1.07); }

.card__fallback {
  position: absolute; inset: 0; display: grid; place-items: center;
  background: radial-gradient(circle at 50% 30%, var(--surface-3), var(--surface));
  color: var(--ink-ghost); font-family: var(--font-display); font-size: 2rem; font-weight: 600;
}

.card__scrim {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, transparent 45%, rgba(7, 10, 18, 0.85) 100%);
}

/* diagonal holo shine on hover */
.card__shine {
  position: absolute; inset: 0;
  background: linear-gradient(112deg, transparent 35%, rgba(168, 200, 255, 0.14) 48%, transparent 60%);
  transform: translateX(-120%);
  pointer-events: none;
}
.card:hover .card__shine { animation: shine 0.8s var(--ease-silk) forwards; }
@keyframes shine { to { transform: translateX(120%); } }

.card__badges { position: absolute; top: var(--s-2); left: var(--s-2); display: flex; gap: var(--s-1); }
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: var(--r-pill);
  font-size: var(--fs-2xs); font-weight: 600;
  font-family: var(--font-mono); letter-spacing: 0.02em;
  backdrop-filter: blur(8px);
}
.badge--up { background: var(--cyan-glow); color: #d6fffb; border: 1px solid rgba(70, 224, 216, 0.4); box-shadow: var(--glow-cyan); }

.card__hover {
  position: absolute; inset: 0; display: grid; place-items: center;
  opacity: 0; transition: opacity var(--t-base) var(--ease-silk);
}
.card:hover .card__hover, .card:focus-visible .card__hover { opacity: 1; }
.card__open {
  width: 52px; height: 52px; display: grid; place-items: center;
  border-radius: 50%; color: #fff;
  background: var(--azure); box-shadow: var(--glow-azure);
  transform: scale(0.8); transition: transform var(--t-base) var(--ease-snap);
}
.card:hover .card__open { transform: scale(1); }

.card__meta { padding: var(--s-3) var(--s-1) 0; }
.card__title {
  font-family: var(--font-body); font-weight: 600; font-size: var(--fs-sm);
  line-height: var(--lh-snug); color: var(--ink);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.card__stats {
  display: flex; align-items: center; gap: var(--s-2);
  margin-top: var(--s-1); font-size: var(--fs-xs); color: var(--ink-faint);
}
.dot { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-ghost); }
</style>
