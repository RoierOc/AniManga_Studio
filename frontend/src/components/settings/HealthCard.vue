<script setup>
import { computed, ref } from 'vue'
import { api } from '@/lib/api'
import Icon from '@/components/ui/Icon.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { formatBytes } from '@/lib/format'
import { healthSnapshotStatus } from './healthSnapshot'

const emit = defineEmits(['navigate'])
const health = ref(null)
const integrity = ref(null)
const healthError = ref('')
const integrityError = ref('')
const loading = ref(false)
const started = ref(false)

async function check() {
  loading.value = true
  started.value = true
  health.value = null
  integrity.value = null
  healthError.value = ''
  integrityError.value = ''
  const results = await Promise.allSettled([
    api.get('/api/health/check'),
    api.get('/api/storage/integrity'),
  ])
  if (results[0].status === 'fulfilled') health.value = results[0].value
  else healthError.value = results[0].reason?.body || results[0].reason?.message || 'No se pudo comprobar la salud de la biblioteca.'
  if (results[1].status === 'fulfilled') integrity.value = results[1].value
  else integrityError.value = results[1].reason?.body || results[1].reason?.message || 'No se pudo comprobar la integridad de los torrents.'
  loading.value = false
}

const status = computed(() => healthSnapshotStatus({
  health: health.value,
  integrity: integrity.value,
  healthError: healthError.value,
  integrityError: integrityError.value,
}))
const broken = computed(() => health.value?.sources?.broken || [])
const autoheal = computed(() => health.value?.sources?.autoheal || [])
const unreachable = computed(() => health.value?.sources?.unreachable || [])
const unresolved = computed(() => health.value?.identity?.unresolved || [])
const identityFailed = computed(() => health.value?.identity?.failed || [])
const services = computed(() => health.value?.services || [])
const serviciosCaidos = computed(() => services.value.filter((service) => !service.online))
const errorList = computed(() => Object.entries(health.value?.errors || {}))
const mismatched = computed(() => integrity.value?.mismatched || [])
const failedChecks = computed(() => Object.entries(health.value?.checks || {}).filter(([, check]) => !check.ok))
const labels = { sources: 'Fuentes', identity: 'Identidad MangaDex', services: 'Servicios locales', errors: 'Registro de errores' }
const badge = computed(() => {
  if (status.value === 'incomplete') return 'Comprobación incompleta'
  if (status.value === 'healthy') return 'Todo en orden'
  return `${(health.value?.problems || 0) + mismatched.value.length} problema(s)`
})
const signed = (n) => (n > 0 ? `+${formatBytes(n)}` : `−${formatBytes(-n)}`)
</script>

