<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useAnimeStore } from '@/stores/anime'
import { useSettingsStore } from '@/stores/settings'
import { formatBytes } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import FolderPicker from '@/components/anime/FolderPicker.vue'

const ui = useUiStore()
const manga = useMangaStore()
const anime = useAnimeStore()
const settings = useSettingsStore()

const dlPath = ref('')
watch(() => anime.dlSettings.download_path, (v) => { dlPath.value = v || '' }, { immediate: true })

const qaInfo = ref({ bytes: 0, flags: 0 })
async function loadQa() { qaInfo.value = await manga.qaSize() }
async function clearQa() {
  if (!confirm('¿Borrar todos los datos QA (artefactos y páginas marcadas)?')) return
  await manga.qaClear(); loadQa()
}

// ── Almacenamiento ──────────────────────────────────────────────────────────
const storage = ref(null)
const storageLoading = ref(false)
const storageBusy = ref('')
async function loadStorage() {
  storageLoading.value = true
  try { storage.value = await api.get('/api/storage/summary') }
  catch (_) { storage.value = null }
  finally { storageLoading.value = false }
}
async function purgeStreamCache() {
  storageBusy.value = 'stream_cache'
  try {
    const res = await api.post('/api/storage/purge', { target: 'stream_cache' })
    ui.toast(`Liberado ${formatBytes(res.freed || 0)}`, 'ok')
    await loadStorage()
  } catch (_) { ui.toast('No se pudo liberar espacio', 'error') }
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
  if (!confirm('¿Recuperar la biblioteca desde la nube? Se fusiona con lo local (no borra tu progreso actual).')) return
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
      <p class="eyebrow"><span class="tick" /> CONFIGURACIÓN</p>
      <h1>Ajustes</h1>
    </header>

    <!-- Upscaling -->
    <section class="card">
      <div class="card__title"><Icon name="spark" :size="16" /> Escalado 4K (manga)</div>
      <div class="row">
        <label class="fld">
          <span>Modelo</span>
          <select :value="manga.activeModel" @change="manga.setModel($event.target.value)">
            <option v-for="(label, key) in manga.models" :key="key" :value="key">{{ label }}</option>
          </select>
        </label>
        <label class="fld fld--chk">
          <span>Modo eco <em>· deja correr MPV mientras escala</em></span>
          <input type="checkbox" :checked="manga.eco" @change="manga.setEco($event.target.checked)" />
        </label>
      </div>
    </section>

    <!-- Anime downloads -->
    <section class="card">
      <div class="card__title"><Icon name="download" :size="16" /> Descargas de anime</div>
      <label class="fld">
        <span>Carpeta de descargas <em>· cada serie en su subcarpeta; útil para usar otro disco</em></span>
        <div class="inline">
          <input v-model="dlPath" class="mono" :placeholder="anime.dlSettings.qbt_default || 'D:\\Anime'" spellcheck="false" />
          <button class="btn" @click="anime.openDlBrowse('')"><Icon name="folder" :size="14" /> Explorar</button>
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

    <!-- Modo QA de traducción (testing) -->
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

    <!-- Almacenamiento -->
    <section class="card">
      <div class="card__title">
        <Icon name="folder" :size="16" /> Almacenamiento
        <span v-if="storage" class="stg__total">{{ formatBytes(storage.totals.total) }}</span>
        <button class="btn btn--xs stg__refresh" :disabled="storageLoading" @click="loadStorage" title="Recalcular">
          <Icon name="refresh" :size="13" />
        </button>
      </div>

      <div v-if="storageLoading && !storage" class="stg__loading"><Spinner :size="22" /> Calculando uso de disco…</div>

      <template v-else-if="storage">
        <!-- Desglose por tipo -->
        <div class="stg__bar" :aria-label="'Uso de disco'">
          <span class="stg__seg stg__seg--orig" :style="{ flexGrow: storage.totals.original || 0.0001 }" title="Originales descargados" />
          <span class="stg__seg stg__seg--up" :style="{ flexGrow: storage.totals.upscaled || 0.0001 }" title="Escalado 4K" />
          <span class="stg__seg stg__seg--cache" :style="{ flexGrow: (storage.totals.stream_cache + storage.totals.qa) || 0.0001 }" title="Cachés" />
        </div>
        <div class="stg__legend">
          <span><i class="stg__dot stg__dot--orig" /> Originales <b>{{ formatBytes(storage.totals.original) }}</b></span>
          <span><i class="stg__dot stg__dot--up" /> Escalado 4K <b>{{ formatBytes(storage.totals.upscaled) }}</b></span>
          <span><i class="stg__dot stg__dot--cache" /> Cachés <b>{{ formatBytes(storage.totals.stream_cache + storage.totals.qa) }}</b></span>
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
        </div>
        <p class="hint">El escalado 4K y los originales no se borran desde aquí: ambos son necesarios para el comparador original/4K del lector. Solo la caché de streaming es prescindible (se regenera al reproducir).</p>

        <!-- Series que más ocupan -->
        <div v-if="storage.series.length" class="sep" />
        <div class="stg__list">
          <div v-for="s in storage.series.slice(0, 12)" :key="s.name" class="stg__row">
            <div class="stg__row-main">
              <span class="stg__name" :title="s.name">{{ s.name }}</span>
              <span class="stg__meta">
                {{ formatBytes(s.original_bytes) }} orig.
                <template v-if="s.upscaled_bytes"> · <b class="stg__up">{{ formatBytes(s.upscaled_bytes) }} 4K</b></template>
                <template v-if="s.translated_chapters"> · <span class="stg__tr">ES {{ s.translated_chapters }}</span></template>
              </span>
            </div>
            <span class="stg__rowsize">{{ formatBytes(s.original_bytes + s.upscaled_bytes) }}</span>
          </div>
        </div>
      </template>
    </section>

    <!-- Conexiones y claves API -->
    <section class="card">
      <div class="card__title">
        <Icon name="settings" :size="16" /> Conexiones y claves API
        <button class="btn btn--xs stg__refresh" @click="triggerEnvImport" title="Importar un archivo .env">
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

    <FolderPicker />
  </div>
</template>

<style scoped>
.set { max-width: 860px; margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.set__head { padding: var(--s-5) 0 var(--s-6); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.card { margin-bottom: var(--s-4); padding: var(--s-5); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.card__title { display: flex; align-items: center; gap: var(--s-2); font-weight: 600; color: var(--ink); margin-bottom: var(--s-4); }
.card__title :deep(svg) { color: var(--azure); }

.row { display: flex; gap: var(--s-5); flex-wrap: wrap; align-items: center; }
.fld { display: flex; flex-direction: column; gap: 6px; font-size: var(--fs-xs); color: var(--ink-faint); flex: 1; min-width: 220px; }
.fld em { font-style: normal; color: var(--ink-ghost); }
.fld--chk { flex-direction: row; align-items: center; justify-content: space-between; }
.fld--chk input { width: auto; }
.fld select, .fld input[type=text], .inline input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.fld select:focus, .inline input:focus { outline: none; border-color: var(--azure); }
.inline { display: flex; gap: var(--s-2); align-items: center; }
.inline--wrap { flex-wrap: wrap; margin-top: var(--s-2); }
.inline input { flex: 1; min-width: 160px; }
.mono { font-family: var(--font-mono); }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.btn:hover { color: var(--ink); border-color: var(--line-strong); }
.btn--accent { background: var(--azure); color: #fff; border-color: transparent; font-weight: 600; }
.btn--accent:hover { background: var(--azure-bright); color: #fff; }
.btn--danger { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }
.btn--danger:hover:not(:disabled) { color: #fff; background: var(--coral); border-color: transparent; }
.btn--danger:disabled { opacity: .5; cursor: not-allowed; }
.tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: var(--tracking-caps); color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); border-radius: var(--r-pill); padding: 1px 8px; }
.qa-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap; margin-bottom: var(--s-2); }
.hint { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.hint code, code { font-family: var(--font-mono); color: var(--ink-soft); }

.sep { height: 1px; background: var(--line); margin: var(--s-4) 0; }
.qbt__status { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); }
.qbt__status.is-on { color: var(--jade); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-ghost); }
.qbt__status.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }

.dests { display: flex; flex-direction: column; gap: var(--s-3); }
.dest { display: flex; align-items: center; gap: var(--s-3); flex-wrap: wrap; }
.dest--col { flex-direction: column; align-items: flex-start; gap: var(--s-2); }
.dest__row { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.dest__url { font-size: var(--fs-xs); font-family: var(--font-mono); color: var(--ink-faint); background: var(--surface-2); padding: 2px 6px; border-radius: var(--r-sm); user-select: all; }
.dest__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); }
.btn--xs { font-size: var(--fs-2xs); padding: 2px 8px; }
.dest__lbl { font-weight: 500; min-width: 130px; }
.dest__ok { color: var(--jade); font-size: var(--fs-sm); }
.dest__link { color: var(--azure-bright); font-size: var(--fs-sm); }
.dest__muted { color: var(--ink-faint); font-size: var(--fs-sm); }

/* Almacenamiento */
.stg__total { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); font-weight: 500; }
.stg__refresh { margin-left: auto; padding: 4px 8px; }
.stg__loading { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); padding: var(--s-2) 0; }
.stg__bar { display: flex; height: 10px; border-radius: var(--r-pill); overflow: hidden; background: var(--surface-2); margin-bottom: var(--s-3); }
.stg__seg { min-width: 2px; transition: flex-grow var(--t-base); }
.stg__seg--orig { background: var(--azure); }
.stg__seg--up { background: var(--cyan); }
.stg__seg--cache { background: var(--ink-ghost); }
.stg__legend { display: flex; gap: var(--s-4); flex-wrap: wrap; font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: var(--s-4); }
.stg__legend b { color: var(--ink); font-weight: 600; margin-left: 3px; }
.stg__dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; vertical-align: baseline; }
.stg__dot--orig { background: var(--azure); }
.stg__dot--up { background: var(--cyan); }
.stg__dot--cache { background: var(--ink-ghost); }
.stg__disk { display: flex; flex-direction: column; gap: 5px; margin-bottom: var(--s-4); }
.stg__diskbar { height: 8px; border-radius: var(--r-pill); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); }
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
.keygrp__state { display: inline-flex; align-items: center; gap: 6px; font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.keygrp__state.is-on { color: var(--jade); }
.keygrp__state.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }
.keygrp__hint { color: var(--jade) !important; font-family: var(--font-mono); font-size: var(--fs-2xs); }
.keygrp__fields { display: flex; flex-wrap: wrap; gap: var(--s-3); }
.keygrp__foot { margin-top: var(--s-2); }

/* Sync */
.sync__foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap; }
.sync__status { display: inline-flex; align-items: center; gap: var(--s-2); font-size: var(--fs-sm); color: var(--ink-faint); }
.sync__status.is-on { color: var(--jade); }
.sync__status.is-on .dot { background: var(--jade); box-shadow: 0 0 8px color-mix(in srgb, var(--jade) 60%, transparent); }

@media (max-width: 560px) { .set { padding: 0 var(--s-4) var(--s-8); } }
</style>
