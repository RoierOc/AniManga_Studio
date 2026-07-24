<script setup>
/* Shell de Series y Películas — espejo de `views/anime/AnimeStudio.vue`: pestañas + swap de vista
   con View Transitions. El detalle sustituye a la vista activa, como en anime. */
import { computed, onMounted } from 'vue'
import { useMediaStore } from '@/stores/media'
import { useUiStore } from '@/stores/ui'
import { vtGo } from '@/lib/vt'
import MediaHome from './MediaHome.vue'
import MediaSearch from './MediaSearch.vue'
import MediaDiscover from './MediaDiscover.vue'
import MediaDetail from './MediaDetail.vue'
import ReleasePicker from './ReleasePicker.vue'
import Icon from '@/components/ui/Icon.vue'
// El modal de subtítulos es el MISMO de anime (vive en el store de anime y se teletransporta al
// body): montarlo aquí basta para que Series/Películas tengan búsqueda e inyección de subs.
import SubTrackModal from '@/components/anime/SubTrackModal.vue'

const store = useMediaStore()

const TABS = [
  { id: 'library', label: 'Mi Biblioteca', icon: 'film' },
  { id: 'search', label: 'Buscar', icon: 'search' },
  { id: 'discover', label: 'Descubrir', icon: 'globe' },
]

const VIEW_MAP = { library: MediaHome, search: MediaSearch, discover: MediaDiscover }
const active = computed(() => VIEW_MAP[store.sub] || MediaHome)

onMounted(() => store.init())

function selectTab(id) {
  vtGo(() => {
    store.closeDetail()
    store.setSub(id)
    useUiStore().pushNav()
  })
}
</script>

<template>
  <div class="mstudio">
    <nav v-if="!store.detail" class="subnav">
      <button v-for="t in TABS" :key="t.id" class="subnav__tab" :class="{ 'is-active': store.sub === t.id }"
              @click="selectTab(t.id)">
        <Icon :name="t.icon" :size="16" /> {{ t.label }}
      </button>
    </nav>

    <MediaDetail v-if="store.detail" :item="store.detail" @back="store.closeDetail()" />
    <component v-else :is="active" />

    <!-- Vive en el shell, no en una vista: así se abre desde la tarjeta de cualquier pestaña. -->
    <ReleasePicker v-if="store.picker" v-bind="store.picker"
                   @close="store.closePicker()" />
    <SubTrackModal />
  </div>
</template>

<style scoped>
.mstudio { display: block; }
</style>
