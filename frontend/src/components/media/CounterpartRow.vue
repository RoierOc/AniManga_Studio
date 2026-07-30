<script setup>
/* Puente manga ⇄ anime: la fila que dice «esto también existe al otro lado de la app».
   Una sola pieza para las dos direcciones — desde la ficha de un manga lista sus animes, desde
   la de un anime la obra de la que salió. Lo que la hace útil no es el listado (eso lo da
   AniList) sino la última columna: si YA lo tienes en la otra biblioteca. Ver src/api/bridge.py. */
import { computed, ref, watch } from 'vue'
import { api } from '@/lib/api'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import { imgProxy } from '@/lib/img'

const props = defineProps({
  alId: { type: [Number, String], default: null },
  // 'manga' → devuelve animes · 'anime' → devuelve el manga/novela original
  from: { type: String, default: 'manga' },
})
const emit = defineEmits(['open'])

const data = ref(null)
const loading = ref(false)
const err = ref('')

const ESTADO = {
  RELEASING: 'En emisión', FINISHED: 'Terminado',
  NOT_YET_RELEASED: 'Anunciado', CANCELLED: 'Cancelado', HIATUS: 'En pausa',
}
const destino = computed(() => (props.from === 'manga' ? 'anime' : 'manga'))

/* Se juntan los trozos QUE HAY y luego se unen: coser el separador a cada dato deja puntos
   colgando en cuanto AniList no rellena uno (una obra anunciada no tiene ni año ni episodios). */
function meta(c) {
  return [
    c.year,
    c.episodes ? `${c.episodes} ep` : (c.chapters ? `${c.chapters} cap.` : ''),
    ESTADO[c.status],
  ].filter(Boolean).join(' · ')
}
const items = computed(() => data.value?.counterparts || [])

async function load() {
  if (!props.alId) { data.value = null; return }
  loading.value = true; err.value = ''
  try {
    data.value = await api.get(`/api/bridge/counterpart?al_id=${props.alId}&from=${props.from}`)
  } catch (e) {
    // Un fallo de AniList NO puede pintarse como «no tiene adaptación»: son cosas distintas.
    err.value = e?.body || e?.message || 'No se pudo consultar AniList.'
  } finally { loading.value = false }
}
watch(() => [props.alId, props.from], load, { immediate: true })
</script>

<template>
  <section v-if="alId && (loading || err || items.length)" class="cpr">
    <p class="eyebrow"><span class="tick" />
      {{ destino === 'anime' ? 'TAMBIÉN EN ANIME' : 'LA OBRA ORIGINAL' }}
    </p>

    <Spinner v-if="loading" :size="20" />
    <ErrorState v-else-if="err" title="No se pudo consultar AniList."
                :detail="err" @retry="load()" />

    <template v-else>
      <p v-if="data?.verdict" class="cpr__verdict">{{ data.verdict }}</p>

      <ul class="cpr__list">
        <li v-for="c in items" :key="c.al_id" class="cpr__item" :class="{ 'is-mine': c.in_library }"
            @click="emit('open', { ...c, kind: destino })">
          <img v-if="c.cover" :src="imgProxy(c.cover, 120)" :alt="c.title" class="cpr__cover"
               loading="lazy" decoding="async" />
          <div v-else class="cpr__cover cpr__cover--ph"><Icon name="library" :size="18" /></div>
          <div class="cpr__txt">
            <b>{{ c.title }}</b>
            <span class="cpr__meta">{{ meta(c) }}</span>
          </div>
          <!-- `in_library` puede venir AUSENTE si no se pudo leer la otra biblioteca: entonces no
               se dice nada, que es distinto de decir «no la tienes» (regla del repo). -->
          <span v-if="c.in_library" class="cpr__have"><Icon name="check" :size="12" /> La tienes</span>
          <span v-else-if="c.in_library === false" class="cpr__go">
            <Icon name="search" :size="12" /> Buscar
          </span>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.cpr { margin: 0 0 var(--s-6); }
.cpr__verdict { margin: var(--s-2) 0 var(--s-3); font-size: var(--fs-sm); color: var(--ink-soft);
  line-height: var(--lh-body); max-width: 46rem; }
.cpr__list { display: flex; flex-wrap: wrap; gap: var(--s-2); margin: 0; padding: 0; list-style: none; }
/* `flex: 0 1` y no `1 1`: con una sola adaptación (el caso normal) una tarjeta elástica se
   estiraba a lo ancho del modal y parecía una barra, no una obra. */
.cpr__item { display: flex; align-items: center; gap: var(--s-3); flex: 0 1 22rem; min-width: 17rem;
  padding: var(--s-2); border-radius: var(--r-md); background: var(--surface);
  border: 1px solid var(--line); cursor: pointer; transition: all var(--t-fast); }
.cpr__item:hover { border-color: var(--azure); background: var(--azure-haze); }
.cpr__item.is-mine { border-color: color-mix(in srgb, var(--jade) 45%, transparent); }
.cpr__cover { width: 2.75rem; height: 4rem; object-fit: cover; border-radius: var(--r-sm); flex: none; }
.cpr__cover--ph { display: grid; place-items: center; background: var(--surface-2); color: var(--ink-ghost); }
.cpr__txt { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.cpr__txt b { font-size: var(--fs-sm); font-weight: 600; color: var(--ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cpr__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
.cpr__have { display: inline-flex; align-items: center; gap: 4px; flex: none;
  font-size: var(--fs-2xs); font-weight: 600; color: var(--jade); }
.cpr__go { display: inline-flex; align-items: center; gap: 4px; flex: none;
  font-size: var(--fs-2xs); color: var(--ink-ghost); }
.cpr__item:hover .cpr__go { color: var(--azure-bright); }
</style>
