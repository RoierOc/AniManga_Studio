<script setup>
/* Barra de filtros + orden + búsqueda de las vistas de biblioteca.
 *
 * El markup estaba copiado LITERAL en tres sitios (LibraryView, AnimeLibrary, MediaHome). Las
 * clases ya vivían en `base.css`, pero el HTML no, así que cada mejora del buscador o de los
 * pills había que hacerla tres veces — y por eso divergían.
 *
 * `filters` y `sorts` son `[{ id, label, n?, color?, icon? }]`. Un filtro con `n === 0` se
 * oculta (salvo el que esté activo, para no dejar la barra sin selección visible). El hueco
 * `#extra` es para los botones propios de cada vista (Historial, Portadas, Carpetas…).
 */
import Icon from '@/components/ui/Icon.vue'

defineProps({
  filters: { type: Array, default: () => [] },
  filter: { type: String, default: '' },
  sorts: { type: Array, default: () => [] },
  sort: { type: String, default: '' },
  search: { type: String, default: '' },
  searchPlaceholder: { type: String, default: 'Buscar…' },
})
defineEmits(['update:filter', 'update:sort', 'update:search'])
</script>

<template>
  <div class="toolbar stagger">
    <div class="filters" style="--i:2">
      <button v-for="f in filters" :key="f.id"
              v-show="f.n == null || f.n > 0 || f.id === filter"
              class="pill" :class="{ 'is-active': filter === f.id }"
              :data-tip="f.title || f.label"
              :style="filter === f.id && f.color ? { color: f.color, borderColor: f.color } : {}"
              @click="$emit('update:filter', f.id)">
        <Icon v-if="f.icon" :name="f.icon" :size="13" />
        {{ f.label }}
        <span v-if="f.n != null" class="pill__n">{{ f.n }}</span>
      </button>
    </div>

    <div class="toolbar__right" style="--i:2">
      <div v-if="sorts.length" class="sorts">
        <button v-for="s in sorts" :key="s.id" class="sort" :class="{ 'is-active': sort === s.id }"
                @click="$emit('update:sort', s.id)">{{ s.label }}</button>
      </div>
      <slot name="extra" />
      <label class="searchbox">
        <Icon name="search" :size="15" />
        <input :value="search" type="search" :placeholder="searchPlaceholder"
               @input="$emit('update:search', $event.target.value)" />
      </label>
    </div>
  </div>
</template>
