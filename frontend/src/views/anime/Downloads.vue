<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { useUiStore } from '@/stores/ui'
import { formatBytes, formatSpeed, formatEta, qbtStateLabel } from '@/lib/format'
import { parseRelease, matchLibrary } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import ContentToolbar from '@/components/ui/ContentToolbar.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import FolderPicker from '@/components/ui/FolderPicker.vue'
import MediaQueue from '@/components/media/MediaQueue.vue'

const store = useAnimeStore()
const ui = useUiStore()
let poll = null

// Los dos botones de quitar están juntos y solo este BORRA el vídeo: confirmación obligatoria.
async function removeWithFiles(t) {
  if (await ui.confirm({ title: 'Borrar archivos', danger: true, confirmLabel: 'Borrar del disco',
      body: `¿Borrar los archivos de "${t.name}"?\nEsto elimina el vídeo del disco, no solo el torrent.` }))
    store.qbtAction('delete', t.hash, true)
}

const pathInput = ref('')
const browsing = ref(false)
watch(() => store.dlSettings.download_path, (v) => { pathInput.value = v || '' }, { immediate: true })

onMounted(async () => {
  store.loadDlSettings()
  // Los pósters salen de cruzar el release con TU biblioteca, y esta vista se puede abrir sin
  // haber pasado por «Mi Anime» — sin esto, la mayoría de filas quedaban sin portada.
  if (!store.library.length) store.loadLibrary(true)
  await store.checkQbt()
  await store.loadQbt()
  poll = setInterval(() => store.loadQbt(), 5000)
})
onUnmounted(() => { if (poll) clearInterval(poll) })

const isDone = (t) => t.progress >= 100
// qBittorrent reports paused as 'pausedDL/UP' (v4) or 'stoppedDL/UP' (v5).
const isPaused = (t) => /^(paused|stopped)/.test(t.state || '')

/* La vista mostraba los 87 torrents de golpe y MEDIDO ninguno estaba descargando (83 detenidos
   sembrando + 4 en cola de subida): la pantalla cuyo trabajo es «qué se está bajando ahora» era
   una lista de cosas terminadas. Por defecto se ven las activas; lo terminado sigue a un clic. */
const FILTROS = [
  { id: 'activas',   label: 'Descargando' },
  { id: 'sembrando', label: 'Sembrando' },
  { id: 'todas',     label: 'Todas' },
]
const filtro = ref('activas')
const busca = ref('')

// Un torrent + lo que se puede saber de él: serie, episodio y el póster de tu biblioteca.
const filas = computed(() => store.qbtTorrents.map(t => {
  const r = parseRelease(t.name)
  const anime = matchLibrary(r.title, store.library)
  return { t, r, anime, cover: anime?.cover || '' }
}))

const cuenta = computed(() => ({
  activas:   filas.value.filter(f => !isDone(f.t)).length,
  sembrando: filas.value.filter(f => isDone(f.t)).length,
  todas:     filas.value.length,
}))
const filtros = computed(() => FILTROS.map(f => ({ ...f, n: cuenta.value[f.id] })))

// Con 87 torrents sembrando y ninguno bajando, la vista entera era un cartel en el tercio
// superior y 700 px de negro debajo, más un botón para ver lo que ya tenemos aquí mismo. Si no
// hay nada activo, se enseñan los terminados directamente y se avisa arriba; el filtro NO cambia,
// así que la pestaña «Descargando» sigue diciendo la verdad sobre lo que hay descargando.
const cayendoASembrando = computed(() =>
  filtro.value === 'activas' && !busca.value.trim() &&
  !filas.value.some(f => !isDone(f.t)) && cuenta.value.sembrando > 0)

const visibles = computed(() => {
  const q = busca.value.trim().toLowerCase()
  const modo = cayendoASembrando.value ? 'sembrando' : filtro.value
  return filas.value.filter(f => {
    if (modo === 'activas' && isDone(f.t)) return false
    if (modo === 'sembrando' && !isDone(f.t)) return false
    return !q || f.r.title.toLowerCase().includes(q) || f.t.name.toLowerCase().includes(q)
  })
})

