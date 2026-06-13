<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { formatBytes, formatSpeed, formatEta, qbtStateLabel } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
let poll = null

const pathInput = ref('')
watch(() => store.dlSettings.download_path, (v) => { pathInput.value = v || '' }, { immediate: true })

function useBrowsed() {
  pathInput.value = store.dlBrowse.win || ''
  store.saveDlPath(pathInput.value)
  store.closeDlBrowse()
}

onMounted(async () => {
  store.loadDlSettings()
  await store.checkQbt()
  await store.loadQbt()
  poll = setInterval(() => store.loadQbt(), 5000)
})
onUnmounted(() => { if (poll) clearInterval(poll) })

const isDone = (t) => t.progress >= 100
</script>

<template>
  <div class="dl">
    <header class="dl__head stagger">
      <div style="--i:0">
        <p class="eyebrow"><span class="tick" /> TRANSFERENCIAS</p>
        <h1>Descargas</h1>
      </div>
      <div class="dl__conn" style="--i:1" :class="{ 'is-on': store.qbt.connected }">
        <span class="dl__conn-dot" />
        {{ store.qbt.connected ? `qBittorrent ${store.qbt.version}` : 'Desconectado' }}
      </div>
    </header>

    <!-- Not connected → config -->
    <div v-if="!store.qbt.connected" class="qcfg">
      <Icon name="download" :size="30" />
      <p>Conecta tu qBittorrent (WebUI) para gestionar descargas.</p>
      <div class="qcfg__form">
        <input v-model="store.qbt.url" placeholder="http://localhost:8080" />
        <input v-model="store.qbt.username" placeholder="Usuario" />
        <input v-model="store.qbt.password" type="password" placeholder="Contraseña" />
        <button class="btn" @click="store.configureQbt()">Conectar</button>
      </div>
    </div>

    <template v-else>
      <!-- Download location -->
      <section class="loc">
        <div class="loc__head">
          <Icon name="folder" :size="16" />
          <div>
            <span class="loc__title">Carpeta de descargas</span>
            <p class="loc__hint">Dónde se guardan las series nuevas (cada anime en su subcarpeta). Útil para usar otro disco y no llenar C:.</p>
          </div>
        </div>
        <div class="loc__row">
          <input v-model="pathInput" class="loc__input" :placeholder="store.dlSettings.qbt_default || 'D:\\Anime'" spellcheck="false" />
          <button class="loc__btn" @click="store.openDlBrowse('')"><Icon name="folder" :size="14" /> Explorar</button>
          <button class="loc__btn loc__btn--save" @click="store.saveDlPath(pathInput)">Guardar</button>
        </div>
        <p class="loc__cur">
          <template v-if="store.dlSettings.download_path">Actual: <code>{{ store.dlSettings.download_path }}</code></template>
          <template v-else>Usando la carpeta por defecto de qBittorrent<template v-if="store.dlSettings.qbt_default">: <code>{{ store.dlSettings.qbt_default }}</code></template>. Deja el campo vacío y guarda para volver a ella.</template>
        </p>
      </section>

      <div v-if="store.qbtLoading && !store.qbtTorrents.length" class="center"><Spinner /></div>
      <div v-else-if="!store.qbtTorrents.length" class="empty">
        <Icon name="download" :size="34" /><p>No hay descargas activas.</p>
      </div>

      <div v-else class="dl__list">
        <div v-for="t in store.qbtTorrents" :key="t.hash" class="trow" :class="{ 'trow--done': isDone(t) }">
          <div class="trow__main">
            <div class="trow__name">{{ t.name }}</div>
            <div class="trow__bar"><span :style="{ width: Math.min(100, t.progress) + '%' }" /></div>
            <div class="trow__stats">
              <span class="trow__state" :class="{ 'is-dl': !isDone(t) }">{{ qbtStateLabel(t.state) }}</span>
              <span>{{ Math.round(t.progress) }}%</span>
              <span class="muted">{{ formatBytes(t.size) }}</span>
              <span v-if="!isDone(t)" class="trow__speed">↓ {{ formatSpeed(t.dlspeed) }}</span>
              <span v-if="!isDone(t)" class="muted">{{ formatEta(t.eta) }}</span>
              <span class="muted">{{ t.num_seeds }}S / {{ t.num_leechs }}L</span>
            </div>
          </div>
          <div class="trow__actions">
            <button v-if="!isDone(t)" class="ti" title="Pausar" @click="store.qbtAction('pause', t.hash)"><Icon name="close" :size="14" /></button>
            <button v-else class="ti" title="Reanudar" @click="store.qbtAction('resume', t.hash)"><Icon name="play" :size="14" /></button>
            <button class="ti" title="Verificar" @click="store.qbtAction('recheck', t.hash)"><Icon name="spark" :size="14" /></button>
            <button class="ti ti--danger" title="Eliminar (con archivos)" @click="store.qbtAction('delete', t.hash, true)"><Icon name="close" :size="14" /></button>
          </div>
        </div>
      </div>
    </template>

    <!-- Folder browser -->
    <Teleport to="body">
      <div v-if="store.dlBrowse.open" class="ov" @click.self="store.closeDlBrowse()">
        <div class="picker">
          <header class="picker__head">
            <span>Elegir carpeta de descargas</span>
            <button class="picker__x" @click="store.closeDlBrowse()"><Icon name="close" :size="16" /></button>
          </header>
          <div class="picker__bar">
            <button class="picker__up" :disabled="store.dlBrowse.parent === null" @click="store.openDlBrowse(store.dlBrowse.parent || '')">
              <Icon name="chevron" :size="14" :style="{ transform: 'rotate(180deg)' }" /> Subir
            </button>
            <code class="picker__path">{{ store.dlBrowse.win || 'Discos' }}</code>
          </div>
          <div v-if="store.dlBrowse.loading" class="center"><Spinner :size="20" /></div>
          <div v-else class="picker__list">
            <button v-for="it in store.dlBrowse.items" :key="it.path" class="picker__item" @click="store.openDlBrowse(it.path)">
              <Icon :name="it.is_drive ? 'download' : 'folder'" :size="15" />
              <span>{{ it.name }}</span>
            </button>
            <p v-if="!store.dlBrowse.items.length" class="picker__empty">Sin subcarpetas.</p>
          </div>
          <footer class="picker__foot">
            <span class="picker__sel">{{ store.dlBrowse.win || '—' }}</span>
            <button class="loc__btn loc__btn--save" :disabled="!store.dlBrowse.win" @click="useBrowsed">Usar esta carpeta</button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.dl { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }

