<script setup>
/* La ÚNICA rueda de carga de la app.
 *
 * Antes había nueve declaraciones de `animation: spin` repartidas (`.xspin`, `.covspin`,
 * `.more__spin`, `.adr__spin`, `.dz__spin`, `.ep__spin`…) girando a velocidades y grosores
 * distintos: el ojo registra esa incoherencia aunque no sepa nombrarla.
 *
 * `size` en px (número) o cualquier medida CSS ('1rem'). `tone`: 'accent' (azure, por defecto),
 * 'light' (sobre vídeo/botón lleno) o 'ok' (jade, tarea que va bien).
 */
defineProps({
  size: { type: [Number, String], default: 22 },
  tone: { type: String, default: 'accent' },   // accent | light | ok
})
const dim = (s) => (typeof s === 'number' || /^\d+$/.test(s) ? `${s}px` : s)
</script>

<template>
  <span class="spinner" :class="`spinner--${tone}`"
        :style="{ width: dim(size), height: dim(size) }" aria-label="Cargando" />
</template>

<style scoped>
.spinner {
  display: inline-block; flex-shrink: 0;
  border-radius: 50%;
  border: 2px solid var(--line-2);
  border-top-color: var(--azure);
  animation: spin 0.7s linear infinite;
}
.spinner--light { border-color: rgba(255, 255, 255, 0.28); border-top-color: #fff; }
.spinner--ok { border-top-color: var(--jade); }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 1.6s; } }
</style>
