<script setup>
import { computed, ref, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { formatBytes } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const store = useAnimeStore()
const selector = computed(() => store.batchSelector)
const selected = ref(new Set())

watch(() => selector.value?.files, () => {
  selected.value = new Set((selector.value?.files || []).filter(f => f.selected).map(f => f.id))
}, { immediate: true })

const groups = computed(() => {
  const map = new Map()
  for (const file of selector.value?.files || []) {
    if (!map.has(file.group)) map.set(file.group, [])
    map.get(file.group).push(file)
  }
  return [...map.entries()].sort(([a], [b]) => {
    const an = Number(a.match(/\d+/)?.[0] || 999)
    const bn = Number(b.match(/\d+/)?.[0] || 999)
    return an - bn || a.localeCompare(b)
  }).map(([label, files]) => ({ label, files }))
})
const selectedFiles = computed(() => (selector.value?.files || []).filter(f => selected.value.has(f.id)))
const selectedSize = computed(() => selectedFiles.value.reduce((total, file) => total + (file.size || 0), 0))
const videoCount = computed(() => selectedFiles.value.filter(f => f.is_video).length)

function setSelected(id, value) {
  const next = new Set(selected.value)
  if (value) next.add(id)
  else next.delete(id)
  selected.value = next
}
function toggle(id) { setSelected(id, !selected.value.has(id)) }
function toggleGroup(files) {
  const all = files.every(file => selected.value.has(file.id))
  const next = new Set(selected.value)
  for (const file of files) all ? next.delete(file.id) : next.add(file.id)
  selected.value = next
}
function selectMissing() {
  selected.value = new Set((selector.value?.files || [])
    .filter(file => file.is_video && !file.already_local)
    .map(file => file.id))
}
function selectAll() { selected.value = new Set((selector.value?.files || []).map(file => file.id)) }
function clearAll() { selected.value = new Set() }
function close() { store.closeBatchSelector() }
function retry() {
  const torrent = selector.value?.torrent
  close()
  if (torrent) store.openBatchSelector(torrent)
}
function apply() { store.applyBatchSelection([...selected.value]) }
</script>

<template>
  <Teleport to="body">
    <div v-if="selector" class="batch-modal" role="dialog" aria-modal="true" aria-labelledby="batch-title">
      <button class="batch-modal__scrim" aria-label="Cerrar selector" @click="close" />
      <section class="batch-modal__panel">
        <header class="batch-modal__head">
          <div>
            <h2 id="batch-title">Elegir archivos del batch</h2>
            <p class="batch-modal__sub">{{ selector.torrent?.title }}</p>
          </div>
          <button class="batch-modal__close" aria-label="Cerrar" @click="close"><Icon name="close" :size="18" /></button>
        </header>

        <div v-if="selector.status === 'adding' || selector.status === 'waiting_metadata'" class="batch-state">
          <Spinner :size="22" />
          <strong>{{ selector.status === 'adding' ? 'Añadiendo el torrent pausado…' : 'Esperando metadata de qBittorrent…' }}</strong>
          <p>El torrent permanece pausado hasta que confirmes los archivos.</p>
        </div>
        <ErrorState v-else-if="selector.status === 'error'" :detail="selector.error" @retry="retry" />
        <div v-else-if="selector.status === 'applying'" class="batch-state">
          <Spinner :size="22" />
          <strong>Aplicando prioridades…</strong>
          <p>qBittorrent se reanudará después de validar la selección.</p>
        </div>

        <template v-else>
          <div class="batch-toolbar">
            <div class="batch-toolbar__copy">
              <strong>{{ videoCount }} vídeo{{ videoCount === 1 ? '' : 's' }}</strong>
              <span>{{ formatBytes(selectedSize) }} seleccionados</span>
            </div>
            <div class="batch-toolbar__actions">
              <button @click="selectMissing">Sólo faltantes</button>
              <button @click="selectAll">Todos</button>
              <button @click="clearAll">Ninguno</button>
            </div>
          </div>

          <p class="batch-hint">Los archivos locales aparecen conservados y no se seleccionan de nuevo. Puedes revisar rutas, temporadas y episodios antes de descargar.</p>
          <div class="batch-groups">
            <section v-for="group in groups" :key="group.label" class="batch-group">
              <header class="batch-group__head">
                <button class="batch-group__toggle" @click="toggleGroup(group.files)">
                  <span class="batch-check" :class="{ 'is-on': group.files.every(file => selected.has(file.id)) }">
                    <Icon v-if="group.files.every(file => selected.has(file.id))" name="check" :size="11" />
                  </span>
                  <strong>{{ group.label }}</strong>
                  <span>{{ group.files.length }} archivo{{ group.files.length === 1 ? '' : 's' }}</span>
                </button>
              </header>
              <label v-for="file in group.files" :key="file.id" class="batch-file" :class="{ 'is-selected': selected.has(file.id), 'is-local': file.already_local }">
                <input type="checkbox" :checked="selected.has(file.id)"
                       @click.stop @change.stop="setSelected(file.id, $event.target.checked)" />
                <span class="batch-check" :class="{ 'is-on': selected.has(file.id) }">
                  <Icon v-if="selected.has(file.id)" name="check" :size="11" />
                </span>
                <span class="batch-file__main">
                  <span class="batch-file__name">{{ file.name }}</span>
                  <span class="batch-file__meta">
                    <span v-if="file.season && file.episode">S{{ String(file.season).padStart(2, '0') }} · Episodio {{ file.episode }}</span>
                    <span v-else-if="file.is_video">Vídeo sin temporada identificada</span>
                    <span v-else>Archivo auxiliar</span>
                    <span>· {{ formatBytes(file.size) }}</span>
                  </span>
                </span>
                <span class="batch-file__status">
                  <span v-if="file.already_local" class="batch-badge batch-badge--local">Ya local</span>
                  <span v-else-if="file.progress >= 100" class="batch-badge batch-badge--done">Completo</span>
                  <span v-else-if="file.progress > 0" class="batch-badge">{{ Math.round(file.progress) }}%</span>
                  <span v-else class="batch-badge">Pendiente</span>
                </span>
              </label>
            </section>
          </div>

          <footer class="batch-modal__foot">
            <button class="batch-cancel" @click="close">Cancelar</button>
            <button class="batch-confirm" :disabled="!selectedFiles.length" @click="apply">
              <Icon name="download" :size="15" /> Descargar selección
            </button>
          </footer>
        </template>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.batch-modal { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: var(--s-5); }
.batch-modal__scrim { position: absolute; inset: 0; z-index: 0; background: rgba(3, 6, 13, .78); backdrop-filter: blur(5px); }
.batch-modal__panel { position: relative; z-index: 1; width: min(58rem, 100%); max-height: min(88vh, 54rem); display: flex; flex-direction: column; overflow: hidden; background: var(--base); border: 1px solid var(--line-strong); border-radius: var(--r-lg); box-shadow: 0 1rem 4rem rgba(0,0,0,.48); }
.batch-modal__head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-4); padding: var(--s-5) var(--s-6); border-bottom: 1px solid var(--line); }
.batch-modal__head h2 { font-size: var(--fs-xl); }
.batch-modal__sub { max-width: 60ch; margin-top: var(--s-1); color: var(--ink-soft); font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.batch-modal__close { display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: var(--r-sm); color: var(--ink-soft); }
.batch-modal__close:hover { color: var(--ink); background: var(--surface-2); }
.batch-state { display: grid; place-items: center; gap: var(--s-3); min-height: 18rem; padding: var(--s-8); color: var(--ink-soft); text-align: center; }
.batch-state strong { color: var(--ink); }
.batch-state p { margin: 0; font-size: var(--fs-sm); }
.batch-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--s-4); padding: var(--s-4) var(--s-6); background: var(--surface); border-bottom: 1px solid var(--line); }
.batch-toolbar__copy { display: flex; align-items: baseline; gap: var(--s-3); color: var(--ink-soft); font-size: var(--fs-xs); }
.batch-toolbar__copy strong { color: var(--azure-bright); font-family: var(--font-mono); }
.batch-toolbar__actions { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.batch-toolbar__actions button { padding: var(--s-1) var(--s-2); color: var(--ink-soft); border: 1px solid var(--line); border-radius: var(--r-sm); font-size: var(--fs-xs); }
.batch-toolbar__actions button:hover { color: var(--azure-bright); border-color: var(--azure); }
.batch-hint { margin: 0; padding: var(--s-3) var(--s-6); color: var(--ink-faint); font-size: var(--fs-xs); line-height: 1.5; }
.batch-groups { overflow: auto; padding: 0 var(--s-6) var(--s-4); }
.batch-group { border-top: 1px solid var(--line); }
.batch-group__head { padding: var(--s-3) 0; }
.batch-group__toggle { display: flex; align-items: center; gap: var(--s-2); color: var(--ink); font-size: var(--fs-sm); }
.batch-group__toggle span:last-child { color: var(--ink-faint); font-size: var(--fs-xs); font-weight: 400; }
.batch-check { display: inline-grid; place-items: center; width: 1.1rem; height: 1.1rem; flex: 0 0 auto; color: #fff; border: 1px solid var(--line-strong); border-radius: var(--r-xs); background: var(--surface-2); }
.batch-check.is-on { border-color: var(--azure); background: var(--azure); }
.batch-file { position: relative; display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); cursor: pointer; }
.batch-file:hover { background: var(--surface); }
.batch-file.is-selected { background: var(--azure-haze); }
.batch-file.is-local { opacity: .7; }
.batch-file input { position: absolute; left: var(--s-3); width: 1.5rem; height: 1.5rem; z-index: 2; margin: 0; opacity: 0; cursor: pointer; }
.batch-file__main { min-width: 0; flex: 1; }
.batch-file__name { display: block; color: var(--ink); font-size: var(--fs-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.batch-file__meta { display: flex; gap: var(--s-2); margin-top: 2px; color: var(--ink-faint); font-family: var(--font-mono); font-size: var(--fs-2xs); }
.batch-file__status { flex: 0 0 auto; }
.batch-badge { padding: 2px var(--s-2); color: var(--ink-soft); border-radius: var(--r-pill); background: var(--surface-3); font-family: var(--font-mono); font-size: var(--fs-2xs); }
.batch-badge--local { color: var(--jade); background: color-mix(in srgb, var(--jade) 14%, transparent); }
.batch-badge--done { color: var(--azure-bright); background: var(--azure-haze); }
.batch-modal__foot { display: flex; justify-content: flex-end; gap: var(--s-3); padding: var(--s-4) var(--s-6); border-top: 1px solid var(--line); background: var(--surface); }
.batch-cancel, .batch-confirm { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 600; }
.batch-cancel { color: var(--ink-soft); border: 1px solid var(--line-strong); }
.batch-confirm { color: #fff; background: var(--azure); }
.batch-confirm:hover:not(:disabled) { background: var(--azure-bright); }
.batch-confirm:disabled { cursor: not-allowed; opacity: .45; }
@media (max-width: 640px) { .batch-modal { padding: 0; } .batch-modal__panel { max-height: 100vh; height: 100%; border-radius: 0; } .batch-toolbar, .batch-modal__head, .batch-modal__foot { padding-left: var(--s-4); padding-right: var(--s-4); } .batch-groups { padding-left: var(--s-4); padding-right: var(--s-4); } .batch-toolbar { align-items: flex-start; flex-direction: column; } .batch-file__status { display: none; } }
</style>
