<script setup>
// Selector MANUAL de torrent, al estilo del panel de anime: tú eliges el release mirando
// seeders, tamaño y grupo, en vez de dejar que Sonarr decida por su perfil de calidad.
import { computed, onMounted, ref } from 'vue'
import { api } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const props = defineProps({
  kind: { type: String, required: true },     // episode | season | movie
  id: { type: [Number, String], required: true },
  season: { type: Number, default: null },
  query: { type: String, default: '' },       // respaldo para el buscador crudo de Prowlarr
  label: { type: String, default: '' },
})
const emit = defineEmits(['close', 'grabbed'])

const ui = useUiStore()
const loading = ref(true)
const err = ref('')
const releases = ref([])
const down = ref([])
const viaProwlarr = ref(false)
const grabbing = ref('')
const onlyHealthy = ref(false)

const shown = computed(() => onlyHealthy.value
  ? releases.value.filter(r => (r.seeders || 0) > 0 && !r.rejected)
  : releases.value)

async function load() {
  loading.value = true; err.value = ''
  try {
    const qs = new URLSearchParams({ kind: props.kind, id: props.id })
    if (props.season != null) qs.set('season', props.season)
    if (props.query) qs.set('q', props.query)
    const d = await api.get(`/api/media/releases?${qs}`)
    releases.value = d.releases || []
    down.value = d.indexers_down || []
    viaProwlarr.value = !!d.via_prowlarr
  } catch (e) {
    err.value = e?.body || e?.message || 'no se pudo buscar'
  } finally { loading.value = false }
}
onMounted(load)

async function grab(r) {
  grabbing.value = r.guid
  try {
    await api.post('/api/media/releases/grab', {
      kind: props.kind === 'movie' ? 'movie' : 'series',
      // El backend necesita saber sobre QUÉ se descarga, no sólo qué torrent: la biblioteca
      // entra sin monitorizar (nada se baja solo) y esto es lo que monitoriza justo lo elegido
      // para que Sonarr/Radarr lo importen al terminar. `id` es el episodio si kind='episode',
      // y la serie si kind='season'.
      id: props.id, ...(props.kind === 'season' ? { season: props.season } : {}),
      guid: r.guid, indexer_id: r.indexer_id,
      via: r.via, title: r.title, download_url: r.download_url,
    })
    ui.toast('Enviado a descargar ✓', 'ok')
    emit('grabbed')
    emit('close')
  } catch (e) {
    ui.toast(`No se pudo descargar: ${e?.body || e?.message || ''}`, 'err')
  } finally { grabbing.value = '' }
}