/* download location */
.loc { margin-bottom: var(--s-5); padding: var(--s-4); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.loc__head { display: flex; align-items: flex-start; gap: var(--s-3); color: var(--azure); }
.loc__title { font-weight: 600; color: var(--ink); }
.loc__hint { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: 2px; }
.loc__row { display: flex; gap: var(--s-2); margin-top: var(--s-3); flex-wrap: wrap; }
.loc__input { flex: 1; min-width: 200px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); font-family: var(--font-mono); }
.loc__input:focus { outline: none; border-color: var(--azure); }
.loc__btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.loc__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.loc__btn--save { background: var(--azure); color: #fff; border-color: transparent; font-weight: 600; }
.loc__btn--save:hover:not(:disabled) { background: var(--azure-bright); color: #fff; }
.loc__btn--save:disabled { opacity: .5; cursor: not-allowed; }
.loc__cur { margin-top: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }
.loc__cur code { font-family: var(--font-mono); color: var(--ink-soft); }

/* folder picker */
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.picker { width: min(34rem, 100%); max-height: 80vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.picker__head { display: flex; align-items: center; justify-content: space-between; padding: var(--s-4); border-bottom: 1px solid var(--line); font-weight: 600; }
.picker__x { color: var(--ink-faint); }
.picker__x:hover { color: var(--ink); }
.picker__bar { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-bottom: 1px solid var(--line); }
.picker__up { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: var(--r-sm); font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2); }
.picker__up:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.picker__up:disabled { opacity: .4; cursor: not-allowed; }
.picker__path { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.picker__list { flex: 1; overflow-y: auto; padding: var(--s-2); display: flex; flex-direction: column; gap: 2px; }
.picker__item { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); color: var(--ink-soft); text-align: left; transition: background var(--t-fast); }
.picker__item:hover { background: var(--surface-2); color: var(--ink); }
.picker__item :deep(svg) { color: var(--azure); flex-shrink: 0; }
.picker__empty { padding: var(--s-4); text-align: center; color: var(--ink-faint); font-size: var(--fs-sm); }
.picker__foot { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-top: 1px solid var(--line); }
.picker__sel { font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dl__head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.dl__conn { display: inline-flex; align-items: center; gap: var(--s-2); padding: 6px 14px; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-faint); border: 1px solid var(--line); }
.dl__conn.is-on { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 30%, transparent); }
.dl__conn-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-ghost); }
.dl__conn.is-on .dl__conn-dot { background: var(--jade); box-shadow: 0 0 10px color-mix(in srgb, var(--jade) 60%, transparent); }

.qcfg { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) var(--s-4); color: var(--ink-faint); text-align: center; }
.qcfg__form { display: flex; flex-wrap: wrap; gap: var(--s-2); justify-content: center; margin-top: var(--s-2); }
.qcfg__form input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); min-width: 180px; }
.qcfg__form input:focus { outline: none; border-color: var(--azure); }

.btn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.btn:hover { background: var(--azure-bright); }

.center { display: grid; place-items: center; padding: var(--s-8); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

.dl__list { display: flex; flex-direction: column; gap: var(--s-2); }
.trow { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-3) var(--s-4); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); transition: border-color var(--t-fast); }
.trow:hover { border-color: var(--line-strong); }
.trow--done { opacity: .72; }
.trow__main { flex: 1; min-width: 0; }
.trow__name { font-size: var(--fs-sm); font-weight: 500; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.trow__bar { height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.trow__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base) var(--ease-silk); }
.trow--done .trow__bar span { background: var(--jade); }
.trow__stats { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); margin-top: 6px; font-size: var(--fs-xs); color: var(--ink-soft); font-family: var(--font-mono); }
.trow__stats .muted { color: var(--ink-faint); }
.trow__state { color: var(--ink-faint); }
.trow__state.is-dl { color: var(--cyan); }
.trow__speed { color: var(--azure-bright); }
.trow__actions { display: flex; gap: var(--s-1); }
.ti { width: 32px; height: 32px; display: grid; place-items: center; border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast); }
.ti:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ti--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

@media (max-width: 640px) { .dl { padding: 0 var(--s-4) var(--s-8); } }
</style>
