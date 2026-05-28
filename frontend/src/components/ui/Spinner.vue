<script setup>
defineProps({ size: { type: [Number, String], default: 22 } })
</script>

<template>
  <span class="spinner" :style="{ width: size + 'px', height: size + 'px' }" aria-label="Cargando" />
</template>

<style scoped>
.spinner {
  display: inline-block;
  border-radius: 50%;
  border: 2px solid var(--line-2);
  border-top-color: var(--azure);
  animation: spin 0.7s linear infinite;
}
</style>
