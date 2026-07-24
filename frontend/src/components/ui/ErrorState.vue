<script setup>
/* «Falló» NO es «no había nada» — la regla de oro del repo, ahora también en la UI.
 *
 * El backend está endurecido para no confundirlas (observability.py), pero el frontend sí las
 * confundía: un fetch que reventaba dejaba la lista en `[]` y la vista pintaba «Tu biblioteca
 * está vacía · añade series», con la biblioteca llena. Peor que un error: manda al usuario a
 * arreglar algo que no está roto, y le hace dudar de si ha perdido sus datos.
 *
 * Uso: SIEMPRE antes del EmptyState de "vacío", que pasa a ser el último caso.
 *   <ErrorState v-if="store.error" :detail="store.error" @retry="store.load()" />
 *   <EmptyState v-else-if="!items.length" … />
 */
import EmptyState from '@/components/ui/EmptyState.vue'
import Icon from '@/components/ui/Icon.vue'

defineProps({
  title: { type: String, default: 'No se pudo cargar.' },
  hint: { type: String, default: 'Comprueba que el servidor esté en marcha e inténtalo de nuevo.' },
  /* Mensaje técnico real. Se enseña en pequeño y en monoespaciada: al usuario no le estorba y
     convierte un "no va" en un informe de fallo útil. */
  detail: { type: String, default: '' },
})
defineEmits(['retry'])
</script>

<template>
  <EmptyState icon="globe" :title="title" :hint="hint">
    <template #action>
      <button class="is-primary" @click="$emit('retry')">
        <Icon name="spark" :size="15" /> Reintentar
      </button>
      <p v-if="detail" class="errst__detail">{{ detail }}</p>
    </template>
  </EmptyState>
</template>

<style scoped>
/* Ocupa toda la fila del slot para caer DEBAJO del botón, no a su lado. */
.errst__detail {
  flex-basis: 100%; margin-top: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs);
  color: var(--ink-ghost); word-break: break-word; max-width: 34rem;
}
</style>
