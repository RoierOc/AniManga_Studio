<script setup>
/* Explorador de carpetas del sistema. Primitiva compartida.
 *
 * Existía sólo para las descargas de anime (`components/anime/FolderPicker.vue`, atado al store
 * de anime). Las carpetas de manga pedían la ruta ESCRITA a mano y ahí es donde se torcía: bajo
 * WSL `D:\Manga` y `/mnt/d/Manga` son la misma carpeta pero `/Manga_Upscaler_project/...` NO está
 * en ningún disco de Windows — vive dentro del .vhdx de WSL, o sea en C:. Tecleando no se
 * distingue; navegando, sí: sólo se puede elegir lo que existe de verdad.
 *
 * Estado local a propósito: es UI efímera, no había razón para que viviera en un store.
 * Usa `/api/anime/browse` (recorrido de directorios genérico, sólo vive ahí por historia).
 */
import { ref, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: 'Elegir carpeta' },
})
const emit = defineEmits(['update:open', 'pick'])

const st = ref({ path: '', win: '', parent: null, items: [], loading: false })

async function browse(path = '') {
  st.value.loading = true
  try {
    const d = await api.get(`/api/anime/browse?path=${encodeURIComponent(path)}`)
    st.value = { path: d.path, win: d.win_path, parent: d.parent, items: d.items || [], loading: false }
  } catch (_) {
    st.value.loading = false
    useUiStore().toast('No se pudo explorar', 'error')
  }
}

// Cada apertura empieza en la lista de discos: heredar la carpeta de la vez anterior es
// justamente cómo se acaba guardando en el sitio equivocado sin mirar.
watch(() => props.open, (v) => { if (v) browse('') })

function close() { emit('update:open', false) }
function use() { emit('pick', { path: st.value.path, win: st.value.win }); close() }
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ov" @click.self="close()">
      <div class="picker">
        <header class="picker__head">
          <span>{{ title }}</span>
          <button class="picker__x" @click="close()"><Icon name="close" :size="16" /></button>
        </header>
        <div class="picker__bar">
          <button class="picker__up" :disabled="st.parent === null" @click="browse(st.parent || '')">
            <Icon name="chevron" :size="14" :style="{ transform: 'rotate(180deg)' }" /> Subir
          </button>
          <code class="picker__path">{{ st.win || 'Discos' }}</code>
        </div>
        <div v-if="st.loading" class="center"><Spinner :size="20" /></div>
        <div v-else class="picker__list">
          <button v-for="it in st.items" :key="it.path" class="picker__item" @click="browse(it.path)">
            <Icon :name="it.is_drive ? 'download' : 'folder'" :size="15" />
            <span>{{ it.name }}</span>
          </button>
          <p v-if="!st.items.length" class="picker__empty">Sin subcarpetas.</p>
        </div>
        <footer class="picker__foot">
          <span class="picker__sel">{{ st.win || '—' }}</span>
          <button class="picker__use" :disabled="!st.win" @click="use()">Usar esta carpeta</button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.picker { width: min(34rem, 100%); max-height: 80vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.picker__head { display: flex; align-items: center; justify-content: space-between; padding: var(--s-4); border-bottom: 1px solid var(--line); font-weight: 600; }
.picker__x { color: var(--ink-faint); }
.picker__x:hover { color: var(--ink); }
.picker__bar { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-bottom: 1px solid var(--line); }
.picker__up { display: inline-flex; align-items: center; gap: 4px; padding: 4px 0.625rem; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); }
.picker__up:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.picker__up:disabled { opacity: .4; cursor: not-allowed; }
.picker__path { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.picker__list { flex: 1; overflow-y: auto; padding: var(--s-2); display: flex; flex-direction: column; gap: 2px; }
.picker__item { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft); text-align: left; transition: background var(--t-fast); }
.picker__item:hover { background: var(--surface-2); color: var(--ink); }
.picker__item :deep(svg) { color: var(--azure); flex-shrink: 0; }
.picker__empty { padding: var(--s-4); text-align: center; color: var(--ink-faint); font-size: var(--fs-sm); }
.center { display: grid; place-items: center; padding: var(--s-6); }
.picker__foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-top: 1px solid var(--line); }
.picker__sel { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.picker__use { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); }
.picker__use:hover:not(:disabled) { background: var(--azure-bright); }
.picker__use:disabled { opacity: .5; cursor: not-allowed; }
</style>
