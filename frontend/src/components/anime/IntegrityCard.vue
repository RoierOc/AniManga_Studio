<script setup>
// Integridad de la biblioteca: ¿lo que hay en disco sigue siendo lo que el torrent dice?
//
// Los vídeos SON el contenido que qBittorrent siembra. Cualquier escritura encima rompe el hash
// en silencio: qBittorrent sigue diciendo "seeding" tan tranquilo y sólo te enteras cuando un
// recheck te obliga a re-descargar varios GB. Este chequeo encontró 2 archivos rotos (de 137)
// que llevaban semanas así sin que nada avisara.
//
// Sólo lee. No dispara rechecks: eso implica re-descargar y lo decide el usuario.
import { computed, ref } from 'vue'
import { api } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'

const data = ref(null)
const loading = ref(false)
const err = ref('')

async function check() {
  loading.value = true; err.value = ''
  try {
    data.value = await api.get('/api/storage/integrity')
    if (data.value && data.value.available === false) err.value = data.value.reason || 'No disponible'
  } catch (e) {
    err.value = e?.body || 'No se pudo comprobar'
  } finally {
    loading.value = false
  }
}

const bad = computed(() => data.value?.mismatched || [])
const clean = computed(() => data.value?.available && bad.value.length === 0)
const signed = (n) => (n > 0 ? `+${formatBytes(n)}` : `−${formatBytes(-n)}`)
</script>

<template>
  <section class="card">
    <div class="card__title">
      <Icon name="check" :size="16" /> Integridad de la biblioteca
      <span v-if="data?.available" class="int__tot" :class="{ 'is-bad': bad.length }">
        {{ data.ok }}/{{ data.checked }}
      </span>
      <button class="btn btn--xs" :disabled="loading" @click="check">
        <Icon name="refresh" :size="13" /> {{ loading ? 'Comprobando…' : 'Comprobar' }}
      </button>
    </div>

    <p v-if="!data && !err" class="hint">
      Compara cada archivo con el tamaño que declara su torrent. Si no coincide, algo lo reescribió
      y qBittorrent ya no puede sembrarlo — aunque siga diciendo que sí.
    </p>

    <p v-if="err" class="hint hint--warn">{{ err }}</p>

    <p v-else-if="clean" class="int__ok">
      <Icon name="check" :size="14" /> Los {{ data.checked }} archivos coinciden con su torrent.
      <template v-if="data.skipped"> ({{ data.skipped }} sin comprobar: movidos o borrados.)</template>
    </p>

    <template v-else-if="bad.length">
      <div v-for="b in bad" :key="b.file" class="int__row">
        <span class="int__name" :title="b.file">{{ b.file }}</span>
        <span class="int__diff">{{ signed(b.diff) }}</span>
        <span class="int__state">{{ b.state }}</span>
      </div>
      <p class="hint">
        Estos archivos ya no son los que el torrent describe, así que <strong>no se están sembrando
        de verdad</strong> pese al estado. Se arreglan con un <em>force recheck</em> + re-descarga
        en qBittorrent, que además te devuelve el archivo original intacto.
      </p>
    </template>
  </section>
</template>

<style scoped>
/* .card/.btn/.hint son scoped en SettingsView y no llegan aquí: se replican con los mismos tokens. */
.card { padding: var(--s-5); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.card__title { display: flex; align-items: center; gap: var(--s-2); font-weight: 600; color: var(--ink); margin-bottom: var(--s-3); }
.card__title :deep(svg) { color: var(--azure); }
.btn { display: inline-flex; align-items: center; gap: 0.375rem; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.btn:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn--xs { padding: 3px var(--s-2); font-size: var(--fs-2xs); }
.hint { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: var(--s-2); line-height: 1.5; }
.hint--warn { color: var(--warn); }

.int__tot { margin-left: auto; font-family: var(--font-mono); font-size: var(--fs-xs); color: var(--jade); }
.int__tot.is-bad { color: var(--coral); }
.int__ok { display: flex; align-items: center; gap: 0.375rem; font-size: var(--fs-sm); color: var(--jade); }
.int__row { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); margin-bottom: 0.375rem;
            border-radius: var(--r-sm); background: var(--base);
            border: 1px solid color-mix(in srgb, var(--coral) 32%, transparent); }
.int__name { flex: 1; min-width: 0; font-size: var(--fs-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.int__diff { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--coral); flex-shrink: 0; }
.int__state { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); flex-shrink: 0; }
</style>