const age = (h) => (h < 24 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`)
</script>

<template>
  <div class="rp" @click.self="emit('close')">
    <div class="rp__box">
      <header class="rp__head">
        <div>
          <h3>Elegir torrent</h3>
          <p v-if="label" class="rp__sub">{{ label }}</p>
        </div>
        <button class="rp__x" @click="emit('close')"><Icon name="close" :size="16" /></button>
      </header>

      <!-- Un indexer caído NO puede parecer "no hay releases": es el fallo que más caro sale. -->
      <p v-if="down.length" class="rp__warn">
        <Icon name="alert" :size="14" />
        <span>
          {{ down.map(d => d.name).join(', ') }} no responde ahora mismo — Prowlarr lo ha
          desactivado temporalmente. La lista vacía es por eso, no porque no existan torrents.
        </span>
      </p>
      <p v-else-if="viaProwlarr" class="rp__note">
        Sonarr no devolvió nada, así que estos vienen del buscador en crudo de Prowlarr.
      </p>

      <div class="rp__tools">
        <label><input v-model="onlyHealthy" type="checkbox" /> Solo con seeders</label>
        <span class="rp__count">{{ shown.length }} de {{ releases.length }}</span>
        <button class="rp__reload" :disabled="loading" @click="load">
          <Icon name="refresh" :size="14" /> Buscar de nuevo
        </button>
      </div>

      <Spinner v-if="loading" />
      <p v-if="loading" class="rp__slow">Preguntando a los indexers… puede tardar hasta un minuto.</p>

      <ErrorState v-else-if="err" title="No se pudo buscar."
                  hint="Los indexers pueden estar caídos o sin VPN." :detail="err" @retry="load" />
      <EmptyState v-else-if="!shown.length" icon="search" title="Sin torrents"
                  :hint="onlyHealthy ? 'Prueba a quitar el filtro de seeders.' : 'Ningún indexer devolvió resultados.'" />

      <ul v-else class="rp__list">
        <li v-for="r in shown" :key="r.guid" :class="{ bad: r.rejected }">
          <div class="rp__main">
            <p class="rp__title" :data-tip="r.title">
              <span v-if="r.full_season" class="rp__pack">TEMPORADA</span>
              <span v-else-if="r.episodes > 1" class="rp__pack">{{ r.episodes }} EPS</span>
              {{ r.title }}
            </p>
            <p class="rp__meta">
              <b :class="{ zero: !r.seeders }">{{ r.seeders ?? '?' }} seeds</b>
              · {{ formatBytes(r.size) }}
              <template v-if="r.quality"> · {{ r.quality }}</template>
              <template v-if="r.age"> · {{ age(r.age) }}</template>
              <template v-if="r.indexer"> · {{ r.indexer }}</template>
            </p>
            <!-- Sonarr ya lo ha juzgado: decir por qué lo rechazaría evita elegir a ciegas. -->
            <p v-if="r.rejected" class="rp__rej">Sonarr lo rechazaría: {{ r.rejections.join(' · ') }}</p>
          </div>
          <button class="rp__get" :disabled="grabbing === r.guid" @click="grab(r)">
            {{ grabbing === r.guid ? '…' : 'Descargar' }}
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.rp { position: fixed; inset: 0; background: rgba(0, 0, 0, .6); backdrop-filter: blur(3px);
  display: grid; place-items: center; z-index: 60; padding: var(--s-4); }
.rp__box { background: var(--base); border: 1px solid var(--line); border-radius: var(--r-lg);
  width: min(56rem, 100%); max-height: 85vh; overflow: auto; padding: var(--s-4); }
.rp__head { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--s-3); }
.rp__head h3 { margin: 0; font-size: var(--fs-md); }
.rp__sub { margin: .15rem 0 0; color: var(--ink-faint); font-size: var(--fs-sm); }
.rp__x { background: none; border: 0; color: var(--ink-faint); cursor: pointer; }
.rp__warn, .rp__note { display: flex; gap: var(--s-2); align-items: flex-start;
  font-size: var(--fs-sm); margin: var(--s-3) 0 0; }
.rp__warn { color: var(--warn); }
.rp__note { color: var(--ink-faint); }
.rp__tools { display: flex; align-items: center; gap: var(--s-3); margin: var(--s-3) 0;
  font-size: var(--fs-sm); color: var(--ink-faint); }
.rp__count { margin-left: auto; }
.rp__reload { background: none; border: 1px solid var(--line); color: var(--ink-faint);
  border-radius: var(--r-md); padding: var(--s-1) var(--s-2); cursor: pointer; font: inherit;
  font-size: var(--fs-xs); display: flex; align-items: center; gap: .3rem; }
.rp__slow { color: var(--ink-faint); font-size: var(--fs-sm); text-align: center; }
.rp__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.rp__list li { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3);
  background: var(--surface); border-radius: var(--r-sm); }
.rp__list li.bad { opacity: .6; }
.rp__main { min-width: 0; flex: 1; }
.rp__title { margin: 0; font-size: var(--fs-sm); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.rp__pack { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700;
  padding: 1px 0.375rem; border-radius: var(--r-xs); color: var(--cyan); background: var(--cyan-glow);
  border: 1px solid color-mix(in srgb, var(--cyan) 35%, transparent); margin-right: var(--s-2); }
.rp__meta { margin: .15rem 0 0; font-size: var(--fs-xs); color: var(--ink-faint); }
.rp__meta b { color: var(--ink); }
.rp__meta b.zero { color: var(--warn); }
.rp__rej { margin: .15rem 0 0; font-size: var(--fs-xs); color: var(--warn); }
.rp__get { background: var(--azure); color: #fff; border: 0; border-radius: var(--r-sm);
  padding: var(--s-2) var(--s-3); cursor: pointer; font: inherit; font-size: var(--fs-xs); flex: none; }
.rp__get:disabled { opacity: .6; cursor: default; }
</style>
