<script setup>
/* Control de densidad de la rejilla: tres pasos, mismo lenguaje que los ordenadores (`.sorts`).
 *
 * El glifo son barras verticales —2, 3, 4— porque el metáfora es literal: cuántas tarjetas caben.
 * Un icono genérico de rejilla no dice cuál es cuál, y una etiqueta de texto («Grande/Normal»)
 * pesa demasiado para algo que se toca una vez y se olvida.
 */
import { DENSITIES, density, setDensity } from '@/lib/density'
</script>

<template>
  <div class="dens" role="group" aria-label="Tamaño de las tarjetas">
    <button v-for="d in DENSITIES" :key="d.id" class="dens__b" :class="{ 'is-active': density === d.id }"
            :data-tip="d.label" :aria-label="d.label" :aria-pressed="density === d.id"
            @click="setDensity(d.id)">
      <span v-for="n in d.bars" :key="n" class="dens__bar" />
    </button>
  </div>
</template>

<style scoped>
.dens { display: flex; align-items: center; gap: 2px; padding: 2px;
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); }
.dens__b { display: flex; align-items: flex-end; justify-content: center; gap: 2px;
  width: 1.75rem; height: 1.75rem; padding: 0 0.25rem;
  border-radius: var(--r-sm); color: var(--ink-faint); transition: all var(--t-fast); }
.dens__b:hover { color: var(--azure-bright); }
.dens__b.is-active { color: var(--azure-bright); background: var(--azure-haze); }
/* Las barras son el icono: `currentColor` para que hereden el estado del botón. */
.dens__bar { flex: 1; min-width: 1px; height: 0.75rem; margin-bottom: 0.5rem;
  border-radius: 1px; background: currentColor; }
</style>