// «T1 · Ep 3 · 1080p», sin las partes que no se saben (y sin el `·` huérfano de cada una).
function detalle(r) {
  return [
    r.season != null ? `T${r.season}` : '',
    r.batch ? 'Temporada completa' : (r.episode != null ? `Ep ${r.episode}` : ''),
    r.quality,
  ].filter(Boolean).join(' · ')
}

const ajustes = ref(false)
</script>

<template>
  <div class="dl">
    <!-- Sin titular: la barra superior ya dice «Descargas» a 15 px de aquí, y un <h1> de 48 px
         repitiendo la misma palabra no informa de nada. Mi Anime —la vista mejor resuelta— tampoco
         tiene titular: el contenido empieza arriba. Lo que sí aporta (el estado de qBittorrent) se
         queda. -->
    <header class="dl__head stagger">
      <div class="dl__conn" style="--i:0" :class="{ 'is-on': store.qbt.connected }">
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
      <!-- Ajustes de carpeta: es configuración, no contenido. Ocupaba el sitio de honor cada vez
           que entrabas a mirar una descarga; ahora se despliega cuando la buscas. -->
      <section v-if="ajustes" class="loc">
        <div class="loc__head">
          <Icon name="folder" :size="16" />
          <div>
            <span class="loc__title">Carpeta de descargas</span>
            <p class="loc__hint">Dónde se guardan las series nuevas (cada anime en su subcarpeta). Útil para usar otro disco y no llenar C:.</p>
          </div>
        </div>
        <div class="loc__row">
          <input v-model="pathInput" class="loc__input" :placeholder="store.dlSettings.qbt_default || 'D:\\Anime'" spellcheck="false" />
          <button class="loc__btn" @click="browsing = true"><Icon name="folder" :size="14" /> Explorar</button>
          <button class="loc__btn loc__btn--save" @click="store.saveDlPath(pathInput)">Guardar</button>
        </div>
        <p class="loc__cur">
          <template v-if="store.dlSettings.download_path">Actual: <code>{{ store.dlSettings.download_path }}</code></template>
          <template v-else>Usando la carpeta por defecto de qBittorrent<template v-if="store.dlSettings.qbt_default">: <code>{{ store.dlSettings.qbt_default }}</code></template>. Deja el campo vacío y guarda para volver a ella.</template>
        </p>
      </section>

      <!-- La cola de Sonarr/Radarr, ARRIBA y no en una vista propia: sus descargas ya salen en la
           lista de torrents de abajo, pero sólo aquí se ve si un fichero al 100 % está atascado
           importando. Se dibuja sola cuando hay algo; si no usas Cine, no existe. -->
      <MediaQueue />

      <ContentToolbar v-if="store.qbtTorrents.length" :filters="filtros" v-model:filter="filtro"
                      v-model:search="busca" search-placeholder="Buscar en la cola…">
        <template #extra>
          <button class="ti" :class="{ 'is-on': ajustes }" data-tip="Carpeta de descargas"
                  @click="ajustes = !ajustes"><Icon name="folder" :size="14" /></button>
        </template>
      </ContentToolbar>

      <div v-if="store.qbtLoading && !store.qbtTorrents.length" class="center"><Spinner /></div>
      <!-- qBittorrent apagado (o la VPN caída) NO es «no hay descargas»: son estados distintos y
           el segundo te haría creer que tus torrents desaparecieron. -->
      <ErrorState v-else-if="store.qbtError" title="No se pudo hablar con qBittorrent."
                  hint="Comprueba que está abierto y, si usas VPN, que sigue conectada."
                  :detail="store.qbtError" @retry="store.loadQbt()" />
      <EmptyState v-else-if="!store.qbtTorrents.length" icon="download" title="No hay descargas activas."
                  hint="Lo que descargues desde Buscar Anime aparecerá aquí." />
      <!-- «Nada bajando ahora» NO es «no tienes torrents»: con 87 sembrando, decir lo segundo
           te haría pensar que se han perdido. -->
      <EmptyState v-else-if="!visibles.length && filtro === 'activas'" full icon="download"
                  title="No hay nada descargando ahora."
                  hint="Lo que envíes a qBittorrent desde Buscar Anime aparecerá aquí." />
      <EmptyState v-else-if="!visibles.length" full icon="search" title="Nada coincide con la búsqueda." />

      <div v-else class="dl__list">
        <p v-if="cayendoASembrando" class="dl__nota">
          No hay nada descargando ahora — estos son tus {{ cuenta.sembrando }} torrents terminados.
        </p>
        <div v-for="{ t, r, cover } in visibles" :key="t.hash" class="trow" :class="{ 'trow--done': isDone(t) }">
          <!-- Sin blur-up: la caja mide 44 px, así que el micro-thumb de 28 px no es un
               placeholder, es prácticamente la imagen final. Una petición por fila, no dos. -->
          <div class="trow__poster">
            <img v-if="cover" :src="imgProxy(cover, 96)" :alt="r.title" loading="lazy" decoding="async" />
            <Icon v-else name="download" :size="16" />
          </div>
          <div class="trow__main">
            <div class="trow__name" :data-tip="t.name">{{ r.title }}</div>
            <div v-if="detalle(r)" class="trow__meta">{{ detalle(r) }}</div>
            <div class="trow__bar"><span :style="{ width: Math.min(100, t.progress) + '%' }" /></div>
            <div class="trow__stats">
              <span class="trow__state" :class="{ 'is-dl': !isDone(t) }">{{ qbtStateLabel(t.state) }}</span>
              <span v-if="!isDone(t)">{{ Math.round(t.progress) }}%</span>
              <span class="muted">{{ formatBytes(t.size) }}</span>
              <span v-if="!isDone(t)" class="trow__speed">↓ {{ formatSpeed(t.dlspeed) }}</span>
              <span v-if="!isDone(t)" class="muted">{{ formatEta(t.eta) }}</span>
              <span class="muted">{{ t.num_seeds }}S / {{ t.num_leechs }}L</span>
            </div>
          </div>
          <div class="trow__actions">
            <button class="ti" :class="{ 'is-on': isPaused(t) }" :data-tip="isPaused(t) ? 'Reanudar' : 'Pausar'"
                    @click="store.qbtAction(isPaused(t) ? 'resume' : 'pause', t.hash)">
              <Icon :name="isPaused(t) ? 'play' : 'pause'" :size="14" />
            </button>
            <button class="ti" data-tip="Verificar" @click="store.qbtAction('recheck', t.hash)"><Icon name="refresh" :size="14" /></button>
            <!-- Quitar de qBittorrent CONSERVANDO los archivos: la forma de dejar de sembrar sin
                 perder el episodio. Es la acción habitual, por eso va antes y sin estilo de peligro. -->
            <button class="ti" data-tip="Dejar de sembrar (conserva los archivos)"
                    @click="store.qbtAction('delete', t.hash, false)"><Icon name="close" :size="14" /></button>
            <button class="ti ti--danger" data-tip="Eliminar CON los archivos"
                    @click="removeWithFiles(t)"><Icon name="trash" :size="14" /></button>
          </div>
        </div>
      </div>
    </template>

    <FolderPicker v-model:open="browsing" title="Elegir carpeta de descargas de anime"
                  @pick="p => store.saveDlPath(p.win || p.path)" />
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
.loc__input { flex: 1; min-width: 12.5rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--base); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); font-family: var(--font-mono); }
.loc__input:focus { outline: none; border-color: var(--azure); }
.loc__btn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.loc__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.loc__btn--save { background: var(--azure); color: #fff; border-color: transparent; font-weight: 600; }
.loc__btn--save:hover:not(:disabled) { background: var(--azure-bright); color: #fff; }
.loc__btn--save:disabled { opacity: .5; cursor: not-allowed; }
.loc__cur { margin-top: var(--s-2); font-size: var(--fs-xs); color: var(--ink-faint); }
.loc__cur code { font-family: var(--font-mono); color: var(--ink-soft); }

/* folder picker */
/* `flex-end`, no `space-between`: al quitar el titular quedó un solo hijo y `space-between` lo
   mandaba al margen izquierdo, donde no pega con nada. El estado de qBittorrent va a la derecha. */
.dl__head { display: flex; align-items: flex-end; justify-content: flex-end; flex-wrap: wrap; gap: var(--s-4); padding: var(--s-5) 0 var(--s-3); }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }

.dl__conn { display: inline-flex; align-items: center; gap: var(--s-2); padding: 0.375rem 0.875rem; border-radius: var(--r-pill); font-size: var(--fs-xs); color: var(--ink-faint); border: 1px solid var(--line); }
.dl__conn.is-on { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 30%, transparent); }
.dl__conn-dot { width: 0.4375rem; height: 0.4375rem; border-radius: 50%; background: var(--ink-ghost); }
.dl__conn.is-on .dl__conn-dot { background: var(--jade); box-shadow: 0 0 10px color-mix(in srgb, var(--jade) 60%, transparent); }

