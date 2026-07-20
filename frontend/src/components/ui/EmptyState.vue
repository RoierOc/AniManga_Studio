<script setup>
import Icon from '@/components/ui/Icon.vue'

// Shared empty / zero-results / error placeholder so every view reads the same.
// Use the `action` slot for a retry/login button when relevant.
defineProps({
  icon: { type: String, default: 'library' },
  title: { type: String, required: true },
  hint: { type: String, default: '' },
})
</script>

<template>
  <div class="empty-state">
    <span class="empty-state__brand jp" aria-hidden="true">青</span>
    <div class="empty-state__glyph"><Icon :name="icon" :size="30" /></div>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="hint" class="empty-state__hint">{{ hint }}</p>
    <div v-if="$slots.action" class="empty-state__action"><slot name="action" /></div>
  </div>
</template>

<style scoped>
.empty-state {
  position: relative; overflow: hidden;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: var(--s-3); padding: var(--s-9) var(--s-4); text-align: center;
  min-height: 40vh;
}
/* Filigrana de marca 青: decorativa, muy tenue, detrás del contenido. Da identidad
   a cada estado vacío sin competir con el texto. */
.empty-state__brand {
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -58%);
  font-size: 20rem; line-height: 1; font-weight: 700;
  color: var(--azure);
  opacity: 0.05;
  pointer-events: none; user-select: none; z-index: 0;
}
.empty-state > :not(.empty-state__brand) { position: relative; z-index: 1; }
.empty-state__glyph {
  width: 64px; height: 64px; display: grid; place-items: center;
  border-radius: var(--r-lg); color: var(--azure);
  background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 22%, transparent);
}
.empty-state__title { color: var(--ink-soft); font-size: var(--fs-base); }
.empty-state__hint { color: var(--ink-faint); font-size: var(--fs-sm); max-width: 30rem; }
.empty-state__action { margin-top: var(--s-2); }
</style>