<template>
  <section class="card">
    <div class="card__title">
      <Icon name="check" :size="16" /> Diagnóstico de biblioteca
      <span v-if="started && !loading" class="hc__badge" :class="`is-${status}`">{{ badge }}</span>
      <button class="btn btn--xs hc__refresh" :disabled="loading" @click="check">
        <Icon name="refresh" :size="14" /> {{ loading ? 'Comprobando…' : started ? 'Volver a comprobar' : 'Ejecutar diagnóstico' }}
      </button>
    </div>

    <p class="hc__intro">
      Revisa servicios, fuentes, identidades y compara los archivos de torrents con los tamaños esperados.
      Es una comprobación manual: no modifica archivos ni repara torrents.
    </p>

    <div v-if="loading" class="hc__loading" role="status">
      <Spinner :size="18" /> Comprobando la biblioteca y los archivos de torrents…
    </div>
    <EmptyState v-else-if="!started" icon="check" title="Diagnóstico pendiente" hint="Pulsa «Ejecutar diagnóstico» cuando quieras revisar los servicios y la integridad de los torrents." />

    <div v-else class="hc__body">
      <div v-if="status === 'incomplete'" class="hc__notice" role="alert">
        <Icon name="alert" :size="15" />
        No se completaron todas las comprobaciones. Revisa el detalle y vuelve a intentarlo; un fallo de consulta no significa que la biblioteca esté limpia.
      </div>
      <p v-if="healthError" class="hc__err">Salud: {{ healthError }}</p>
      <p v-if="integrityError" class="hc__err">Integridad: {{ integrityError }}</p>

      <template v-if="health">
        <p v-if="failedChecks.length" class="hc__err">
          No se pudo verificar: {{ failedChecks.map(([key]) => labels[key] || key).join(', ') }}.
        </p>

        <div class="hc__row" :class="{ 'is-bad': failedChecks.some(([key]) => key === 'services') || serviciosCaidos.length }">
          <div class="hc__ic"><Icon :name="failedChecks.some(([key]) => key === 'services') || serviciosCaidos.length ? 'close' : 'check'" :size="15" /></div>
          <div class="hc__txt">
            <strong>Servicios locales</strong>
            <span v-if="failedChecks.some(([key]) => key === 'services')">No se pudo completar la comprobación de servicios.</span>
            <span v-else-if="serviciosCaidos.length">{{ serviciosCaidos.length }} de {{ services.length }} no responden.</span>
            <span v-else class="hc__ok">Los {{ services.length }} responden.</span>
            <ul class="hc__list hc__svc">
              <li v-for="service in services" :key="service.name" :class="{ 'is-off': !service.online }">
                <Icon :name="service.online ? 'check' : 'close'" :size="12" />
                <b>{{ service.name }}</b> <em>:{{ service.port }} · {{ service.for }}</em>
                <code v-if="!service.online && service.start">{{ service.start }}</code>
              </li>
            </ul>
          </div>
          <button v-if="serviciosCaidos.some((service) => service.name === 'qBittorrent')" class="btn btn--xs" @click="emit('navigate', 'anime')">
            Revisar qBittorrent
          </button>
        </div>

        <div class="hc__row" :class="{ 'is-bad': failedChecks.some(([key]) => key === 'sources') || broken.length }">
          <div class="hc__ic"><Icon :name="failedChecks.some(([key]) => key === 'sources') || broken.length ? 'close' : 'check'" :size="15" /></div>
          <div class="hc__txt">
            <strong>Fuentes</strong>
            <span v-if="failedChecks.some(([key]) => key === 'sources')">No se pudieron verificar las referencias de Suwayomi.</span>
            <span v-else-if="broken.length">{{ broken.length }} obra(s) cuya versión ya no se encuentra en su fuente.</span>
            <span v-else class="hc__ok">No se detectaron fuentes rotas.</span>
            <ul v-if="broken.length" class="hc__list">
              <li v-for="item in broken" :key="item.title + item.kind">
                <b>{{ item.title }}</b> <em>({{ item.kind }})</em> → la fuente ahora da «{{ item.now_title || '?' }}»
              </li>
            </ul>
          </div>
        </div>

        <div v-if="autoheal.length" class="hc__row is-info">
          <div class="hc__ic"><Icon name="refresh" :size="15" /></div>
          <div class="hc__txt">
            <strong>Se re-resuelven al abrir</strong>
            <span>{{ autoheal.length }} obra(s) tienen el id de fuente desplazado; se corrigen al abrirlas.</span>
            <ul class="hc__list"><li v-for="item in autoheal" :key="item.title + item.kind"><b>{{ item.title }}</b> <em>({{ item.kind }})</em></li></ul>
          </div>
        </div>

        <div class="hc__row" :class="{ 'is-warn': unresolved.length || identityFailed.length || failedChecks.some(([key]) => key === 'identity') }">
          <div class="hc__ic"><Icon :name="unresolved.length || identityFailed.length || failedChecks.some(([key]) => key === 'identity') ? 'globe' : 'check'" :size="15" /></div>
          <div class="hc__txt">
            <strong>Identidad MangaDex</strong>
            <span v-if="failedChecks.some(([key]) => key === 'identity')">No se pudo completar la lectura de identidades.</span>
            <span v-else-if="identityFailed.length">No se pudieron leer {{ identityFailed.length }} identidad(es) guardada(s).</span>
            <span v-else-if="unresolved.length">{{ unresolved.length }} obra(s) todavía no tienen identidad verificada.</span>
            <span v-else class="hc__ok">No se detectaron identidades sin resolver.</span>
            <ul v-if="unresolved.length || identityFailed.length" class="hc__list">
              <li v-for="title in unresolved" :key="`unresolved-${title}`"><b>{{ title }}</b> — sin resolver en MangaDex</li>
              <li v-for="title in identityFailed" :key="`failed-${title}`"><b>{{ title }}</b> — no se pudo leer</li>
            </ul>
          </div>
        </div>

        <div v-if="unreachable.length" class="hc__row is-info">
          <div class="hc__ic"><Icon name="globe" :size="15" /></div>
          <div class="hc__txt">
            <strong>Fuentes no consultables</strong>
            <span>{{ unreachable.length }} obra(s) tienen una fuente que no responde ahora; se pueden reintentar cuando vuelva.</span>
            <ul class="hc__list"><li v-for="item in unreachable" :key="item.title"><b>{{ item.title }}</b></li></ul>
          </div>
        </div>

        <div v-if="errorList.length" class="hc__row is-info">
          <div class="hc__ic"><Icon name="spark" :size="15" /></div>
          <div class="hc__txt">
            <strong>Fallos registrados desde el arranque</strong>
            <ul class="hc__list"><li v-for="[area, count] in errorList" :key="area"><b>{{ area }}</b>: {{ count }}</li></ul>
          </div>
        </div>
      </template>

      <div class="hc__row" :class="{ 'is-bad': integrityError || integrity?.available === false || mismatched.length }">
        <div class="hc__ic"><Icon :name="integrityError || integrity?.available === false || mismatched.length ? 'close' : 'check'" :size="15" /></div>
        <div class="hc__txt">
          <strong>Integridad de archivos de torrents</strong>
          <span v-if="integrityError">No se pudo comprobar: {{ integrityError }}</span>
          <span v-else-if="integrity?.available === false">No disponible: {{ integrity.reason || 'qBittorrent no responde.' }}</span>
          <span v-else-if="integrity && !mismatched.length" class="hc__ok">
            {{ integrity.ok }}/{{ integrity.checked }} archivos coinciden con sus torrents.
            <template v-if="integrity.skipped"> {{ integrity.skipped }} sin comprobar por estar movidos o ausentes.</template>
          </span>
          <span v-else-if="mismatched.length">{{ mismatched.length }} archivo(s) no coinciden con el tamaño declarado.</span>
          <span v-else class="hc__ok">Comprobación pendiente.</span>
          <ul v-if="mismatched.length" class="hc__list hc__mismatch">
            <li v-for="item in mismatched" :key="item.file">
              <span :data-tip="item.file">{{ item.file }}</span>
              <b>{{ signed(item.diff) }}</b><em>{{ item.state }}</em>
            </li>
          </ul>
          <span v-if="mismatched.length" class="hc__hint">No se modificó ni re-descargó nada. Si corresponde, revisa el torrent y ejecuta un recheck manual desde qBittorrent.</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hc__badge { font-size: var(--fs-2xs); font-weight: 700; padding: 0.125rem 0.5625rem; border-radius: var(--r-pill); }
