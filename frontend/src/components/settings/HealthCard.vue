<script setup>
// Salud de la biblioteca de manga: reúne en un sitio los problemas que antes solo se veían en
// barridos manuales o en silencio, con reparación de un clic.
//   · Fuentes cruzadas: el mangaId de Suwayomi derivó a otra obra (Fijar/origen leían otra cosa).
//   · Identidad MangaDex sin verificar: la obra no casa por título/AniList → sin avisos de novedades.
//   · Fallos recientes: contador de errores por costura desde el arranque (observabilidad).
// El chequeo es de solo lectura y rápido; reparar sí ejecuta los fixers (pueden tocar la red).
import { computed, ref, onMounted } from 'vue'
import { api } from '@/lib/api'
import Icon from '@/components/ui/Icon.vue'

const data = ref(null)
const loading = ref(false)
const repairing = ref(false)
const err = ref('')
const done = ref('')

async function check() {
  loading.value = true; err.value = ''; done.value = ''
  try {
    data.value = await api.get('/api/health/check')
  } catch (e) {
    err.value = e?.body || 'No se pudo comprobar'
  } finally {
    loading.value = false
  }
}

async function repair(what) {
  repairing.value = true; err.value = ''; done.value = ''
  try {
    const d = await api.post(`/api/health/repair?what=${what}`, {})
    const parts = []
    if (d.sources) parts.push(`${d.sources.fixed || 0} fuente(s) reparada(s)`)
    if (d.identity) parts.push(`${d.identity.fixed || 0} identidad(es) corregida(s)`)
    done.value = parts.join(' · ') || 'Sin cambios'
    await check()
  } catch (e) {
    err.value = e?.body || 'No se pudo reparar'
  } finally {
    repairing.value = false
  }
}

const broken = computed(() => data.value?.sources?.broken || [])
const autoheal = computed(() => data.value?.sources?.autoheal || [])
const unreachable = computed(() => data.value?.sources?.unreachable || [])
const unresolved = computed(() => data.value?.identity?.unresolved || [])
const errorList = computed(() => Object.entries(data.value?.errors || {}))
const problems = computed(() => data.value?.problems ?? null)
const healthy = computed(() => data.value && problems.value === 0)

onMounted(check)
</script>

