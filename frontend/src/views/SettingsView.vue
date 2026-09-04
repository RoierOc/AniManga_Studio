<script setup>
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useAnimeStore } from '@/stores/anime'
import { useSettingsStore } from '@/stores/settings'
import { formatBytes } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import SubStyleCard from '@/components/anime/SubStyleCard.vue'
import IntegrityCard from '@/components/anime/IntegrityCard.vue'
import MangaRootsCard from '@/components/settings/MangaRootsCard.vue'
import HealthCard from '@/components/settings/HealthCard.vue'
import NovelSourcesCard from '@/components/settings/NovelSourcesCard.vue'
import Spinner from '@/components/ui/Spinner.vue'
import FolderPicker from '@/components/ui/FolderPicker.vue'
import Select from '@/components/ui/Select.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { filterSettingsSections } from '@/lib/settingsSearch'

const ui = useUiStore()
const manga = useMangaStore()
const anime = useAnimeStore()
const settings = useSettingsStore()

// Settings categories (left rail). Persisted so you land where you left off.
const TABS = [
  { id: 'general', label: 'General', icon: 'spark', description: 'Escalado 4K y biblioteca oculta', keywords: ['modelo', 'modo eco', 'código secreto', 'manga'] },
  { id: 'anime', label: 'Anime', icon: 'film', description: 'Descargas, qBittorrent y subtítulos', keywords: ['carpeta', 'ruta', 'episodios', 'servidor'] },
  { id: 'conexiones', label: 'Conexiones', icon: 'globe', description: 'Claves API e importación de .env', keywords: ['servicios', 'api', 'token', 'credenciales'] },
  { id: 'salud', label: 'Salud', icon: 'check', description: 'Integridad, fuentes y diagnósticos', keywords: ['reparar', 'verificar', 'errores'] },
  { id: 'novelas', label: 'Novelas', icon: 'book', description: 'Fuentes e idiomas', keywords: ['capítulos', 'literatura', 'web'] },
  { id: 'almacenamiento', label: 'Almacenamiento', icon: 'folder', description: 'Raíces, espacio de disco y caché', keywords: ['carpetas', 'disco', 'limpiar', 'originales', '4k'] },
  { id: 'copia', label: 'Copia y sync', icon: 'refresh', description: 'Respaldo, Git, Drive y biblioteca móvil', keywords: ['sincronización', 'backup', 'exportar', 'remoto', 'webdav', 'android'] },
  /* La Cocina del diseño y el modo QA son andamiaje de desarrollo y vivían en «General», que es
     la pestaña que se abre por defecto: dos de las tres tarjetas que veías al entrar en Ajustes
     eran herramientas internas, y ocupaban más sitio que el único ajuste de verdad (el 4K). */
  { id: 'interno', label: 'Interno', icon: 'palette', description: 'Cocina del diseño y modo QA', keywords: ['testing', 'traducción', 'debug'] },
]
// En el store (`ui.tabs.set`), no en un ref local: el historial la ve, así que "atrás" vuelve a la
// pestaña de Ajustes en la que estabas en lugar de sacarte de Ajustes.
const tab = computed(() => ui.tabs.set)
const setTab = (id) => ui.setTab('set', id)
const settingsQuery = ref('')
const visibleTabs = computed(() => filterSettingsSections(settingsQuery.value, TABS))
const hasSettingsQuery = computed(() => Boolean(settingsQuery.value.trim()))
watch(visibleTabs, (tabs) => {
  if (hasSettingsQuery.value && tabs.length && !tabs.some((item) => item.id === tab.value)) setTab(tabs[0].id)
})
function clearSettingsSearch() { settingsQuery.value = '' }

const dlPath = ref('')
const dlBrowse = ref(false)
watch(() => anime.dlSettings.download_path, (v) => { dlPath.value = v || '' }, { immediate: true })

const qaInfo = ref({ bytes: 0, flags: 0 })
async function loadQa() { qaInfo.value = await manga.qaSize() }
async function clearQa() {
  if (!await ui.confirm({ title: 'Borrar datos QA', danger: true, body: '¿Borrar todos los datos QA (artefactos y páginas marcadas)?', confirmLabel: 'Borrar' })) return
  await manga.qaClear(); loadQa()
}

// ── Biblioteca oculta (descubrimiento discreto) ──────────────────────────────
// La tarjeta NO se muestra en Ajustes salvo que: (a) el modo oculto ya esté
// activo, o (b) se haya "revelado". Se revela de dos formas discretas:
//   · Tecleando la combinación numérica en cualquier parte de Ajustes (fuera de
//     un campo) → activa el modo directamente (recarga).
//   · 7 toques en el título "CONFIGURACIÓN" → revela el formulario (necesario la
//     primera vez, cuando aún no hay código que teclear).
const hidCode = ref('')        // código a introducir (toggle) o nuevo código (set)
const hidCurrent = ref('')     // código actual (sólo al cambiar uno ya existente)
const hidMsg = ref('')         // mensaje de error/estado bajo el input
const hidBusy = ref(false)
const hidRevealed = ref(false) // knock: revela la tarjeta aunque el modo no esté activo
const hidCardShown = computed(() => ui.hiddenModeActive || hidRevealed.value)

// Knock en el título: 7 toques seguidos revelan la tarjeta.
let _knock = 0, _knockTimer = null
function hidKnock() {
  clearTimeout(_knockTimer)
  _knockTimer = setTimeout(() => { _knock = 0 }, 1500)
  if (++_knock >= 7) { _knock = 0; hidRevealed.value = true; setTab('general') }
}

// Buffer de dígitos tecleados en Ajustes (fuera de inputs). Tras una pausa breve,
// si coincide con el código configurado, alterna el modo (una única prueba por
// pausa → sin brute-force ni bloqueo del backend por tecleo legítimo).
let _digits = '', _digitTimer = null
function onSettingsKey(e) {
  const el = e.target
  if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return
  if (!ui.hiddenConfigured) return          // sin código aún → sólo el knock revela
  if (e.key < '0' || e.key > '9') return
  _digits = (_digits + e.key).slice(-12)
  clearTimeout(_digitTimer)
  _digitTimer = setTimeout(async () => {
    const seq = _digits; _digits = ''
    if (seq.length >= 3) await ui.toggleHiddenMode(seq)   // en éxito recarga; en fallo, silencio
  }, 900)
}
onMounted(() => window.addEventListener('keydown', onSettingsKey))
onUnmounted(() => { window.removeEventListener('keydown', onSettingsKey); clearTimeout(_digitTimer); clearTimeout(_knockTimer) })
async function saveHiddenCode() {
  if (!hidCode.value.trim()) { hidMsg.value = 'Introduce un código'; return }
  hidBusy.value = true; hidMsg.value = ''
  const r = await ui.setHiddenCode(hidCode.value.trim(), hidCurrent.value.trim())
  hidBusy.value = false
  if (r.ok) { hidCode.value = ''; hidCurrent.value = ''; ui.toast('Código de biblioteca oculta guardado', 'ok') }
  else hidMsg.value = r.error || 'No se pudo guardar'
}
async function toggleHidden() {
  if (!hidCode.value.trim()) { hidMsg.value = 'Introduce el código'; return }
  hidBusy.value = true; hidMsg.value = ''
  const r = await ui.toggleHiddenMode(hidCode.value.trim())
  hidBusy.value = false
  // En éxito la página se recarga (toggleHiddenMode hace location.reload); sólo
  // llegamos aquí en error.
  if (!r.ok) hidMsg.value = r.retry_after ? `Demasiados intentos, espera ${Math.ceil(r.retry_after)}s` : (r.error || 'Código incorrecto')
}

