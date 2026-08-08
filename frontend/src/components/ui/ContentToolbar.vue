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
 *
 * `genres` (opcional, de `lib/generos.js`) añade el filtro por género. Va AQUÍ y no en cada
 * vista porque las tres estanterías comparten esta barra: puesto tres veces a mano volvería a
 * divergir, que es justo por lo que existe este componente.
 */
import { computed } from 'vue'
import Icon from '@/components/ui/Icon.vue'
import Select from '@/components/ui/Select.vue'
import { generoActivo, filtroDeGenero } from '@/lib/generos'

const props = defineProps({
  filters: { type: Array, default: () => [] },
  filter: { type: String, default: '' },
  sorts: { type: Array, default: () => [] },
  sort: { type: String, default: '' },
  search: { type: String, default: '' },
  searchPlaceholder: { type: String, default: 'Buscar…' },
  // [{ value, label, hint }] de `opcionesGenero()`. Vacío ⇒ el control ni aparece.
  genres: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:filter', 'update:sort', 'update:search'])

const genre = computed({
  get: () => generoActivo(props.filter),
  set: (g) => emit('update:filter', filtroDeGenero(g)),
})
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
      <!-- El género es un FILTRO: va pegado a los pills, no junto al orden. Con el género puesto
           los pills se apagan solos (el filtro activo pasa a ser `gen:…`), así que se ve de un
           vistazo por qué estás mirando lo que estás mirando. -->
      <Select v-if="genres.length" v-model="genre" icon="palette" class="tb__gen"
              :class="{ 'is-on': !!genre }" aria-label="Filtrar por género" :options="genres" />
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

<style scoped>
/* Con género puesto, el desplegable se pinta como un pill activo: es el mismo lenguaje que ya
   usa la fila de estados, y hace falta porque al filtrar por género TODOS los pills se apagan —
   sin esta marca la barra parecería «sin filtro» mientras la rejilla enseña un subconjunto. */
.tb__gen.is-on :deep(.usel__btn) {
  background: var(--azure-haze); border-color: var(--azure); color: var(--azure-bright);
}
.tb__gen.is-on :deep(.usel__ico) { color: var(--azure-bright); }
</style>
