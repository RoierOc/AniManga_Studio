<script setup>
import Icon from '@/components/ui/Icon.vue'
defineProps({ label: { type: String, default: 'Vista' }, icon: { type: String, default: 'spark' } })
</script>

<template>
  <div class="ph">
    <div class="ph__glyph"><Icon :name="icon" :size="40" /></div>
    <h2 class="ph__title">{{ label }}</h2>
    <p class="ph__text">
      Esta sección se está reconstruyendo en el nuevo motor visual.
      <br />Próxima fase de la migración.
    </p>
    <span class="ph__tag">EN MIGRACIÓN</span>
  </div>
</template>

<style scoped>
.ph {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 70vh; text-align: center; gap: var(--s-3);
  animation: rise var(--t-cine) var(--ease-silk);
}
.ph__glyph {
  width: 5.75rem; height: 5.75rem; display: grid; place-items: center;
  border-radius: var(--r-lg); color: var(--azure);
  background: var(--azure-haze); border: 1px solid var(--line-2);
  box-shadow: var(--glow-azure);
  margin-bottom: var(--s-2);
}
.ph__title { font-size: var(--fs-2xl); }
.ph__text { color: var(--ink-faint); font-size: var(--fs-sm); }
.ph__tag {
  margin-top: var(--s-3);
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  letter-spacing: var(--tracking-caps); color: var(--cyan);
  padding: var(--s-1) var(--s-3); border-radius: var(--r-pill);
  border: 1px solid var(--cyan-glow);
}
</style>