<template>
  <section class="card">
    <div class="card__title">
      <Icon name="check" :size="16" /> Salud de la biblioteca
      <span v-if="data" class="hc__badge" :class="healthy ? 'is-ok' : 'is-warn'">
        {{ healthy ? 'Todo en orden' : `${problems} problema(s)` }}
      </span>
      <button class="btn btn--xs hc__refresh" :disabled="loading" @click="check" title="Volver a comprobar">
        <Icon name="refresh" :size="14" /> {{ loading ? 'Comprobando…' : 'Comprobar' }}
      </button>
    </div>

    <p v-if="err" class="hc__err">{{ err }}</p>
    <p v-if="done" class="hc__done"><Icon name="check" :size="13" /> {{ done }}</p>

    <div v-if="data" class="hc__body">
      <!-- Fuentes rotas (no re-resolubles = problema real) -->
      <div class="hc__row" :class="{ 'is-bad': broken.length }">
        <div class="hc__ic"><Icon :name="broken.length ? 'close' : 'check'" :size="15" /></div>
        <div class="hc__txt">
          <strong>Fuentes rotas</strong>
          <span v-if="broken.length">{{ broken.length }} obra(s) cuya versión ya no se encuentra en su fuente (no se puede localizar).</span>
          <span v-else class="hc__ok">Toda obra con problema de fuente es localizable.</span>
          <ul v-if="broken.length" class="hc__list">
            <li v-for="c in broken" :key="c.title + c.kind">
              <b>{{ c.title }}</b> <em>({{ c.kind }})</em> → la fuente ahora da «{{ c.now_title || '?' }}»
            </li>
          </ul>
        </div>
      </div>

      <!-- Auto-reparables (informativo): el id de Suwayomi derivó pero se re-resuelve solo al abrir -->
      <div v-if="autoheal.length" class="hc__row is-info">
        <div class="hc__ic"><Icon name="refresh" :size="15" /></div>
        <div class="hc__txt">
          <strong>Se re-resuelven al abrir</strong>
          <span>{{ autoheal.length }} obra(s) con el id de fuente desplazado (normal: Suwayomi reasigna ids). Se corrigen solas al abrirlas; «Refrescar anclas» las deja apuntando por url exacta.</span>
          <ul class="hc__list"><li v-for="c in autoheal" :key="c.title + c.kind"><b>{{ c.title }}</b> <em>({{ c.kind }})</em></li></ul>
        </div>
        <button class="btn btn--xs" :disabled="repairing" @click="repair('sources')">Refrescar anclas</button>
      </div>

      <!-- Identidad MangaDex -->
      <div class="hc__row" :class="{ 'is-warn': unresolved.length }">
        <div class="hc__ic"><Icon :name="unresolved.length ? 'globe' : 'check'" :size="15" /></div>
        <div class="hc__txt">
          <strong>Identidad MangaDex</strong>
          <span v-if="unresolved.length">{{ unresolved.length }} obra(s) sin verificar en MangaDex (no reciben avisos de capítulos nuevos).</span>
          <span v-else class="hc__ok">Todas las obras resueltas casan con MangaDex.</span>
          <ul v-if="unresolved.length" class="hc__list">
            <li v-for="t in unresolved" :key="t"><b>{{ t }}</b></li>
          </ul>
        </div>
        <button v-if="unresolved.length" class="btn btn--xs" :disabled="repairing" @click="repair('identity')">Re-resolver</button>
      </div>

      <!-- Fuentes no consultables (informativo, no reparable) -->
      <div v-if="unreachable.length" class="hc__row is-info">
        <div class="hc__ic"><Icon name="globe" :size="15" /></div>
        <div class="hc__txt">
          <strong>Fuentes no consultables</strong>
          <span>{{ unreachable.length }} obra(s) con la fuente caída ahora mismo — no es un cruce, se reintenta al volver la fuente.</span>
          <ul class="hc__list"><li v-for="c in unreachable" :key="c.title"><b>{{ c.title }}</b></li></ul>
        </div>
      </div>

      <!-- Errores recientes -->
      <div v-if="errorList.length" class="hc__row is-info">
        <div class="hc__ic"><Icon name="spark" :size="15" /></div>
        <div class="hc__txt">
          <strong>Fallos registrados desde el arranque</strong>
          <ul class="hc__list"><li v-for="[c, n] in errorList" :key="c"><b>{{ c }}</b>: {{ n }}</li></ul>
        </div>
      </div>

      <div v-if="!healthy" class="hc__all">
        <button class="btn btn--sm" :disabled="repairing" @click="repair('sources,identity')">
          <Icon name="refresh" :size="14" /> {{ repairing ? 'Reparando…' : 'Reparar todo' }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hc__badge { font-size: var(--fs-2xs); font-weight: 700; padding: 2px 9px; border-radius: var(--r-pill); }
.hc__badge.is-ok { background: rgba(52,199,120,.16); color: #4ad07f; }
.hc__badge.is-warn { background: rgba(240,170,60,.16); color: #f0aa3c; }
.hc__refresh { margin-left: auto; }
.hc__err { color: var(--danger, #e5484d); font-size: var(--fs-sm); margin: var(--s-2) 0; }
.hc__done { color: #4ad07f; font-size: var(--fs-sm); display: flex; align-items: center; gap: 6px; margin: var(--s-2) 0; }
.hc__body { display: flex; flex-direction: column; gap: var(--s-3); margin-top: var(--s-3); }
.hc__row { display: flex; gap: var(--s-3); align-items: flex-start; padding: var(--s-3); border-radius: var(--r-md); background: var(--surface-2); }
.hc__row.is-bad { background: rgba(229,72,77,.08); }
.hc__row.is-warn { background: rgba(240,170,60,.08); }
.hc__ic { flex-shrink: 0; width: 1.6rem; height: 1.6rem; display: grid; place-items: center; border-radius: 50%; background: var(--surface); color: var(--ink-soft); }
.hc__row.is-bad .hc__ic { color: #e5484d; }
.hc__row.is-warn .hc__ic { color: #f0aa3c; }
.hc__txt { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; font-size: var(--fs-sm); }
.hc__txt strong { font-size: var(--fs-sm); }
.hc__txt span { color: var(--ink-soft); }
.hc__ok { color: var(--ink-ghost) !important; }
.hc__list { margin: 4px 0 0; padding-left: var(--s-3); display: flex; flex-direction: column; gap: 2px; font-size: var(--fs-xs); color: var(--ink-soft); }
.hc__list li { list-style: disc; }
.hc__list em { color: var(--ink-ghost); font-style: normal; }
.hc__all { margin-top: var(--s-2); }
</style>
