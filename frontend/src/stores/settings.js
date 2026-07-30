import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/lib/api'

// Config (API keys) + library sync (Guardar/Recuperar). Secrets never leave the
// backend as plaintext — the UI only ever sees {set, hint}.
export const useSettingsStore = defineStore('settings', () => {
  const keyGroups = ref([])      // [{group, fields:[{key,label,secret,set,hint|value,placeholder}]}]
  const keysLoading = ref(false)
  const keysSaving = ref(false)

  async function loadKeys() {
    keysLoading.value = true
    try { keyGroups.value = (await api.get('/api/config/keys')).groups || [] }
    finally { keysLoading.value = false }
  }

  // values: {KEY: newValue} — only send fields the user actually typed into.
  async function saveKeys(values) {
    keysSaving.value = true
    try {
      const res = await api.post('/api/config/keys', { values })
      keyGroups.value = res.groups || keyGroups.value
      return res
    } finally { keysSaving.value = false }
  }

  async function importEnv(text) {
    const res = await api.post('/api/config/import_env', { text })
    keyGroups.value = res.groups || keyGroups.value
    return res
  }

  // ── Sync ───────────────────────────────────────────────────────────────────
  const sync = ref({ remote_set: false, pat_set: false, last_saved_at: null, identity: '' })
  const syncBusy = ref('')  // '' | 'save' | 'restore' | 'configure'

  async function loadSyncStatus() {
    try { sync.value = await api.get('/api/sync/status') } catch (_) {}
  }
  async function configureSync(remote_url, pat) {
    syncBusy.value = 'configure'
    try { sync.value = await api.post('/api/sync/configure', { remote_url, pat }); return sync.value }
    finally { syncBusy.value = '' }
  }
  async function saveLibrary() {
    syncBusy.value = 'save'
    try { const r = await api.post('/api/sync/save', {}); if (!r.error) sync.value = { ...sync.value, ...r }; return r }
    finally { syncBusy.value = '' }
  }
  async function setSyncAuto(enabled) {
    const prev = sync.value.auto
    sync.value = { ...sync.value, auto: enabled }          // respuesta inmediata al clic
    try { sync.value = await api.post('/api/sync/auto', { enabled }) }
    catch (e) { sync.value = { ...sync.value, auto: prev }; throw e }
  }

  async function restoreLibrary() {
    syncBusy.value = 'restore'
    try { return await api.post('/api/sync/restore', {}) }
    finally { syncBusy.value = '' }
  }

  return {
    keyGroups, keysLoading, keysSaving, loadKeys, saveKeys, importEnv,
    sync, syncBusy, loadSyncStatus, configureSync, saveLibrary, setSyncAuto, restoreLibrary,
  }
})