.hc__badge.is-healthy { background: color-mix(in srgb, var(--ok) 16%, transparent); color: var(--ok); }
.hc__badge.is-issues { background: color-mix(in srgb, var(--danger) 12%, transparent); color: var(--danger); }
.hc__badge.is-incomplete { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.hc__refresh { margin-left: auto; }
.hc__intro { color: var(--ink-soft); font-size: var(--fs-sm); line-height: 1.5; margin: var(--s-2) 0; }
.card :deep(.empty-state) { min-height: auto; padding: var(--s-3); }
.card :deep(.empty-state__brand) { font-size: 8rem; }
.hc__loading { display: flex; align-items: center; gap: var(--s-2); margin-top: var(--s-3); color: var(--ink-soft); font-size: var(--fs-sm); }
.hc__body { display: flex; flex-direction: column; gap: var(--s-3); margin-top: var(--s-3); }
.hc__notice { display: flex; align-items: flex-start; gap: var(--s-2); color: var(--warn); font-size: var(--fs-sm); line-height: 1.45; }
.hc__err { color: var(--danger); font-size: var(--fs-sm); margin: 0; }
.hc__row { display: flex; gap: var(--s-3); align-items: flex-start; padding: var(--s-3); border-radius: var(--r-md); background: var(--surface-2); }
.hc__row.is-bad { background: color-mix(in srgb, var(--danger) 8%, transparent); }
.hc__row.is-warn { background: color-mix(in srgb, var(--warn) 8%, transparent); }
.hc__ic { flex-shrink: 0; width: 1.6rem; height: 1.6rem; display: grid; place-items: center; border-radius: 50%; background: var(--surface); color: var(--ink-soft); }
.hc__row.is-bad .hc__ic { color: var(--danger); }
.hc__row.is-warn .hc__ic { color: var(--warn); }
.hc__txt { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--s-1); font-size: var(--fs-sm); }
.hc__txt strong { font-size: var(--fs-sm); }
.hc__txt span { color: var(--ink-soft); }
.hc__txt .hc__ok { color: var(--ink-ghost); }
.hc__list { margin: var(--s-1) 0 0; padding-inline-start: var(--s-3); display: flex; flex-direction: column; gap: var(--s-1); font-size: var(--fs-xs); color: var(--ink-soft); }
.hc__list li { list-style: disc; }
.hc__list em { color: var(--ink-ghost); font-style: normal; }
.hc__svc { padding-inline-start: 0; gap: var(--s-1); }
.hc__svc li { list-style: none; display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.hc__svc li.is-off { color: var(--danger); }
.hc__svc code { font-size: var(--fs-2xs); padding: 0 var(--s-1); border-radius: var(--r-xs); background: var(--surface); border: 0.0625rem solid var(--line); color: var(--ink-soft); }
.hc__mismatch { padding: 0; gap: var(--s-1); }
.hc__mismatch li { list-style: none; display: flex; gap: var(--s-2); align-items: center; min-width: 0; }
.hc__mismatch li span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.hc__mismatch li b { color: var(--danger); font-family: var(--font-mono); font-size: var(--fs-2xs); flex-shrink: 0; }
.hc__mismatch li em { font-size: var(--fs-2xs); flex-shrink: 0; }
.hc__hint { font-size: var(--fs-xs); color: var(--ink-faint) !important; }
@media (max-width: 40rem) {
  .hc__row { flex-wrap: wrap; }
  .hc__txt { flex-basis: calc(100% - 2.2rem); }
  .hc__refresh { font-size: var(--fs-2xs); }
}
</style>
