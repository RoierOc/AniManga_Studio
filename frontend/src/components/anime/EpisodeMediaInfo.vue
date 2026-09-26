<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import ErrorState from '@/components/ui/ErrorState.vue'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  animeId: { type: [String, Number], required: true },
  episodeKey: { type: String, required: true },
  episodeTitle: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const state = ref('loading')
const info = ref(null)
const error = ref('')

function durationLabel(value) {
  const seconds = Math.floor(Number(value) || 0)
  if (!seconds) return 'Desconocida'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

async function load() {
  state.value = 'loading'
  error.value = ''
  try {
    info.value = await api.get(`/api/anime/media_info/${encodeURIComponent(props.animeId)}/${encodeURIComponent(props.episodeKey)}`)
    state.value = 'ready'
  } catch (cause) {
    error.value = cause?.message || 'No se pudo leer la ficha técnica.'
    state.value = 'error'
  }
}

function onKey(event) { if (event.key === 'Escape') emit('close') }
onMounted(() => { window.addEventListener('keydown', onKey); load() })
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div class="emi" role="presentation" @click.self="emit('close')">
      <section class="emi__panel" role="dialog" aria-modal="true" aria-labelledby="emi-title" :data-state="state">
        <header class="emi__head">
          <div class="emi__heading">
            <span class="emi__icon"><Icon name="film" :size="17" /></span>
            <div>
              <h2 id="emi-title">Información del archivo</h2>
              <p>{{ episodeTitle || `Episodio ${episodeKey}` }}</p>
            </div>
          </div>
          <button class="emi__close" data-tip="Cerrar" aria-label="Cerrar" @click="emit('close')">
            <Icon name="close" :size="16" />
          </button>
        </header>

        <div v-if="state === 'loading'" class="emi__loading" role="status">
          <Spinner :size="22" /> <span>Leyendo el archivo…</span>
        </div>
        <ErrorState v-else-if="state === 'error'" title="No se pudo leer el archivo" :detail="error" @retry="load" />
        <div v-else-if="state === 'ready' && info" class="emi__body">
          <div class="emi__facts">
            <div><span>Resolución</span><strong>{{ info.video.width || '—' }} × {{ info.video.height || '—' }}</strong></div>
            <div><span>Vídeo</span><strong>{{ info.video.codec }}</strong></div>
            <div><span>Contenedor</span><strong>{{ info.container || '—' }}</strong></div>
            <div><span>Duración</span><strong>{{ durationLabel(info.duration_seconds) }}</strong></div>
            <div><span>Tamaño</span><strong>{{ formatBytes(info.size_bytes) }}</strong></div>
          </div>

          <section class="emi__tracks">
            <h3>Audio · {{ info.audio_tracks.length }}</h3>
            <ul v-if="info.audio_tracks.length">
              <li v-for="(track, i) in info.audio_tracks" :key="`a${i}`">
                <span>{{ track.title || track.language || `Pista ${i + 1}` }}</span>
                <small>{{ [track.codec, track.language, track.channels ? `${track.channels} canales` : ''].filter(Boolean).join(' · ') }}</small>
              </li>
            </ul>
            <p v-else class="emi__none">Sin pistas de audio</p>
          </section>

          <section class="emi__tracks">
            <h3>Subtítulos · {{ info.subtitle_tracks.length }}</h3>
            <ul v-if="info.subtitle_tracks.length">
              <li v-for="(track, i) in info.subtitle_tracks" :key="`s${i}`">
                <span>{{ track.title || track.language || `Pista ${i + 1}` }}</span>
                <small>{{ [track.codec, track.language].filter(Boolean).join(' · ') }}</small>
              </li>
            </ul>
            <p v-else class="emi__none">Sin pistas de subtítulos</p>
          </section>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.emi { position: fixed; inset: 0; z-index: 130; display: grid; place-items: center; padding: var(--s-4); background: rgba(4, 6, 12, .72); backdrop-filter: blur(5px); }
.emi__panel { width: min(34rem, 100%); max-height: min(85vh, 46rem); overflow: auto; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); }
.emi__head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); padding: var(--s-4); border-bottom: 1px solid var(--line); }
.emi__heading { display: flex; align-items: center; gap: var(--s-3); min-width: 0; }
.emi__icon { width: 2.25rem; height: 2.25rem; display: grid; place-items: center; flex: 0 0 auto; color: var(--azure-bright); background: var(--azure-haze); border-radius: var(--r-sm); }
.emi__head h2 { font-size: var(--fs-md); font-weight: 650; }
.emi__head p { margin-top: var(--s-1); color: var(--ink-faint); font-size: var(--fs-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.emi__close { width: 2rem; height: 2rem; display: grid; place-items: center; flex: 0 0 auto; border-radius: var(--r-sm); color: var(--ink-faint); }
.emi__close:hover { color: var(--ink); background: var(--surface-2); }
.emi__loading { min-height: 10rem; display: flex; align-items: center; justify-content: center; gap: var(--s-3); color: var(--ink-faint); font-size: var(--fs-sm); }
.emi__body { display: grid; gap: var(--s-4); padding: var(--s-4); }
.emi__facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s-2); }
.emi__facts > div { min-width: 0; display: grid; gap: var(--s-1); padding: var(--s-3); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-sm); }
.emi__facts span { color: var(--ink-faint); font-size: var(--fs-2xs); }
.emi__facts strong { color: var(--ink); font-size: var(--fs-sm); overflow-wrap: anywhere; }
.emi__tracks h3 { margin-bottom: var(--s-2); color: var(--ink-soft); font-size: var(--fs-xs); font-weight: 650; }
.emi__tracks ul { display: grid; gap: var(--s-1); margin: 0; padding: 0; list-style: none; }
.emi__tracks li { display: flex; justify-content: space-between; gap: var(--s-3); padding: var(--s-2) var(--s-3); background: var(--surface); border-radius: var(--r-xs); font-size: var(--fs-xs); }
.emi__tracks li span { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.emi__tracks small, .emi__none { color: var(--ink-faint); font-size: var(--fs-2xs); }
.emi__none { padding: var(--s-2) 0; }
@media (max-width: 38rem) { .emi { padding: var(--s-2); } .emi__facts { grid-template-columns: 1fr 1fr; } .emi__tracks li { flex-direction: column; gap: var(--s-1); } }
</style>
