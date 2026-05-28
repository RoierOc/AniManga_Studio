<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { formatBytes, formatSpeed, formatEta, qbtStateLabel } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
let poll = null

onMounted(async () => {
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
  </div>
</template>

<style scoped>
.dl { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
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
