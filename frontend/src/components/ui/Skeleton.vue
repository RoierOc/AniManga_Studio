<script setup>
// Esqueleto de carga unificado. Una sola implementación para toda la app
// (antes: 3 keyframes distintos — pulse/recpulse/shimmer — por componente).
// Usa el keyframe `shimmer` global (base.css) y los tokens de superficie.
//   variant: 'poster' (2:3) | 'line' | 'block'
//   ratio:   sobrescribe el aspect-ratio (p.ej. '3/4.3', '16/9')
defineProps({
  variant: { type: String, default: 'block' },
  ratio: { type: String, default: '' },
  width: { type: String, default: '' },
  height: { type: String, default: '' },
  radius: { type: String, default: '' },
})
</script>

<template>
  <div
    class="skel"
    :class="`skel--${variant}`"
    :style="{
      aspectRatio: ratio || undefined,
      width: width || undefined,
      height: height || undefined,
      borderRadius: radius || undefined,
    }"
    aria-hidden="true"
  />
</template>

<style scoped>
.skel {
  background:
    linear-gradient(100deg,
      var(--surface) 30%,
      var(--surface-2) 50%,
      var(--surface) 70%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
  border-radius: var(--r-md);
}
.skel--poster { aspect-ratio: 2 / 3; }
.skel--line   { height: 0.75rem; border-radius: var(--r-xs); }
@media (prefers-reduced-motion: reduce) {
  .skel { animation: none; }
}
</style>