.qcfg { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) var(--s-4); color: var(--ink-faint); text-align: center; }
.qcfg__form { display: flex; flex-wrap: wrap; gap: var(--s-2); justify-content: center; margin-top: var(--s-2); }
.qcfg__form input { padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); min-width: 11.25rem; }
.qcfg__form input:focus { outline: none; border-color: var(--azure); }

.btn { padding: var(--s-2) var(--s-5); border-radius: var(--r-sm); background: var(--azure); color: #fff; font-weight: 600; font-size: var(--fs-sm); transition: background var(--t-fast); }
.btn:hover { background: var(--azure-bright); }

.center { display: grid; place-items: center; padding: var(--s-8); }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); }

.dl__list { display: flex; flex-direction: column; gap: var(--s-2); }
.dl__nota { color: var(--ink-faint); font-size: var(--fs-sm); margin-bottom: var(--s-1); }
.trow { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-3) var(--s-4); border-radius: var(--r-md); background: var(--surface); border: 1px solid var(--line); transition: border-color var(--t-fast); }
.trow:hover { border-color: var(--line-strong); }
.trow--done { opacity: .72; }
.trow__poster {
  position: relative; flex-shrink: 0; width: 2.75rem; aspect-ratio: 2 / 3; overflow: hidden;
  border-radius: var(--r-sm); background: var(--surface-2);
  display: grid; place-items: center; color: var(--ink-ghost);
}
.trow__poster img { width: 100%; height: 100%; object-fit: cover; }
.trow__main { flex: 1; min-width: 0; }
.trow__meta { font-size: var(--fs-xs); color: var(--ink-faint); margin-bottom: 0.375rem; }
.trow__name { font-size: var(--fs-sm); font-weight: 600; margin-bottom: 0.125rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.trow__bar { height: 4px; border-radius: var(--r-pill); background: var(--surface-3); overflow: hidden; }
.trow__bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--cyan), var(--azure)); transition: width var(--t-base) var(--ease-silk); }
.trow--done .trow__bar span { background: var(--jade); }
.trow__stats { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); margin-top: 0.375rem; font-size: var(--fs-xs); color: var(--ink-soft); font-family: var(--font-mono); }
.trow__stats .muted { color: var(--ink-faint); }
.trow__state { color: var(--ink-faint); }
.trow__state.is-dl { color: var(--cyan); }
.trow__speed { color: var(--azure-bright); }
.trow__actions { display: flex; gap: var(--s-1); }
.ti { width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); border: 1px solid var(--line); color: var(--ink-faint); transition: all var(--t-fast); }
.ti:hover { color: var(--ink); border-color: var(--line-strong); background: var(--surface-2); }
.ti.is-on { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.ti--danger:hover { color: var(--coral); border-color: color-mix(in srgb, var(--coral) 40%, transparent); }

@media (max-width: 640px) { .dl { padding: 0 var(--s-4) var(--s-8); } }
</style>
