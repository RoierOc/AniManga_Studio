<script setup>
/* Ajustes → Novelas: qué fuentes (plugins LNReader) se consultan al buscar una novela.
 * Sin configurar nada funciona: se usa la lista CURADA por idioma. Este panel es para
 * quien quiera añadir o quitar fuentes concretas. */
import { computed, onMounted, ref } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const ui = useUiStore()
const loading = ref(true)
const saving = ref(false)
const plugins = ref([])
const langs = ref(['en', 'es'])
const selected = ref([])       // vacío = usar la curada
const curated = ref({})
const filter = ref('')

// Idiomas tal y como los etiqueta el índice de plugins, mapeados a nuestros códigos.
const LANG_NAME = { en: 'English', es: 'Español' }

const visible = computed(() => {
  const names = langs.value.map(l => LANG_NAME[l]).filter(Boolean)
  const q = filter.value.toLowerCase().trim()
  return plugins.value
    .filter(p => !names.length || names.includes(p.lang))
    .filter(p => !q || p.id.toLowerCase().includes(q) || (p.name || '').toLowerCase().includes(q))
})

const usingCurated = computed(() => selected.value.length === 0)

async function load() {
  loading.value = true
  try {
    const [prefs, list] = await Promise.all([
      api.get('/api/novels/prefs'),
      api.get('/api/novels/plugins'),
    ])
    langs.value = prefs.langs || ['en', 'es']
    selected.value = prefs.pluginIds || []
    curated.value = prefs.curated || {}
    plugins.value = list || []
  } catch (_) {
    ui.toast('No se pudieron cargar las fuentes de novelas', 'error')
  } finally { loading.value = false }
}

function toggle(id) {
  // Primer clic partiendo de "curada": se materializa la curada y se quita/añade sobre ella.
  const base = usingCurated.value
    ? langs.value.flatMap(l => curated.value[l] || [])
    : selected.value
  selected.value = base.includes(id) ? base.filter(x => x !== id) : [...base, id]
}

function toggleLang(l) {
  langs.value = langs.value.includes(l) ? langs.value.filter(x => x !== l) : [...langs.value, l]
}

function resetCurated() { selected.value = [] }

async function save() {
  saving.value = true
  try {
    await api.post('/api/novels/prefs', { langs: langs.value, pluginIds: selected.value })
    ui.toast('Fuentes de novelas guardadas', 'ok')
  } catch (_) {
    ui.toast('No se pudieron guardar las fuentes', 'error')
  } finally { saving.value = false }
}

function isOn(id) {
  return usingCurated.value
    ? langs.value.some(l => (curated.value[l] || []).includes(id))
    : selected.value.includes(id)
}

onMounted(load)
</script>

<template>
  <section class="ns">
    <header class="ns__head">
      <h3 class="ns__title"><Icon name="book" :size="16" /> Fuentes de novelas</h3>
      <p class="ns__sub">
        Al buscar una novela se consultan estas fuentes en paralelo. Sin tocar nada se usa una
        selección recomendada por idioma.
      </p>
    </header>

    <div v-if="loading" class="ns__loading"><Spinner :size="18" /> Cargando fuentes…</div>

    <template v-else>
      <div class="ns__row">
        <span class="ns__lbl">Idiomas</span>
        <div class="ns__seg">
          <button v-for="(name, code) in LANG_NAME" :key="code"
                  :class="{ 'is-on': langs.includes(code) }" @click="toggleLang(code)">{{ name }}</button>
        </div>
      </div>

      <div class="ns__row">
        <span class="ns__lbl">Selección</span>
        <span class="ns__mode">
          {{ usingCurated ? 'Recomendada por idioma' : `${selected.length} fuentes elegidas` }}
          <button v-if="!usingCurated" class="ns__reset" @click="resetCurated">volver a la recomendada</button>
        </span>
      </div>

      <input v-model="filter" class="ns__filter" type="search" placeholder="Filtrar fuentes…" />

      <div class="ns__grid">
        <button v-for="p in visible" :key="p.id" class="ns__item" :class="{ 'is-on': isOn(p.id) }"
                @click="toggle(p.id)">
          <Icon :name="isOn(p.id) ? 'check' : 'plus'" :size="13" />
          <span class="ns__name">{{ p.name || p.id }}</span>
          <span class="ns__lang">{{ p.lang }}</span>
        </button>
      </div>
      <p v-if="!visible.length" class="ns__empty">Ninguna fuente coincide con el filtro.</p>

      <button class="ns__save" :disabled="saving" @click="save">
        <Icon name="check" :size="15" /> {{ saving ? 'Guardando…' : 'Guardar fuentes' }}
      </button>
    </template>
  </section>
</template>

<style scoped>
.ns { display: flex; flex-direction: column; gap: var(--s-3); }
.ns__title { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-md); font-weight: 600; color: var(--ink); }
.ns__sub { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: var(--s-1); }
.ns__loading { display: flex; align-items: center; gap: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); }

.ns__row { display: flex; align-items: center; gap: var(--s-3); }
.ns__lbl { font-size: var(--fs-2xs); text-transform: uppercase; letter-spacing: .05em; color: var(--ink-faint); min-width: 5rem; }
.ns__mode { font-size: var(--fs-xs); color: var(--ink-soft); display: inline-flex; align-items: center; gap: var(--s-2); }
.ns__reset { font-size: var(--fs-2xs); color: var(--azure-bright); text-decoration: underline; }

.ns__seg { display: flex; gap: 2px; padding: 2px; border-radius: var(--r-sm); background: var(--void); border: 1px solid var(--line); }
.ns__seg button { padding: 4px var(--s-3); border-radius: 0.3125rem; font-size: var(--fs-2xs); color: var(--ink-soft); transition: all var(--t-fast); }
.ns__seg button.is-on { background: var(--azure-haze); color: var(--azure-bright); font-weight: 600; }

.ns__filter { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--void);
  border: 1px solid var(--line); color: var(--ink); font-size: var(--fs-xs); }
.ns__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: var(--s-2);
  max-height: 22rem; overflow-y: auto; }
.ns__item { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) var(--s-3);
  border: 1px solid var(--line); border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft);
  text-align: left; transition: all var(--t-fast); }
.ns__item:hover { color: var(--ink); }
.ns__item.is-on { color: var(--azure-bright); background: var(--azure-haze); border-color: var(--azure-bright); }
.ns__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ns__lang { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .6; }
.ns__empty { font-size: var(--fs-xs); color: var(--ink-faint); }

.ns__save { align-self: flex-start; display: inline-flex; align-items: center; gap: var(--s-2);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 600;
  color: var(--azure-bright); background: var(--azure-haze); border: 1px solid var(--line); }
.ns__save:disabled { opacity: .6; }
</style>