// ── Almacenamiento ──────────────────────────────────────────────────────────
const storage = ref(null)
const storageLoading = ref(false)
const storageBusy = ref('')
const stgFilter = ref('all')   // 'all' | 'manga' | 'anime'
async function loadStorage() {
  storageLoading.value = true
  try { storage.value = await api.get('/api/storage/summary') }
  catch (_) { storage.value = null }
  finally { storageLoading.value = false }
}
// Series filtradas por tipo + subtotales por sección, para ver junto o por separado.
const stgSeries = computed(() => {
  const list = storage.value?.series || []
  const f = stgFilter.value
  return f === 'all' ? list : list.filter((s) => (s.kind || 'manga') === f)
})
const stgCounts = computed(() => {
  const list = storage.value?.series || []
  return {
    manga: list.filter((s) => (s.kind || 'manga') === 'manga').length,
    anime: list.filter((s) => s.kind === 'anime').length,
  }
})
async function purgeStreamCache() {
  storageBusy.value = 'stream_cache'
  try {
    const res = await api.post('/api/storage/purge', { target: 'stream_cache' })
    ui.toast(`Liberado ${formatBytes(res.freed || 0)}`, 'ok')
    await loadStorage()
  } catch (_) { ui.toast('No se pudo liberar espacio', 'error') }
  finally { storageBusy.value = '' }
}
async function purgeStorageCache(target, label) {
  const bytes = target === 'image_cache' ? storage.value?.totals.image_cache : storage.value?.totals.export_orphan
  if (!bytes) return
  if (!await ui.confirm({
    title: `Limpiar ${label}`,
    danger: false,
    body: `Se liberarán hasta ${formatBytes(bytes)}. El contenido se puede regenerar.`,
    confirmLabel: 'Limpiar',
  })) return
  storageBusy.value = target
  try {
    const res = await api.post('/api/storage/purge', { target })
    ui.toast(`Liberado ${formatBytes(res.freed || 0)}`, 'ok')
    await loadStorage()
  } catch (e) { ui.toast(e?.message || 'No se pudo liberar espacio', 'error') }
  finally { storageBusy.value = '' }
}

onMounted(() => {
  if (!Object.keys(manga.models).length) manga.loadModels()
  manga.loadDestinations()
  anime.loadDlSettings()
  anime.checkQbt()
  loadQa()
  loadStorage()
  settings.loadKeys()
  settings.loadSyncStatus()
  ui.refreshHiddenStatus()
  loadPair()
})

// ── API keys ──────────────────────────────────────────────────────────────
const keyEdits = ref({})   // { KEY: typedValue } — only typed fields are sent
const envInput = ref(null)
function triggerEnvImport() { envInput.value?.click() }
async function onEnvFile(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    const res = await settings.importEnv(await file.text())
    ui.toast(res.detected?.length ? `Importadas ${res.detected.length} clave(s): ${res.detected.join(', ')}` : 'No se detectaron claves conocidas en el .env', res.detected?.length ? 'ok' : 'error', 5000)
  } catch (_) { ui.toast('No se pudo leer el .env', 'error') }
}
async function saveGroup(fields) {
  const values = {}
  for (const f of fields) if (keyEdits.value[f.key] != null && keyEdits.value[f.key] !== '') values[f.key] = keyEdits.value[f.key]
  if (!Object.keys(values).length) { ui.toast('Nada que guardar en este grupo', 'error'); return }
  try {
    await settings.saveKeys(values)
    for (const k of Object.keys(values)) delete keyEdits.value[k]
    ui.toast('Claves guardadas · se aplican al instante', 'ok')
  } catch (_) { ui.toast('No se pudieron guardar las claves', 'error') }
}

// ── Sync (Guardar / Recuperar biblioteca) ─────────────────────────────────
const remoteUrl = ref('')
const patInput = ref('')
watch(() => settings.sync, (s) => { if (s?.remote_set && !remoteUrl.value) remoteUrl.value = '' }, { deep: true })

function relTime(ts) {
  if (!ts) return 'nunca'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return 'hace segundos'
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`
  return `hace ${Math.floor(s / 86400)} d`
}
async function configureSync() {
  try {
    await settings.configureSync(remoteUrl.value.trim(), patInput.value.trim())
    patInput.value = ''
    ui.toast('Remoto configurado', 'ok')
  } catch (e) { ui.toast(e?.message || 'No se pudo configurar', 'error', 5000) }
}
async function saveLib() {
  const r = await settings.saveLibrary()
  if (r.error) return ui.toast(r.error, 'error', 6000)
  ui.toast(r.changed ? 'Biblioteca guardada en la nube ✓' : 'Sin cambios · ya estaba al día', 'ok')
}
async function restoreLib() {
  if (!await ui.confirm({ title: 'Recuperar biblioteca', body: '¿Recuperar la biblioteca desde la nube?\nSe fusiona con lo local (no borra tu progreso actual).', confirmLabel: 'Recuperar' })) return
  const r = await settings.restoreLibrary()
  if (r.error) return ui.toast(r.error, 'error', 6000)
  const m = r.manga || { added: 0, merged: 0 }, a = r.anime || { added: 0, merged: 0 }
  ui.toast(`Recuperado: ${m.added + m.merged} manga (${m.added} nuevos), ${a.added + a.merged} anime (${a.added} nuevos). Recargá Biblioteca/Mi Anime.`, 'ok', 6000)
  anime.loadLibrary(true)
}

const phoneCopied = ref(false)
function copyPhoneUrl() {
  if (!manga.webdav.phoneUrl) return
  navigator.clipboard.writeText(manga.webdav.phoneUrl).then(() => {
    phoneCopied.value = true
    setTimeout(() => { phoneCopied.value = false }, 2000)
  })
}

/* Acceso remoto — el token con el que un móvil habla con este PC. Se pide sólo al abrir Ajustes
   y `/api/pair` únicamente responde a peticiones locales, así que no puede filtrarse por la red. */
const pair = ref({ url: '', token: '' })
const pairShown = ref(false)
const pairCopied = ref(false)
async function loadPair() {
  try { pair.value = await api.get('/api/pair') } catch (_) { /* servidor viejo: la tarjeta se queda vacía */ }
}
function copyPair() {
  navigator.clipboard.writeText(pair.value.token || '').then(() => {
    pairCopied.value = true
    setTimeout(() => { pairCopied.value = false }, 2000)
  })
}
async function rotatePair() {
  if (!await ui.confirm({
    title: 'Cambiar el token',
    body: 'Los dispositivos ya emparejados dejarán de tener acceso y habrá que volver a emparejarlos.',
    confirmLabel: 'Cambiar',
  })) return
  try {
    pair.value = { ...pair.value, ...(await api.post('/api/pair/rotate')) }
    pairShown.value = true
    ui.toast('Token cambiado · vuelve a emparejar tus dispositivos', 'ok', 5000)
  } catch (e) { ui.toast(e?.message || 'No se pudo cambiar', 'error', 5000) }
}

const importInput = ref(null)
function triggerImport() { importInput.value?.click() }

async function onImportFile(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    const res = await api.post('/api/backup/import', data)
    ui.toast(
      `Importado: ${res.manga.added + res.manga.merged} mangas (${res.manga.added} nuevos), ` +
      `${res.anime.added + res.anime.merged} animes (${res.anime.added} nuevos). Recargá Biblioteca/Mi Anime para verlo.`,
      'ok', 6000
    )
    anime.loadLibrary(true)
  } catch (err) {
    ui.toast('Archivo de respaldo inválido', 'error')
  }
}
</script>

<template>
  <div class="set">
    <header class="set__head">
      <p class="eyebrow" @click="hidKnock"><span class="tick" /> CONFIGURACIÓN</p>
      <h1>Ajustes</h1>
      <label class="set__search">
        <Icon name="search" :size="16" />
        <input v-model="settingsQuery" type="search" placeholder="Buscar un ajuste…"
               aria-label="Buscar un ajuste" spellcheck="false" />
        <button v-if="hasSettingsQuery" type="button" class="set__search-clear"
                aria-label="Limpiar búsqueda" data-tip="Limpiar búsqueda" @click="clearSettingsSearch">
          <Icon name="close" :size="14" />
        </button>
      </label>
      <p v-if="hasSettingsQuery" class="set__search-hint">
        {{ visibleTabs.length ? `${visibleTabs.length} sección(es) encontrada(s)` : 'Sin secciones coincidentes' }}
      </p>
    </header>

    <div class="set__shell">
      <nav class="set__rail">
        <button v-for="t in visibleTabs" :key="t.id" type="button" class="set__tab" :class="{ 'is-active': tab === t.id }" @click="setTab(t.id)">
          <Icon :name="t.icon" :size="16" /><span>{{ t.label }}</span>
        </button>
      </nav>

      <div class="set__pane">
        <EmptyState v-if="hasSettingsQuery && !visibleTabs.length" icon="search"
                    title="No encontramos ese ajuste"
                    hint="Prueba con palabras como carpeta, API, idioma, disco o respaldo." />
        <template v-else>
        <!-- General -->
        <div v-show="tab === 'general'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="spark" :size="18" /></span>
            <div><h2>General</h2><p>Escalado 4K de manga y herramientas de traducción.</p></div>
          </div>
<!-- Upscaling -->
    <section class="card">
      <div class="card__title"><Icon name="spark" :size="16" /> Escalado 4K (manga)</div>
      <div class="row">
        <label class="fld">
          <span>Modelo</span>
          <Select block :model-value="manga.activeModel" aria-label="Modelo de escalado"
                  :options="Object.entries(manga.models).map(([key, label]) => ({ value: key, label, hint: manga.modelsColor[key] ? 'Color' : 'B&N' }))"
                  @change="manga.setModel($event)" />
        </label>
        <label class="fld fld--chk">
          <span>Modo eco <em>· deja correr MPV mientras escala</em></span>
          <input type="checkbox" :checked="manga.eco" @change="manga.setEco($event.target.checked)" />
        </label>
      </div>
    </section>

    

<!-- Biblioteca oculta (oculta salvo modo activo o revelada por gesto) -->
    <section v-if="hidCardShown" class="card" :class="{ 'card--hid': ui.hiddenModeActive }">
      <div class="card__title">
        <Icon name="library" :size="16" /> Biblioteca oculta
        <span v-if="ui.hiddenModeActive" class="tag tag--hid">ACTIVA</span>
      </div>
      <p class="hint">
        Un espacio de biblioteca <b>totalmente independiente</b> (manga y anime). Lo que añadas
        aquí no aparece nunca en la biblioteca principal, ni en su almacenamiento, mientras el
        modo oculto esté desactivado. El modo <b>siempre vuelve a normal</b> al reiniciar la app.
      </p>

      <!-- Sin código configurado → configurarlo -->
      <template v-if="!ui.hiddenConfigured">
        <label class="fld">
          <span>Crear código secreto <em>· combinación numérica, ej. 77788</em></span>
          <input v-model="hidCode" type="password" inputmode="numeric" class="mono" autocomplete="off"
                 placeholder="Nuevo código" @keyup.enter="saveHiddenCode" />
        </label>
        <div class="row">
          <button class="btn btn--accent" :disabled="hidBusy" @click="saveHiddenCode">Guardar código</button>
        </div>
      </template>

      <!-- Con código → activar/desactivar el modo -->
      <template v-else>
        <div class="hid__state" :class="{ 'is-on': ui.hiddenModeActive }">
          <span class="dot" />
          {{ ui.hiddenModeActive ? 'Estás en la biblioteca oculta' : 'Estás en la biblioteca principal' }}
        </div>
        <label class="fld">
          <span>Código secreto</span>
          <input v-model="hidCode" type="password" inputmode="numeric" class="mono" autocomplete="off"
                 :placeholder="ui.hiddenModeActive ? 'Código para volver a la principal' : 'Código para entrar'"
                 @keyup.enter="toggleHidden" />
        </label>
        <div class="row">
          <button class="btn" :class="ui.hiddenModeActive ? 'btn--danger' : 'btn--accent'" :disabled="hidBusy" @click="toggleHidden">
            {{ ui.hiddenModeActive ? 'Volver a la biblioteca principal' : 'Entrar en la biblioteca oculta' }}
          </button>
        </div>
        <details class="hid__change">
          <summary>Cambiar el código</summary>
          <label class="fld">
            <span>Código actual</span>
            <input v-model="hidCurrent" type="password" inputmode="numeric" class="mono" autocomplete="off" placeholder="Actual" />
          </label>
          <label class="fld">
            <span>Nuevo código</span>
            <input v-model="hidCode" type="password" inputmode="numeric" class="mono" autocomplete="off" placeholder="Nuevo" />
          </label>
          <div class="row">
            <button class="btn btn--accent btn--xs" :disabled="hidBusy" @click="saveHiddenCode">Guardar nuevo código</button>
          </div>
        </details>
      </template>

      <p v-if="hidMsg" class="hid__msg">{{ hidMsg }}</p>
    </section>


        </div>

        <!-- Anime -->
        <div v-show="tab === 'anime'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="film" :size="18" /></span>
            <div><h2>Anime</h2><p>Carpeta de descargas, conexión con qBittorrent y estilo de subtítulos.</p></div>
          </div>

    <SubStyleCard />
    <div class="sep" />
<!-- Anime downloads -->
    <section class="card">
      <div class="card__title"><Icon name="download" :size="16" /> Descargas de anime</div>
      <label class="fld">
        <span>Carpeta de descargas <em>· cada serie en su subcarpeta; útil para usar otro disco</em></span>
        <div class="inline">
          <input v-model="dlPath" class="mono" :placeholder="anime.dlSettings.qbt_default || 'D:\\Anime'" spellcheck="false" />
          <button class="btn" @click="dlBrowse = true"><Icon name="folder" :size="14" /> Explorar</button>
          <button class="btn btn--accent" @click="anime.saveDlPath(dlPath)">Guardar</button>
        </div>
        <p class="hint">
          <template v-if="anime.dlSettings.download_path">Actual: <code>{{ anime.dlSettings.download_path }}</code></template>
          <template v-else>Usando la carpeta de qBittorrent<template v-if="anime.dlSettings.qbt_default">: <code>{{ anime.dlSettings.qbt_default }}</code></template>.</template>
        </p>
      </label>

      <div class="sep" />
      <div class="qbt">
        <span class="qbt__status" :class="{ 'is-on': anime.qbt.connected }">
          <span class="dot" /> {{ anime.qbt.connected ? `qBittorrent ${anime.qbt.version}` : 'qBittorrent desconectado' }}
        </span>
        <div v-if="!anime.qbt.connected" class="inline inline--wrap">
          <input v-model="anime.qbt.url" placeholder="http://localhost:8080" />
          <input v-model="anime.qbt.username" placeholder="Usuario" />
          <input v-model="anime.qbt.password" type="password" placeholder="Contraseña" />
          <button class="btn btn--accent" @click="anime.configureQbt()">Conectar</button>
        </div>
      </div>
    </section>

    
        </div>

        <!-- Conexiones -->
        <div v-show="tab === 'conexiones'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="globe" :size="18" /></span>
            <div><h2>Conexiones</h2><p>Claves API de cada servicio. Se aplican al instante, sin reiniciar.</p></div>
          </div>
<!-- Conexiones y claves API -->
    <section class="card">
      <div class="card__title">
        <Icon name="settings" :size="16" /> Conexiones y claves API
        <button class="btn btn--xs stg__refresh" @click="triggerEnvImport" data-tip="Importar un archivo .env">
          <Icon name="upload" :size="13" /> Importar .env
        </button>
        <input ref="envInput" type="file" accept=".env,text/plain" hidden @change="onEnvFile" />
      </div>
      <p class="hint">Configura aquí las claves de cada servicio. Se aplican al instante, sin reiniciar. Las claves nunca se muestran completas ni se incluyen en la copia a la nube.</p>

      <div v-if="settings.keysLoading && !settings.keyGroups.length" class="stg__loading"><Spinner :size="20" /> Cargando…</div>

      <div v-for="grp in settings.keyGroups" :key="grp.group" class="keygrp">
        <div class="keygrp__head">
          <span class="keygrp__name">{{ grp.group }}</span>
          <span class="keygrp__state" :class="{ 'is-on': grp.fields.some(f => f.set) }">
            <span class="dot" /> {{ grp.fields.filter(f => f.set).length }}/{{ grp.fields.length }}
          </span>
        </div>
        <div class="keygrp__fields">
          <label v-for="f in grp.fields" :key="f.key" class="fld">
            <span>{{ f.label }} <em v-if="f.set && f.secret" class="keygrp__hint">· configurada {{ f.hint }}</em></span>
            <input
              v-if="f.secret"
              type="password"
              :placeholder="f.set ? '•••••••• (dejar en blanco para conservar)' : (f.placeholder || '')"
              v-model="keyEdits[f.key]"
              autocomplete="off" spellcheck="false" class="mono"
            />
            <input
              v-else
              type="text"
              :placeholder="f.placeholder || ''"
              :value="keyEdits[f.key] != null ? keyEdits[f.key] : (f.value || '')"
              @input="keyEdits[f.key] = $event.target.value"
              spellcheck="false" class="mono"
            />
          </label>
        </div>
        <div class="keygrp__foot">
          <button class="btn btn--accent btn--xs" :disabled="settings.keysSaving" @click="saveGroup(grp.fields)">Guardar {{ grp.group }}</button>
        </div>
      </div>
    </section>

    
        </div>

        <!-- Salud de la biblioteca -->
        <div v-show="tab === 'salud'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="check" :size="18" /></span>
            <div><h2>Salud de la biblioteca</h2><p>Detecta y repara fuentes cruzadas, identidades sin verificar y fallos recientes.</p></div>
          </div>
          <HealthCard />
        </div>

        <!-- Novelas -->
        <div v-show="tab === 'novelas'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="book" :size="18" /></span>
            <div><h2>Novelas</h2><p>Elige de qué fuentes se buscan las novelas y en qué idiomas.</p></div>
          </div>
          <NovelSourcesCard />
        </div>

        <!-- Almacenamiento -->
        <div v-show="tab === 'almacenamiento'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="folder" :size="18" /></span>
            <div><h2>Almacenamiento</h2><p>Uso de disco por serie y limpieza de cachés prescindibles.</p></div>
          </div>

    <!-- Dónde vive la biblioteca (varios discos) va ANTES del uso de disco: primero qué
         carpetas hay, luego cuánto ocupan. -->
    <section class="card"><MangaRootsCard /></section>
    <div class="sep" />
    <IntegrityCard />
    <div class="sep" />
<!-- Almacenamiento -->
    <section class="card">
      <div class="card__title">
        <Icon name="folder" :size="16" /> Almacenamiento
        <span v-if="storage" class="stg__total">{{ formatBytes(storage.totals.total) }}</span>
        <button class="btn btn--xs stg__refresh" :disabled="storageLoading" @click="loadStorage" data-tip="Recalcular">
          <Icon name="refresh" :size="13" />
        </button>
      </div>

      <div v-if="storageLoading && !storage" class="stg__loading"><Spinner :size="22" /> Calculando uso de disco…</div>

      <template v-else-if="storage">
        <!-- Desglose por tipo -->
        <div class="stg__bar" :aria-label="'Uso de disco'">
          <span class="stg__seg stg__seg--orig" :style="{ flexGrow: storage.totals.original || 0.0001 }" data-tip="Originales descargados" />
          <span class="stg__seg stg__seg--up" :style="{ flexGrow: storage.totals.upscaled || 0.0001 }" data-tip="Escalado 4K" />
          <span class="stg__seg stg__seg--anime" :style="{ flexGrow: storage.totals.anime || 0.0001 }" data-tip="Anime (vídeo)" />
          <span class="stg__seg stg__seg--cache" :style="{ flexGrow: storage.totals.cache_total || 0.0001 }" data-tip="Cachés" />
        </div>
        <div class="stg__legend">
          <span><i class="stg__dot stg__dot--orig" /> Manga <b>{{ formatBytes(storage.totals.original) }}</b></span>
          <span><i class="stg__dot stg__dot--up" /> Escalado 4K <b>{{ formatBytes(storage.totals.upscaled) }}</b></span>
          <span v-if="storage.totals.anime"><i class="stg__dot stg__dot--anime" /> Anime <b>{{ formatBytes(storage.totals.anime) }}</b></span>
          <span><i class="stg__dot stg__dot--cache" /> Cachés <b>{{ formatBytes(storage.totals.cache_total) }}</b></span>
        </div>

        <!-- Espacio del disco -->
        <div v-if="storage.totals.disk_total" class="stg__disk">
          <div class="stg__diskbar">
            <span class="stg__diskfill" :style="{ width: (100 * storage.totals.disk_used / storage.totals.disk_total) + '%' }" />
          </div>
          <span class="stg__diskn">
            {{ formatBytes(storage.totals.disk_used) }} usados · <b>{{ formatBytes(storage.totals.disk_free) }} libres</b><em>&nbsp;de {{ formatBytes(storage.totals.disk_total) }}</em>
          </span>
        </div>

        <!-- Limpieza segura -->
        <div class="row stg__actions">
          <button class="btn" :disabled="storageBusy === 'stream_cache' || !storage.totals.stream_cache" @click="purgeStreamCache">
            <Icon name="close" :size="13" /> Vaciar caché de streaming
            <span class="stg__free">{{ formatBytes(storage.totals.stream_cache) }}</span>
          </button>
          <button class="btn" :disabled="storageBusy === 'image_cache' || !storage.totals.image_cache" @click="purgeStorageCache('image_cache', 'la caché de imágenes')">
            <Icon name="trash" :size="13" /> Limpiar caché de imágenes
            <span class="stg__free">{{ formatBytes(storage.totals.image_cache) }}</span>
          </button>
          <button class="btn" :disabled="storageBusy === 'export_temp' || !storage.totals.export_orphan" @click="purgeStorageCache('export_temp', 'temporales huérfanos')">
            <Icon name="trash" :size="13" /> Limpiar temporales huérfanos
            <span class="stg__free">{{ formatBytes(storage.totals.export_orphan) }}</span>
          </button>
        </div>
        <p class="hint">Las cachés se regeneran solas. Los temporales de exportación que aún pertenecen a una tarea —incluidos tomos terminados aún no guardados— se conservan; sólo se limpian huérfanos.</p>

        <!-- Series que más ocupan — con filtros por tipo (junto / por separado) -->
        <div v-if="storage.series.length" class="sep" />
        <div v-if="storage.series.length" class="stg__filters">
          <button type="button" class="stg__chip" :class="{ 'is-on': stgFilter === 'all' }" @click="stgFilter = 'all'">
            Todo <span class="stg__chipn">{{ formatBytes(storage.totals.original + storage.totals.upscaled + storage.totals.anime) }}</span>
          </button>
          <button type="button" class="stg__chip" :class="{ 'is-on': stgFilter === 'manga' }" @click="stgFilter = 'manga'">
            <i class="stg__dot stg__dot--orig" /> Manga
            <span class="stg__chipn">{{ formatBytes(storage.totals.original + storage.totals.upscaled) }}</span>
            <em class="stg__chipc">{{ stgCounts.manga }}</em>
          </button>
          <button v-if="storage.totals.anime" type="button" class="stg__chip" :class="{ 'is-on': stgFilter === 'anime' }" @click="stgFilter = 'anime'">
            <i class="stg__dot stg__dot--anime" /> Anime
            <span class="stg__chipn">{{ formatBytes(storage.totals.anime) }}</span>
            <em class="stg__chipc">{{ stgCounts.anime }}</em>
          </button>
        </div>
        <div class="stg__list">
          <div v-for="s in stgSeries.slice(0, 20)" :key="(s.kind || 'manga') + s.name" class="stg__row">
            <div class="stg__row-main">
              <span class="stg__name" :data-tip="s.name">{{ s.name }}</span>
              <span class="stg__meta">
                <template v-if="s.kind === 'anime'">{{ s.episodes }} episodio(s) · <span class="stg__an">anime</span></template>
                <template v-else>
                  {{ formatBytes(s.original_bytes) }} orig.
                  <template v-if="s.upscaled_bytes"> · <b class="stg__up">{{ formatBytes(s.upscaled_bytes) }} 4K</b></template>
                  <template v-if="s.translated_chapters"> · <span class="stg__tr">ES {{ s.translated_chapters }}</span></template>
                </template>
              </span>
            </div>
            <span class="stg__rowsize">{{ formatBytes(s.original_bytes + s.upscaled_bytes) }}</span>
          </div>
          <p v-if="!stgSeries.length" class="hint">No hay series de este tipo.</p>
        </div>
      </template>
    </section>

    
        </div>

        <!-- Copia y sincronización -->
        <div v-show="tab === 'copia'" class="set__cat">
          <div class="set__cathead">
            <span class="set__catic"><Icon name="refresh" :size="18" /></span>
            <div><h2>Copia y sincronización</h2><p>Guarda tu perfil en la nube, expórtalo a un archivo o a un tomo.</p></div>
          </div>

    <!-- Acceso remoto -->
    <section class="card">
      <div class="card__title"><Icon name="globe" :size="16" /> Acceso remoto</div>
      <p class="hint">
        Este equipo atiende peticiones de otros dispositivos <b>sólo con este token</b>. Desde el
        propio PC nunca hace falta. Úsalo para emparejar la aplicación de Android.
      </p>
      <div v-if="(pair.urls || []).length" class="dest dest--col">
        <span class="dest__lbl">Direcciones</span>
        <div v-for="u in pair.urls" :key="u" class="dest__row">
          <code class="dest__url">{{ u }}</code>
        </div>
        <span class="dest__hint">— usa la que esté en la misma red que el dispositivo; si va por el
          punto de acceso de este PC, es la <code>192.168.137.x</code></span>
      </div>
      <div class="dest">
        <span class="dest__lbl">Token</span>
        <code class="dest__url">{{ pairShown ? pair.token : '••••••••••••••••••••••••' }}</code>
        <button class="btn btn--xs" @click="pairShown = !pairShown">{{ pairShown ? 'Ocultar' : 'Ver' }}</button>
        <button class="btn btn--xs" @click="copyPair">{{ pairCopied ? '✓ Copiado' : 'Copiar' }}</button>
        <button class="btn btn--xs btn--danger" @click="rotatePair">Cambiar</button>
      </div>
      <p class="hint">
        Cambiar el token <b>desconecta cualquier dispositivo ya emparejado</b>: úsalo si pierdes uno.
        Nunca abras el puerto 5101 en el router — fuera de casa, VPN.
      </p>
    </section>
<!-- Copia y sincronización -->
    <section class="card">
      <div class="card__title"><Icon name="refresh" :size="16" /> Copia y sincronización</div>
      <p class="hint">
        Guarda tu perfil (progreso de manga y anime, favoritos, historial y ajustes) en un
        <b>repositorio Git privado</b> para no perderlo nunca al cambiar de equipo o reinstalar.
        No incluye archivos descargados ni claves API.
      </p>

      <label class="fld">
        <span>Repositorio remoto (URL git) <em>· usa un repo privado dedicado</em></span>
        <input v-model="remoteUrl" class="mono" placeholder="https://github.com/usuario/mi-perfil.git" spellcheck="false" />
      </label>
      <label class="fld">
        <span>Token de acceso (PAT) <em v-if="settings.sync.pat_set">· ya configurado, deja en blanco para conservarlo</em></span>
        <input v-model="patInput" type="password" class="mono" placeholder="github_pat_…" autocomplete="off" spellcheck="false" />
      </label>
      <div class="row">
        <button class="btn" :disabled="settings.syncBusy === 'configure'" @click="configureSync">Configurar remoto</button>
      </div>

      <div class="sep" />
      <div class="sync__foot">
        <span class="sync__status" :class="{ 'is-on': settings.sync.remote_set }">
          <span class="dot" />
          <template v-if="settings.sync.remote_set">Remoto configurado · última copia {{ relTime(settings.sync.last_saved_at) }}</template>
          <template v-else>Sin remoto configurado</template>
        </span>
        <div class="inline">
          <button class="btn btn--accent" :disabled="!settings.sync.remote_set || settings.syncBusy === 'save'" @click="saveLib">
            <Icon name="upload" :size="14" /> {{ settings.syncBusy === 'save' ? 'Guardando…' : 'Guardar biblioteca' }}
          </button>
          <button class="btn" :disabled="!settings.sync.remote_set || settings.syncBusy === 'restore'" @click="restoreLib">
            <Icon name="download" :size="14" /> {{ settings.syncBusy === 'restore' ? 'Recuperando…' : 'Recuperar biblioteca' }}
          </button>
        </div>
      </div>
      <div class="sep" />
      <!-- Un respaldo que hay que acordarse de pulsar no es un respaldo. -->
      <label class="fld fld--chk">
        <span>Copia automática cada semana
          <em>· en segundo plano, sólo si hay repo y token; si falla lo dice aquí</em></span>
        <input type="checkbox" :checked="settings.sync.auto !== false"
               @change="settings.setSyncAuto($event.target.checked)" />
      </label>
      <p v-if="settings.sync.auto_last_error" class="hint hint--warn">
        La última copia automática falló: {{ settings.sync.auto_last_error }}
      </p>
      <p v-if="settings.sync.identity" class="hint">Los commits se firman como <code>{{ settings.sync.identity }}</code>.</p>
    </section>

    

<!-- Backup de biblioteca -->
    <section class="card">
      <div class="card__title"><Icon name="download" :size="16" /> Respaldo de biblioteca (archivo)</div>
      <p class="hint">
        Exportá tu lista de manga y anime (series seguidas, estado y progreso de
        lectura/visto) para llevarla a otra PC — no incluye los archivos descargados.
      </p>
      <div class="row">
        <a class="btn btn--accent" href="/api/backup/export" download>
          <Icon name="download" :size="14" /> Exportar listas
        </a>
        <button class="btn" @click="triggerImport"><Icon name="folder" :size="14" /> Importar listas</button>
        <input ref="importInput" type="file" accept="application/json" hidden @change="onImportFile" />
      </div>
    </section>

    

<!-- Export destinations -->
    <section class="card">
      <div class="card__title"><Icon name="globe" :size="16" /> Exportar tomos</div>
      <div class="dests">
        <div class="dest">
          <span class="dest__lbl">Google Drive</span>
          <template v-if="manga.drive.connected">
            <span class="dest__ok">Conectado <template v-if="manga.drive.email">· {{ manga.drive.email }}</template></span>
            <button class="btn" @click="manga.disconnectDrive()">Desconectar</button>
          </template>
          <button v-else class="btn btn--accent" @click="manga.connectDrive()">Conectar Drive</button>
        </div>
        <div class="dest dest--col">
          <span class="dest__lbl">Biblioteca móvil</span>
          <div v-if="manga.webdav.localUrl" class="dest__row">
            <a :href="manga.webdav.localUrl" target="_blank" rel="noopener" class="dest__link">Abrir en este PC ↗</a>
            <span class="dest__hint">— funciona desde este navegador</span>
          </div>
          <div v-if="manga.webdav.phoneUrl" class="dest__row">
            <code class="dest__url">{{ manga.webdav.phoneUrl }}</code>
            <button class="btn btn--xs" @click="copyPhoneUrl">{{ phoneCopied ? '✓ Copiado' : 'Copiar' }}</button>
            <span class="dest__hint">— pega esta URL en el móvil (debe estar en el mismo WiFi)</span>
          </div>
          <span v-if="!manga.webdav.localUrl && !manga.webdav.phoneUrl" class="dest__muted">No configurado</span>
        </div>
      </div>
    </section>

    
        </div>
        <div v-show="tab === 'interno'" class="set__cat">
      <section class="card">
        <div class="card__title"><Icon name="palette" :size="16" /> Cocina del diseño <span class="tag">interno</span></div>
        <p class="hint">Todos los componentes del sistema en todos sus estados (vacío, cargando, error,
          título kilométrico). Sirve para pulir los casos límite aquí en vez de descubrirlos en tu biblioteca.</p>
        <button class="btn" @click="ui.goto('kitchen')"><Icon name="grid" :size="14" /> Abrir la cocina</button>
      </section>

      <section class="card">
        <div class="card__title"><Icon name="spark" :size="16" /> Modo QA de traducción <span class="tag">testing</span></div>
        <label class="fld fld--chk">
          <span>Activar modo QA <em>· conserva artefactos de debug al traducir y habilita el botón ⚑ en el lector para marcar páginas mal traducidas</em></span>
          <input type="checkbox" :checked="manga.qaMode" @change="manga.toggleQa()" />
        </label>
        <div class="sep" />
        <div class="qa-foot">
          <span class="hint">{{ qaInfo.flags }} página(s) marcada(s) · {{ formatBytes(qaInfo.bytes) }} en disco</span>
          <button class="btn btn--danger" :disabled="!qaInfo.bytes && !qaInfo.flags" @click="clearQa">
            <Icon name="close" :size="14" /> Borrar datos QA
          </button>
        </div>
        <p class="hint">Los casos se guardan en <code>data/_translation_qa/</code> (salida + arte EN + ES emparejada + overlay + diagnóstico) para afinar el algoritmo. Apagar el modo no borra lo ya guardado.</p>
      </section>
        </div>
        </template>
      </div>
    </div>

    <FolderPicker v-model:open="dlBrowse" title="Elegir carpeta de descargas de anime"
                  @pick="p => anime.saveDlPath(p.win || p.path)" />
  </div>
</template>

<style scoped>
.set { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.set__head { padding: var(--s-5) 0 var(--s-5); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.set__search { display: flex; align-items: center; gap: var(--s-2); max-width: 34rem; margin-top: var(--s-4); padding: var(--s-2) var(--s-3); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); color: var(--ink-faint); }
.set__search:focus-within { border-color: var(--azure); box-shadow: 0 0 0 3px color-mix(in srgb, var(--azure) 12%, transparent); }
.set__search input { flex: 1; min-width: 0; padding: 0; border: 0; outline: 0; background: transparent; color: var(--ink); font-size: var(--fs-sm); }
.set__search input::placeholder { color: var(--ink-ghost); }
.set__search-clear { display: grid; place-items: center; padding: 0.125rem; border: 0; border-radius: var(--r-sm); background: transparent; color: var(--ink-faint); cursor: pointer; }
.set__search-clear:hover { color: var(--ink); background: var(--surface-2); }
.set__search-hint { margin-top: var(--s-2); font-size: var(--fs-2xs); color: var(--ink-faint); }

/* ── two-pane shell: sticky category rail + content ─────────────────────── */
.set__shell { display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: var(--s-6); align-items: start; }
.set__rail { position: sticky; top: var(--s-4); display: flex; flex-direction: column; gap: 3px; padding: var(--s-2); border: 1px solid var(--line); border-radius: var(--r-lg); background: color-mix(in srgb, var(--surface) 70%, transparent); backdrop-filter: blur(8px); }
.set__tab { display: flex; align-items: center; gap: var(--s-2); width: 100%; padding: var(--s-2) var(--s-3); border: 1px solid transparent; border-radius: var(--r-md); background: transparent; color: var(--ink-faint); font-family: var(--font-body); font-size: var(--fs-sm); font-weight: 500; text-align: left; cursor: pointer; transition: background var(--t-fast), color var(--t-fast), border-color var(--t-fast); }
.set__tab :deep(svg) { color: currentColor; opacity: .8; flex-shrink: 0; }
.set__tab:hover { background: var(--surface-2); color: var(--ink-soft); }
.set__tab.is-active { color: var(--ink); background: color-mix(in srgb, var(--azure) 14%, transparent); border-color: color-mix(in srgb, var(--azure) 30%, transparent); box-shadow: inset 2px 0 0 var(--azure), 0 0 12px -4px var(--azure-glow); }
.set__tab.is-active :deep(svg) { color: var(--azure); opacity: 1; }

.set__pane { min-width: 0; display: flex; flex-direction: column; }
.set__cat { display: flex; flex-direction: column; gap: var(--s-4); }
.set__cathead { display: flex; align-items: center; gap: var(--s-3); margin-bottom: var(--s-1); }
.set__catic { display: grid; place-items: center; width: 2.5rem; height: 2.5rem; flex-shrink: 0; border-radius: var(--r-md); background: linear-gradient(140deg, color-mix(in srgb, var(--azure) 26%, transparent), color-mix(in srgb, var(--cyan) 18%, transparent)); border: 1px solid color-mix(in srgb, var(--azure) 30%, transparent); }
.set__catic :deep(svg) { color: var(--azure); }
.set__cathead h2 { font-family: var(--font-display); font-size: var(--fs-lg); font-weight: 600; color: var(--ink); line-height: 1.1; }
.set__cathead p { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }

.card { margin-bottom: 0; padding: var(--s-5); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.card__title { display: flex; align-items: center; gap: var(--s-2); font-weight: 600; color: var(--ink); margin-bottom: var(--s-4); }
.card__title :deep(svg) { color: var(--azure); }

.row { display: flex; gap: var(--s-5); flex-wrap: wrap; align-items: center; }
.fld { display: flex; flex-direction: column; gap: 0.375rem; font-size: var(--fs-xs); color: var(--ink-faint); flex: 1; min-width: 13.75rem; }
.fld em { font-style: normal; color: var(--ink-ghost); }
.fld--chk { flex-direction: row; align-items: center; justify-content: space-between; }
.fld--chk input { width: auto; }
.fld select, .fld input[type=text], .inline input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.fld select:focus, .inline input:focus { outline: none; border-color: var(--azure); }
.inline { display: flex; gap: var(--s-2); align-items: center; }
.inline--wrap { flex-wrap: wrap; margin-top: var(--s-2); }
.inline input { flex: 1; min-width: 10rem; }
.mono { font-family: var(--font-mono); }
.btn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.btn:hover { color: var(--ink); border-color: var(--line-strong); }
.btn--accent { background: var(--azure); color: #fff; border-color: transparent; font-weight: 600; }
.btn--accent:hover { background: var(--azure-bright); color: #fff; }
.btn--danger { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.btn--danger:hover:not(:disabled) { color: #fff; background: var(--coral); border-color: transparent; }
.btn--danger:disabled { opacity: .5; cursor: not-allowed; }
.tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: var(--tracking-caps); color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); border-radius: var(--r-pill); padding: 1px 0.5rem; }
.qa-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap; margin-bottom: var(--s-2); }
.hint { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.hint code, code { font-family: var(--font-mono); color: var(--ink-soft); }
.hint--warn { color: var(--warn); }

.sep { height: 1px; background: var(--line); margin: var(--s-4) 0; }
.qbt__status { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); }
.qbt__status.is-on { color: var(--jade); }
.dot { width: 0.4375rem; height: 0.4375rem; border-radius: 50%; background: var(--ink-ghost); }
.qbt__status.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }

.dests { display: flex; flex-direction: column; gap: var(--s-3); }
.dest { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }
.dest--col { flex-direction: column; align-items: flex-start; gap: var(--s-2); }
.dest__row { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.dest__url { font-size: var(--fs-xs); font-family: var(--font-mono); color: var(--ink-faint); background: var(--surface-2); padding: 2px 0.375rem; border-radius: var(--r-sm); user-select: all; }
.dest__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); }
.btn--xs { font-size: var(--fs-2xs); padding: 2px 0.5rem; }
.dest__lbl { font-weight: 500; min-width: 8.125rem; }
.dest__ok { color: var(--jade); font-size: var(--fs-sm); }
.dest__link { color: var(--azure-bright); font-size: var(--fs-sm); }
.dest__muted { color: var(--ink-faint); font-size: var(--fs-sm); }

/* Almacenamiento */
.stg__total { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); font-weight: 500; }
.stg__refresh { margin-left: auto; padding: 4px 0.5rem; }
.stg__loading { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); padding: var(--s-2) 0; }
.stg__bar { display: flex; height: 0.625rem; border-radius: var(--r-pill); overflow: hidden; background: var(--surface-2); margin-bottom: var(--s-3); }
.stg__seg { min-width: 2px; transition: flex-grow var(--t-base); }
.stg__seg--orig { background: var(--azure); }
.stg__seg--up { background: var(--cyan); }
.stg__seg--anime { background: var(--violet); }
.stg__seg--cache { background: var(--ink-ghost); }
.stg__legend { display: flex; gap: var(--s-4); flex-wrap: wrap; font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: var(--s-4); }
.stg__legend b { color: var(--ink); font-weight: 600; margin-left: 3px; }
.stg__dot { display: inline-block; width: 0.5rem; height: 0.5rem; border-radius: 2px; margin-right: 0.3125rem; vertical-align: baseline; }
.stg__dot--orig { background: var(--azure); }
.stg__dot--up { background: var(--cyan); }
.stg__dot--anime { background: var(--violet); }
.stg__dot--cache { background: var(--ink-ghost); }
.stg__an { color: var(--violet); }

/* filtros por tipo de contenido (junto / por separado) */
.stg__filters { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-bottom: var(--s-3); }
.stg__chip { display: inline-flex; align-items: center; gap: 0.4375rem; padding: 0.3125rem var(--s-3); border-radius: var(--r-pill); border: 1px solid var(--line-2); background: var(--surface-2); color: var(--ink-faint); font-size: var(--fs-xs); font-weight: 500; cursor: pointer; transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast); }
.stg__chip:hover { color: var(--ink-soft); border-color: var(--line); }
.stg__chip.is-on { color: var(--ink); background: color-mix(in srgb, var(--azure) 14%, transparent); border-color: color-mix(in srgb, var(--azure) 32%, transparent); }
.stg__chip .stg__dot { width: 0.5rem; height: 0.5rem; border-radius: 2px; }
.stg__chipn { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-soft); }
.stg__chip.is-on .stg__chipn { color: var(--ink); }
.stg__chipc { font-style: normal; font-size: var(--fs-2xs); color: var(--ink-ghost); background: var(--base); border-radius: var(--r-pill); padding: 1px 0.375rem; }
.stg__disk { display: flex; flex-direction: column; gap: 0.3125rem; margin-bottom: var(--s-4); }
.stg__diskbar { height: 0.5rem; border-radius: var(--r-pill); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
.stg__diskfill { display: block; height: 100%; background: linear-gradient(90deg, var(--azure), var(--cyan)); border-radius: var(--r-pill); }
.stg__diskn { font-size: var(--fs-xs); color: var(--ink-faint); }
.stg__diskn b { color: var(--jade); font-weight: 600; }
.stg__diskn em { font-style: normal; color: var(--ink-ghost); }
.stg__actions { margin-bottom: var(--s-2); }
.stg__free { font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .7; margin-left: 4px; }
.stg__rowsize { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); flex-shrink: 0; }
.stg__list { display: flex; flex-direction: column; gap: 2px; }
.stg__row { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-2); border-radius: var(--r-sm); }
.stg__row:hover { background: var(--surface-2); }
.stg__row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.stg__name { font-size: var(--fs-sm); color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stg__meta { font-size: var(--fs-2xs); color: var(--ink-faint); font-family: var(--font-mono); }
.stg__up { color: var(--cyan); font-weight: 600; }
.stg__tr { color: var(--jade); }

/* password inputs share the text-input styling */
.fld input[type=password] { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); font-family: var(--font-mono); }
.fld input[type=password]:focus { outline: none; border-color: var(--azure); }

/* API keys */
.keygrp { padding: var(--s-3) 0; border-top: 1px solid var(--line); }
.keygrp:first-of-type { border-top: none; }
.keygrp__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--s-2); }
.keygrp__name { font-size: var(--fs-sm); font-weight: 600; color: var(--ink); }
.keygrp__state { display: inline-flex; align-items: center; gap: 0.375rem; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.keygrp__state.is-on { color: var(--jade); }
.keygrp__state.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }
.keygrp__hint { color: var(--jade) !important; font-family: var(--font-mono); font-size: var(--fs-2xs); }
.keygrp__fields { display: flex; flex-wrap: wrap; gap: var(--s-3); }
.keygrp__foot { margin-top: var(--s-2); }

/* Biblioteca oculta */
.card--hid { border-color: color-mix(in srgb, var(--violet) 42%, transparent); box-shadow: inset 2px 0 0 var(--violet), 0 0 18px -8px color-mix(in srgb, var(--violet) 70%, transparent); }
.card--hid .card__title :deep(svg) { color: var(--violet); }
.tag--hid { color: var(--violet); border-color: color-mix(in srgb, var(--violet) 45%, transparent); }
.hid__state { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); margin: var(--s-1) 0 var(--s-3); }
.hid__state.is-on { color: var(--violet); }
.hid__state.is-on .dot { background: var(--violet); box-shadow: 0 0 8px color-mix(in srgb, var(--violet) 60%, transparent); }
.hid__change { margin-top: var(--s-3); }
.hid__change summary { font-size: var(--fs-xs); color: var(--ink-faint); cursor: pointer; user-select: none; }
.hid__change summary:hover { color: var(--ink-soft); }
.hid__change .fld { margin-top: var(--s-3); }
.hid__msg { font-size: var(--fs-xs); color: var(--coral); margin-top: var(--s-2); }

/* Sync */
.sync__foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap; }
.sync__status { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); }
.sync__status.is-on { color: var(--jade); }
.sync__status.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }

@media (max-width: 820px) {
  .set__shell { grid-template-columns: 1fr; gap: var(--s-4); }
  .set__rail { position: sticky; top: 0; z-index: 4; flex-direction: row; overflow-x: auto; gap: var(--s-1); scrollbar-width: none; }
  .set__rail::-webkit-scrollbar { display: none; }
  .set__tab { width: auto; white-space: nowrap; flex-shrink: 0; }
  .set__tab.is-active { box-shadow: inset 0 -2px 0 var(--azure), 0 0 12px -4px var(--azure-glow); }
}
@media (max-width: 560px) { .set { padding: 0 var(--s-4) var(--s-8); } }
</style>
