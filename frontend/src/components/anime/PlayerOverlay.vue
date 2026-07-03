<script setup>
/* Player web embebido (estilo Crunchyroll) — overlay a pantalla completa dentro
 * de la app. HLS del backend (remux sin pérdida), subtítulos ASS fieles vía
 * JASSUB (libass WASM + fuentes del MKV), controles propios mouse-first con
 * auto-hide, resume/visto sincronizados con el backend (misma semántica que MPV). */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Hls from 'hls.js'
import JASSUB from 'jassub'
// OJO con las rutas: el worker REAL es dist/worker/worker.js (módulo ES que
// jassub lanza con type:'module'); dist/wasm/jassub-worker.js es solo el
// pegamento emscripten (cargado como worker muere en silencio: espera llamarse
// 'em-pthread'). Y sin modernWasmUrl los navegadores SIMD piden una variante
// por una ruta que no existe en el bundle.
import jassubWorkerUrl from 'jassub/dist/worker/worker.js?worker&url'
import jassubWasmUrl from 'jassub/dist/wasm/jassub-worker.wasm?url'
import jassubModernWasmUrl from 'jassub/dist/wasm/jassub-worker-modern.wasm?url'
import { useAnimeStore } from '@/stores/anime'
import { useUiStore } from '@/stores/ui'
import { Anime4KRenderer, A4K_MODES } from '@/lib/anime4k'
import { animeEpLabel } from '@/lib/anime'
import { imgProxy } from '@/lib/img'
import { api } from '@/lib/api'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useAnimeStore()
const p = computed(() => store.player)

const wrap = ref(null)          // contenedor (fullscreen target)
const videoEl = ref(null)

/* ── estado de reproducción ── */
const playing = ref(false)
const time = ref(0)
const duration = ref(0)
const buffered = ref(0)
const volume = ref(parseFloat(localStorage.getItem('anime-player-vol') ?? '1'))
const muted = ref(false)
const speed = ref(1)
const isFs = ref(false)
const waiting = ref(false)      // buffering spinner

/* ── UI ── */
const uiVisible = ref(true)
const menuOpen = ref('')        // '' | 'subs' | 'audio' | 'speed'
let hideTimer = null

/* ── panel de episodios (drawer lateral, cambiar sin salir del player) ── */
const epPanel = ref(false)
const panelEps = computed(() => {
  const eps = (p.value?.anime?.episodes || []).filter(e => e.num > 0 && e.ep_type !== 'special')
  return [...eps].sort((a, b) => a.num - b.num)
})
const isPlayable = (e) => e.in_local || (e.in_qbt && e.progress >= 100)
function playFromPanel(e) {
  if (!isPlayable(e) || e.num === p.value.ep.num) return
  sendProgress(false, true)
  store.openPlayer(p.value.anime, e)
}
// al abrir, centrar el episodio actual en la lista
watch(epPanel, async (open) => {
  if (!open) return
  await nextTick()
  document.querySelector('.wp__ep.is-cur')?.scrollIntoView({ block: 'center' })
})

/* ── subs ── */
const subTracks = computed(() => p.value?.sess?.sub_tracks || [])
const audioTracks = computed(() => p.value?.sess?.audio_tracks || [])
const subIndex = ref(-1)        // -1 = sin subtítulos
const audioIndex = ref(0)
const SKIP_SECS = 88

/* ── next-episode countdown (tarjeta con miniatura + anillo, como Crunchyroll) ── */
const NEXT_CD_SECS = 5
const nextCd = ref(0)            // segundos restantes (float: el anillo es suave)
const nextEp = ref(null)         // { anime, ep } del episodio que viene
const nextThumbFailed = ref(false)
let nextTimer = null
const tcDismissed = ref(false)
// anillo SVG: r=17 → circunferencia ≈ 106.8; el offset crece al agotarse el tiempo
const ringOff = computed(() => 106.8 * (1 - Math.max(0, nextCd.value) / NEXT_CD_SECS))

// Watchdog de decodificación: el navegador puede ACEPTAR un códec (MSE) y aun
// así no poder decodificarlo (HEVC sin decode por hardware) → se quedaría
// "cargando" para siempre. Si en 8 s no llega ni un frame, ofrecemos salidas.
const decodeFailed = ref(false)
let decodeTimer = null
function armDecodeWatchdog() {
  clearTimeout(decodeTimer)
  decodeFailed.value = false
  decodeTimer = setTimeout(() => {
    const v = videoEl.value
    if (v && p.value && !p.value.error && v.currentTime < 0.2 && v.readyState < 2) {
      decodeFailed.value = true
      try { v.pause() } catch (_) {}
    }
  }, 8000)
}
function retryTranscode() {
  const { anime, ep } = p.value
  store.openPlayer(anime, ep, 0, audioIndex.value, true)
}

let hls = null
let jassub = null
let progressTimer = null
let lastSentPos = -1

/* ── Anime4K (WebGPU) ── */
const a4kCanvas = ref(null)
const a4kMode = ref(localStorage.getItem('anime-a4k') || 'off')
const a4kActive = ref(false)          // pipeline corriendo (canvas visible)
const a4kAvailable = Anime4KRenderer.supported()
const a4k = new Anime4KRenderer()
a4k.onFatal = () => { a4kActive.value = false }   // canvas fuera, vídeo visible
window.__a4kr = a4k   // gancho de depuración (frames renderizados, device)
const canvasRect = ref({ left: 0, top: 0, width: 0, height: 0 })

async function setA4kMode(id) {
  a4kMode.value = id
  localStorage.setItem('anime-a4k', id)
  menuOpen.value = ''
  await applyA4k()
}

/* El canvas debe calcar el rectángulo REAL del vídeo (object-fit: contain
 * deja franjas) — se recalcula en metadata/resize/fullscreen. */
function updateCanvasRect() {
  const v = videoEl.value
  if (!v || !v.videoWidth) return
  const cw = v.clientWidth, ch = v.clientHeight
  const scale = Math.min(cw / v.videoWidth, ch / v.videoHeight)
  const w = v.videoWidth * scale, h = v.videoHeight * scale
  canvasRect.value = { left: (cw - w) / 2, top: (ch - h) / 2, width: w, height: h }
}

async function applyA4k() {
  const v = videoEl.value
  if (!v || !a4kCanvas.value) return
  if (a4kMode.value === 'off' || !a4kAvailable || !v.videoWidth) {
    a4k.stop()
    a4kActive.value = false
    return
  }
  updateCanvasRect()
  const wasPlaying = !v.paused
  const dpr = window.devicePixelRatio || 1
  try {
    const ok = await a4k.start(v, a4kCanvas.value, a4kMode.value,
      canvasRect.value.width * dpr, canvasRect.value.height * dpr)
    a4kActive.value = ok
    if (!ok) console.warn('[a4k] no arrancó (adapter/preset no disponible)')
  } catch (e) {
    a4kActive.value = false
    console.error('[a4k] error al arrancar:', e)
    api.post('/api/stream/caps', { a4k_error: String(e?.message || e) }).catch(() => {})
  }
  // Chromium pausa el vídeo internamente (una vez) al crear el device WebGPU
  // sobre él — reanudar si estaba reproduciendo. Un pequeño delay porque la
  // pausa llega asíncrona tras el arranque del pipeline.
  if (wasPlaying) {
    setTimeout(() => { if (p.value && v.paused && !v.ended) v.play().catch(() => {}) }, 400)
    setTimeout(() => { if (p.value && v.paused && !v.ended) v.play().catch(() => {}) }, 1500)
  }
  // Medidor honesto: si el tier elegido no da ni 12 fps en esta GPU, avisar
  // (los CNN pueden con una dedicada, la iGPU necesita el modo Ligero).
  if (a4kActive.value) {
    const f0 = a4k.frames
    setTimeout(() => {
      if (!a4kActive.value || !p.value || videoEl.value?.paused) return
      const fps = (a4k.frames - f0) / 6
      if (fps < 12) {
        useUiStore().toast(`Anime4K ${a4kMode.value} va a ${fps.toFixed(0)} fps en tu GPU — prueba el modo Ligero`, 'warn', 5000)
      }
    }, 6000)
  }
}

const title = computed(() => p.value ? animeEpLabel(p.value.anime, p.value.ep) : '')

/* ═══ ciclo de vida de la sesión ═══ */
watch(() => p.value?.sess, async (sess) => {
  if (!sess) return
  await nextTick()
  setup(sess)
})

function setup(sess) {
  teardownMedia()
  const v = videoEl.value
  if (!v) return
  duration.value = sess.duration || 0
  audioIndex.value = p.value.audio || 0

  const resume = p.value?.startPos > 0 ? p.value.startPos : (sess.resume_pos || 0)
  // startPosition explícito: el playlist es tipo EVENT (crece mientras ffmpeg
  // remuxa) y sin esto hls.js lo trata como "live" y arranca por el final →
  // el player se queda cargando sin imagen.
  hls = new Hls({ maxBufferLength: 60, maxMaxBufferLength: 120,
                  startPosition: resume > 5 ? resume : 0 })
  hls.loadSource(sess.playlist)
  hls.attachMedia(v)
  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    v.volume = volume.value
    v.playbackRate = speed.value
    v.play().catch(() => {})
  })
  hls.on(Hls.Events.ERROR, (_e, data) => {
    if (data.fatal) {
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad()
      else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError()
    }
  })

  // Subs por defecto: español si existe, si no la primera pista
  const subs = sess.sub_tracks || []
  const esIdx = subs.findIndex(t => /^(spa|es)/i.test(t.lang) || /(español|spanish|latin)/i.test(t.title))
  setSubTrack(esIdx >= 0 ? esIdx : (subs.length ? 0 : -1))

  progressTimer = setInterval(() => sendProgress(false), 10000)
  armDecodeWatchdog()

  // Anime4K: arranca cuando se conocen las dimensiones del vídeo
  v.addEventListener('loadedmetadata', () => { updateCanvasRect(); applyA4k() }, { once: true })
  window.addEventListener('resize', updateCanvasRect)
  poke()
}

async function setSubTrack(idx) {
  subIndex.value = idx
  menuOpen.value = ''
  if (jassub) { try { jassub.destroy() } catch (_) {} jassub = null }
  const v = videoEl.value
  if (v) [...v.querySelectorAll('track')].forEach(t => t.remove())
  if (idx < 0 || !p.value?.sess) return
  try {
    const r = await api.post('/api/stream/subs', { path: p.value.sess.path, index: idx })
    if (!p.value) return
    if (r.format === 'ass') {
      jassub = new JASSUB({
        video: videoEl.value,
        subUrl: r.url,
        workerUrl: jassubWorkerUrl,
        wasmUrl: jassubWasmUrl,
        modernWasmUrl: jassubModernWasmUrl,
        fonts: r.fonts || [],
      })
      // El worker de jassub muere en silencio si algo falla (wasm, fuentes…):
      // vigilar el handshake y dejar rastro en consola + log del backend.
      window.__jassub = jassub   // gancho de depuración (app local)
      const jsRef = jassub
      Promise.race([
        jsRef.ready,
        new Promise((_, rej) => setTimeout(() => rej(new Error('timeout 10s')), 10000)),
      ]).then(
        () => console.log('[subs] jassub listo'),
        (e) => {
          if (jassub !== jsRef) return   // ya se cambió de pista
          console.error('[subs] jassub falló:', e)
          api.post('/api/stream/caps', { jassub_error: String(e?.message || e) }).catch(() => {})
        },
      )
    } else {
      // vtt (el backend convierte srt→vtt) → pista nativa del <video>
      const track = document.createElement('track')
      track.kind = 'subtitles'; track.default = true
      track.src = r.url
      videoEl.value.appendChild(track)
      track.track.mode = 'showing'
    }
  } catch (_) {}
}

async function setAudioTrack(idx) {
  if (idx === audioIndex.value || !p.value) return
  menuOpen.value = ''
  const pos = videoEl.value?.currentTime || 0
  const { anime, ep } = p.value
  await store.openPlayer(anime, ep, pos, idx)
}

function teardownMedia() {
  a4k.stop()
  a4kActive.value = false
  window.removeEventListener('resize', updateCanvasRect)
  if (decodeTimer) { clearTimeout(decodeTimer); decodeTimer = null }
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null }
  if (nextTimer) { clearInterval(nextTimer); nextTimer = null; nextCd.value = 0 }
  if (jassub) { try { jassub.destroy() } catch (_) {} jassub = null }
  if (hls) { try { hls.destroy() } catch (_) {} hls = null }
}

function close() {
  sendProgress(false, true)
  teardownMedia()
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
  store.closePlayer()
}

function sendProgress(ended, flush = false) {
  const v = videoEl.value
  if (!v || !p.value) return
  const pos = ended ? (duration.value || v.duration || 0) : v.currentTime
  if (!ended && !flush && Math.abs(pos - lastSentPos) < 5) return
  lastSentPos = pos
  api.post('/api/stream/progress', {
    anime_id: p.value.anime.id, episode: p.value.ep.num,
    position: pos, duration: duration.value || v.duration || 0, ended,
  }).catch(() => {})
}

/* ═══ eventos del <video> ═══ */
function onTime() {
  const v = videoEl.value
  if (!v) return
  if (v.currentTime > 0.2 && decodeTimer) { clearTimeout(decodeTimer); decodeTimer = null; decodeFailed.value = false }
  time.value = v.currentTime
  if (v.duration && isFinite(v.duration)) duration.value = v.duration
  try {
    const b = v.buffered
    buffered.value = b.length ? b.end(b.length - 1) : 0
  } catch (_) {}
}
function onEnded() {
  playing.value = false
  sendProgress(true)
  const nxt = store.playerNext()
  if (!nxt) return
  nextEp.value = nxt
  nextThumbFailed.value = false
  nextCd.value = NEXT_CD_SECS
  nextTimer = setInterval(() => {
    nextCd.value -= 0.1
    if (nextCd.value <= 0) {
      clearInterval(nextTimer); nextTimer = null
      nextCd.value = 0
      store.openPlayer(nxt.anime, nxt.ep)
    }
  }, 100)
}
function cancelNext() {
  if (nextTimer) { clearInterval(nextTimer); nextTimer = null }
  nextCd.value = 0
  nextEp.value = null
}
function goNext() {
  cancelNext()
  const nxt = store.playerNext()
  if (nxt) { sendProgress(false, true); store.openPlayer(nxt.anime, nxt.ep) }
}

/* ═══ controles ═══ */
/* Feedback central efímero (icono play/pausa que se expande y desvanece) y
 * burbujas laterales de seek — los micro-gestos estándar de un player web. */
const ripple = ref('')
const rippleKey = ref(0)
let rippleTimer = null
function showRipple(icon) {
  ripple.value = icon
  rippleKey.value++
  clearTimeout(rippleTimer)
  rippleTimer = setTimeout(() => { ripple.value = '' }, 550)
}
const seekBubble = ref(null)     // { side: 'left'|'right', secs, key }
let bubbleTimer = null
function showSeekBubble(secs) {
  seekBubble.value = { side: secs < 0 ? 'left' : 'right', secs, key: (seekBubble.value?.key || 0) + 1 }
  clearTimeout(bubbleTimer)
  bubbleTimer = setTimeout(() => { seekBubble.value = null }, 650)
}

function togglePlay() {
  const v = videoEl.value
  if (!v) return
  if (v.paused) { v.play().catch(() => {}); showRipple('play') }
  else { v.pause(); showRipple('pause') }
}
function seekTo(ev) {
  const v = videoEl.value
  if (!v || !duration.value) return
  const r = ev.currentTarget.getBoundingClientRect()
  const frac = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width))
  v.currentTime = frac * duration.value
  poke()
}

/* ── preview de la timeline (miniatura + tiempo al hacer hover) ── */
const tlHover = ref(-1)          // segundos bajo el cursor; -1 = sin hover
const tlLeft = ref(0)            // posición X del preview dentro de la barra
const thumbOk = ref(false)       // la miniatura del índice actual cargó
function onTlHover(ev) {
  if (!duration.value) return
  const r = ev.currentTarget.getBoundingClientRect()
  const frac = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width))
  tlHover.value = frac * duration.value
  // clamp para que la tarjeta (≈10rem) no se salga de la barra
  const half = 88
  tlLeft.value = Math.min(r.width - half, Math.max(half, ev.clientX - r.left))
}
const thumbUrl = computed(() => {
  const t = p.value?.sess?.thumbs
  if (!t || tlHover.value < 0) return ''
  const idx = Math.max(1, Math.floor(tlHover.value / t.interval) + 1)
  return `${t.url}/t_${String(idx).padStart(5, '0')}.jpg`
})
// las miniaturas se generan en segundo plano: si aún no existe, solo el tiempo
watch(thumbUrl, () => { thumbOk.value = false })
function skip(secs) {
  const v = videoEl.value
  if (v) v.currentTime = Math.min(Math.max(0, v.currentTime + secs), duration.value || 1e9)
  showSeekBubble(secs)
  poke()
}
function setVolume(ev) {
  const val = parseFloat(ev.target.value)
  volume.value = val
  muted.value = val === 0
  const v = videoEl.value
  if (v) { v.volume = val; v.muted = muted.value }
  localStorage.setItem('anime-player-vol', String(val))
}
function toggleMute() {
  muted.value = !muted.value
  if (videoEl.value) videoEl.value.muted = muted.value
}
function setSpeed(s) {
  speed.value = s
  menuOpen.value = ''
  if (videoEl.value) videoEl.value.playbackRate = s
}
function toggleFs() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
  else wrap.value?.requestFullscreen().catch(() => {})
}
function onFsChange() {
  isFs.value = !!document.fullscreenElement
  requestAnimationFrame(() => {
    updateCanvasRect()
    // el pipeline computa a la resolución visible: al cambiar fullscreen hay
    // que rearmarlo con el nuevo tamaño (si no, se vería borroso o sobra GPU)
    if (a4kActive.value) applyA4k()
  })
}

function openInMpv() {
  const { anime, ep } = p.value
  const pos = videoEl.value?.currentTime || 0
  close()
  store.setPlayerMode('mpv')
  store.play(anime, ep, '', pos)
  store.setPlayerMode('web')
}

/* auto-hide de controles */
function poke() {
  uiVisible.value = true
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => {
    if (playing.value && !menuOpen.value && !epPanel.value) uiVisible.value = false
  }, 3000)
}
function onWheel(ev) {
  const delta = ev.deltaY < 0 ? 0.05 : -0.05
  const val = Math.min(1, Math.max(0, volume.value + delta))
  volume.value = val
  if (videoEl.value) videoEl.value.volume = val
  localStorage.setItem('anime-player-vol', String(val))
  poke()
}
function onKey(ev) {
  if (!p.value) return
  const v = videoEl.value
  switch (ev.key) {
    case ' ': ev.preventDefault(); togglePlay(); break
    case 'ArrowLeft': skip(-10); break
    case 'ArrowRight': skip(10); break
    case 'ArrowUp': ev.preventDefault(); onWheel({ deltaY: -1 }); break
    case 'ArrowDown': ev.preventDefault(); onWheel({ deltaY: 1 }); break
    case 'f': toggleFs(); break
    case 'm': toggleMute(); break
    case 'Escape':
      if (epPanel.value) epPanel.value = false
      else if (!document.fullscreenElement) close()
      break
  }
  poke()
}

watch(p, (val) => {
  if (val) {
    document.addEventListener('keydown', onKey)
    document.addEventListener('fullscreenchange', onFsChange)
  } else {
    document.removeEventListener('keydown', onKey)
    document.removeEventListener('fullscreenchange', onFsChange)
    teardownMedia()
  }
})
onBeforeUnmount(() => { teardownMedia() })

const fmt = (s) => {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + ':' + String(ss).padStart(2, '0')
}
const pct = computed(() => duration.value ? (time.value / duration.value) * 100 : 0)
const bufPct = computed(() => duration.value ? (buffered.value / duration.value) * 100 : 0)
const trackLabel = (t, i) => t.title || t.lang || `Pista ${i + 1}`
</script>

<template>
  <Teleport to="body">
    <div v-if="p" ref="wrap" class="wp" :class="{ 'is-idle': !uiVisible }"
         @mousemove="poke" @wheel.prevent="onWheel">

      <!-- vídeo -->
      <video ref="videoEl" class="wp__video" :class="{ 'is-shaded': a4kActive }" crossorigin="anonymous"
             @click="togglePlay" @dblclick="toggleFs"
             @play="playing = true; poke()" @pause="playing = false; poke()"
             @timeupdate="onTime" @ended="onEnded"
             @waiting="waiting = true" @playing="waiting = false" />

      <!-- salida Anime4K (WebGPU) calcada al rectángulo real del vídeo -->
      <canvas ref="a4kCanvas" v-show="a4kActive" class="wp__a4k"
              :style="{ left: canvasRect.left + 'px', top: canvasRect.top + 'px',
                        width: canvasRect.width + 'px', height: canvasRect.height + 'px' }"
              @click="togglePlay" @dblclick="toggleFs" />

      <!-- estados -->
      <div v-if="p.loading" class="wp__center">
        <Spinner :size="42" /><p>Preparando el stream…</p>
      </div>
      <div v-else-if="p.error" class="wp__center">
        <Icon name="close" :size="36" /><p>{{ p.error }}</p>
        <button class="wp__btnalt" @click="openInMpv">Abrir en MPV</button>
      </div>
      <div v-else-if="waiting" class="wp__center wp__center--soft"><Spinner :size="42" /></div>

      <!-- pausa (indicador central) -->
      <button v-if="!playing && !p.loading && !p.error && !nextCd" class="wp__bigplay" @click="togglePlay">
        <Icon name="play" :size="34" />
      </button>

      <!-- feedback central de play/pausa (se expande y desvanece) -->
      <div v-if="ripple" :key="rippleKey" class="wp__ripple">
        <Icon :name="ripple" :size="30" />
      </div>

      <!-- burbuja lateral de seek (±10 s / Saltar OP) -->
      <div v-if="seekBubble" :key="seekBubble.key" class="wp__seekbub" :class="`is-${seekBubble.side}`">
        <span>{{ seekBubble.secs > 0 ? '+' : '−' }}{{ Math.abs(seekBubble.secs) }} s</span>
      </div>

      <!-- el navegador no pudo decodificar el códec (HEVC sin hardware) -->
      <div v-if="decodeFailed" class="wp__tc">
        <p><strong>Tu navegador no pudo decodificar este vídeo ({{ p.sess?.video_codec?.toUpperCase() }})</strong>
          — elige cómo verlo:</p>
        <div class="wp__nextacts">
          <button class="wp__btnalt" @click="retryTranscode">Convertir aquí (pierde algo de calidad)</button>
          <button class="wp__btnmain" @click="openInMpv"><Icon name="play" :size="14" /> Ver original en MPV</button>
        </div>
      </div>

      <!-- aviso de transcode: la calidad NO es la original — ofrecer MPV -->
      <div v-if="p.sess?.transcode && !tcDismissed" class="wp__tc">
        <p><strong>Códec no soportado por el navegador</strong> — reproduciendo
          una conversión (no es la calidad original del archivo).</p>
        <div class="wp__nextacts">
          <button class="wp__btnalt" @click="tcDismissed = true">Continuar así</button>
          <button class="wp__btnmain" @click="openInMpv"><Icon name="play" :size="14" /> Ver original en MPV</button>
        </div>
      </div>

      <!-- siguiente episodio: tarjeta con miniatura + anillo de cuenta atrás -->
      <div v-if="nextCd > 0 && nextEp" class="wp__next">
        <span class="wp__next-eyebrow">Siguiente episodio</span>
        <div class="wp__next-thumb" @click="goNext">
          <img v-if="!nextThumbFailed" :src="`/api/anime/thumb/${nextEp.anime.id}/${nextEp.ep.num}`"
               alt="" @error="nextThumbFailed = true" />
          <img v-else-if="nextEp.anime.cover" :src="imgProxy(nextEp.anime.cover)" alt="" />
          <div class="wp__next-scrim" />
          <span class="wp__next-play">
            <svg class="wp__ring" viewBox="0 0 40 40">
              <circle class="wp__ring-bg" cx="20" cy="20" r="17" />
              <circle class="wp__ring-fg" cx="20" cy="20" r="17" :style="{ strokeDashoffset: ringOff }" />
            </svg>
            <Icon name="play" :size="16" />
          </span>
        </div>
        <p class="wp__next-title">{{ animeEpLabel(nextEp.anime, nextEp.ep) }}</p>
        <div class="wp__nextacts">
          <button class="wp__btnalt" @click="cancelNext">Cancelar</button>
          <button class="wp__btnmain" @click="goNext"><Icon name="play" :size="14" /> Reproducir ({{ Math.ceil(nextCd) }})</button>
        </div>
      </div>

      <!-- cabecera -->
      <header class="wp__head">
        <button class="wp__ic" title="Volver" @click="close"><Icon name="chevron" :size="20" style="transform: rotate(90deg)" /></button>
        <div class="wp__titles">
          <h2>{{ title }}</h2>
          <p>{{ p.anime.title }}</p>
        </div>
        <span v-if="p.sess?.transcode" class="wp__badge" title="El códec no es compatible con el navegador: transcodificando">TRANSCODE</span>
      </header>

      <!-- barra de controles -->
      <footer class="wp__bar">
        <!-- timeline -->
        <div class="wp__timeline" @click="seekTo" @mousemove="onTlHover" @mouseleave="tlHover = -1">
          <div class="wp__tl-buf" :style="{ width: bufPct + '%' }" />
          <div class="wp__tl-cur" :style="{ width: pct + '%' }" />
          <div class="wp__tl-knob" :style="{ left: pct + '%' }" />

          <!-- preview flotante: miniatura del vídeo + tiempo (estilo Crunchyroll) -->
          <div v-if="tlHover >= 0" class="wp__preview" :style="{ left: tlLeft + 'px' }">
            <img v-if="thumbUrl" v-show="thumbOk" :src="thumbUrl" alt=""
                 @load="thumbOk = true" @error="thumbOk = false" />
            <span class="wp__preview-t">{{ fmt(tlHover) }}</span>
          </div>
        </div>

        <div class="wp__row">
          <button class="wp__ic" @click="togglePlay" :title="playing ? 'Pausa' : 'Reproducir'">
            <Icon :name="playing ? 'pause' : 'play'" :size="20" />
          </button>
          <button class="wp__ic" title="-10 s" @click="skip(-10)"><span class="wp__sk">-10</span></button>
          <button class="wp__ic" title="+10 s" @click="skip(10)"><span class="wp__sk">+10</span></button>
          <button class="wp__skipop" title="Saltar opening" @click="skip(SKIP_SECS)">
            <Icon name="spark" :size="13" /> Saltar OP
          </button>

          <div class="wp__vol">
            <button class="wp__ic" @click="toggleMute" :title="muted ? 'Quitar silencio' : 'Silenciar'"><span class="wp__sk">{{ muted || volume === 0 ? '🔇' : '🔊' }}</span></button>
            <input type="range" min="0" max="1" step="0.02" :value="muted ? 0 : volume" @input="setVolume" />
          </div>

          <span class="wp__time">{{ fmt(time) }} <em>/ {{ fmt(duration) }}</em></span>
          <span class="wp__gap" />

          <!-- menús -->
          <div class="wp__menuwrap" v-if="audioTracks.length > 1">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'audio' }" @click="menuOpen = menuOpen === 'audio' ? '' : 'audio'">Audio</button>
            <div v-if="menuOpen === 'audio'" class="wp__menu">
              <button v-for="(t, i) in audioTracks" :key="'a' + i" :class="{ 'is-sel': i === audioIndex }" @click="setAudioTrack(i)">{{ trackLabel(t, i) }}</button>
            </div>
          </div>
          <div class="wp__menuwrap" v-if="subTracks.length">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'subs' }" @click="menuOpen = menuOpen === 'subs' ? '' : 'subs'">Subtítulos</button>
            <div v-if="menuOpen === 'subs'" class="wp__menu">
              <button :class="{ 'is-sel': subIndex === -1 }" @click="setSubTrack(-1)">Sin subtítulos</button>
              <button v-for="(t, i) in subTracks" :key="'s' + i" :class="{ 'is-sel': i === subIndex }" @click="setSubTrack(i)">{{ trackLabel(t, i) }}</button>
            </div>
          </div>
          <div class="wp__menuwrap" v-if="a4kAvailable">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'a4k', 'is-glow': a4kActive }"
                    title="Anime4K (mejora de imagen por GPU)"
                    @click="menuOpen = menuOpen === 'a4k' ? '' : 'a4k'">
              <Icon name="spark" :size="13" /> {{ a4kMode === 'off' ? 'Anime4K' : 'A4K·' + a4kMode }}
            </button>
            <div v-if="menuOpen === 'a4k'" class="wp__menu">
              <button v-for="m in A4K_MODES" :key="m.id" :class="{ 'is-sel': m.id === a4kMode }"
                      @click="setA4kMode(m.id)">{{ m.label }}</button>
            </div>
          </div>
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'speed' }" @click="menuOpen = menuOpen === 'speed' ? '' : 'speed'">{{ speed }}×</button>
            <div v-if="menuOpen === 'speed'" class="wp__menu">
              <button v-for="s in [0.5, 0.75, 1, 1.25, 1.5, 2]" :key="s" :class="{ 'is-sel': s === speed }" @click="setSpeed(s)">{{ s }}×</button>
            </div>
          </div>

          <button v-if="store.playerNext()" class="wp__ctl" title="Siguiente episodio" @click="goNext">
            Siguiente <Icon name="chevron" :size="14" style="transform: rotate(-90deg)" />
          </button>
          <button v-if="panelEps.length > 1" class="wp__ctl" :class="{ 'is-on': epPanel }"
                  title="Lista de episodios" @click="epPanel = !epPanel">
            <Icon name="menu" :size="14" /> Episodios
          </button>
          <button class="wp__ic" :title="isFs ? 'Salir de pantalla completa' : 'Pantalla completa'" @click="toggleFs">
            <Icon name="external" :size="18" />
          </button>
        </div>
      </footer>

      <!-- panel de episodios: cambiar de episodio sin salir del player -->
      <Transition name="wp-eps">
        <aside v-if="epPanel" class="wp__eps" @mousemove.stop="poke">
          <header class="wp__eps-head">
            <h3>{{ p.anime.title }}</h3>
            <button class="wp__ic" title="Cerrar" @click="epPanel = false"><Icon name="close" :size="16" /></button>
          </header>
          <div class="wp__eps-list">
            <button v-for="e in panelEps" :key="e.num" class="wp__ep"
                    :class="{ 'is-cur': e.num === p.ep.num, 'is-off': !isPlayable(e) }"
                    @click="playFromPanel(e)">
              <div class="wp__ep-th">
                <img v-if="isPlayable(e)" :src="`/api/anime/thumb/${p.anime.id}/${e.num}`"
                     alt="" loading="lazy" @error="$event.target.style.display = 'none'" />
                <span v-if="e.num === p.ep.num" class="wp__ep-now"><Icon name="play" :size="12" /></span>
                <span v-if="e.resume_pos > 0 && e.duration" class="wp__ep-pr">
                  <i :style="{ width: Math.min(100, e.resume_pos / e.duration * 100) + '%' }" />
                </span>
              </div>
              <div class="wp__ep-info">
                <span class="wp__ep-num">Episodio {{ e.num }}</span>
                <span class="wp__ep-meta">{{ e.num === p.ep.num ? 'Reproduciendo' : (isPlayable(e) ? (e.watched ? 'Visto' : '') : 'No descargado') }}</span>
              </div>
              <Icon v-if="e.watched" name="check" :size="14" class="wp__ep-check" />
            </button>
          </div>
        </aside>
      </Transition>
    </div>
  </Teleport>
</template>

<style scoped>
.wp {
  position: fixed; inset: 0; z-index: 300; background: #000;
  display: flex; align-items: center; justify-content: center;
}
.wp.is-idle { cursor: none; }
.wp__video { width: 100%; height: 100%; object-fit: contain; background: #000; }
/* Con Anime4K activo el vídeo sigue reproduciendo debajo del canvas WebGPU
 * (que lo tapa por completo, alphaMode opaque). OJO: NO usar visibility:hidden
 * ni opacity:0 — la intervención de ahorro de Chromium PAUSA los vídeos
 * ocultos (el "se queda trabado"). Se deja visible y simplemente tapado. */
.wp__video.is-shaded { /* intencionadamente sin ocultar */ }
.wp__a4k { position: absolute; z-index: 1; }
/* subtítulos (JASSUB) siempre por encima del canvas Anime4K */
.wp :deep(canvas.JASSUB) { z-index: 2; }
.wp__ctl.is-glow { color: var(--cyan); text-shadow: 0 0 8px var(--cyan-glow); }

.wp__center {
  position: absolute; inset: 0; display: flex; flex-direction: column; gap: var(--s-3);
  align-items: center; justify-content: center; color: var(--ink); pointer-events: none;
}
.wp__center button, .wp__center p { pointer-events: auto; }
.wp__center--soft { background: rgba(0,0,0,.25); }

.wp__bigplay {
  position: absolute; width: 5rem; height: 5rem; border-radius: 50%;
  display: grid; place-items: center; color: #fff;
  background: color-mix(in srgb, var(--azure) 80%, transparent);
  border: none; cursor: pointer; transition: transform var(--t-fast), background var(--t-fast);
}
.wp__bigplay:hover { transform: scale(1.08); background: var(--azure-bright); }

/* icono central que se expande y desvanece al pausar/reproducir */
.wp__ripple {
  position: absolute; inset: 0; margin: auto; width: 5rem; height: 5rem;
  display: grid; place-items: center; color: #fff; border-radius: 50%;
  background: rgba(7,10,18,.55); pointer-events: none;
  animation: wp-ripple .55s var(--ease-silk) forwards;
}
@keyframes wp-ripple {
  from { opacity: .95; transform: scale(.7); }
  to   { opacity: 0;   transform: scale(1.45); }
}
/* burbuja de seek: aparece en el lado hacia el que se salta */
.wp__seekbub {
  position: absolute; top: 50%; transform: translateY(-50%);
  padding: var(--s-2) var(--s-4); border-radius: var(--r-pill);
  background: rgba(7,10,18,.65); color: #fff; pointer-events: none;
  font-family: var(--font-mono); font-size: var(--fs-md); font-weight: 700;
  animation: wp-bubble .65s var(--ease-silk) forwards;
}
.wp__seekbub.is-left { left: 12%; }
.wp__seekbub.is-right { right: 12%; }
@keyframes wp-bubble {
  from { opacity: .95; transform: translateY(-50%) scale(.85); }
  60%  { opacity: .95; }
  to   { opacity: 0;   transform: translateY(-50%) scale(1.1); }
}

.wp__next {
  position: absolute; right: var(--s-6); bottom: 7rem; width: 19rem; padding: var(--s-3);
  background: rgba(10,14,24,.92); border: 1px solid var(--line); border-radius: var(--r-md);
  color: var(--ink); backdrop-filter: blur(8px); box-shadow: var(--shadow-lg);
}
.wp__next-eyebrow { display: block; margin-bottom: var(--s-2); font-family: var(--font-mono);
  font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--cyan); }
.wp__next-thumb { position: relative; aspect-ratio: 16 / 9; border-radius: var(--r-sm);
  overflow: hidden; cursor: pointer; background: #000; }
.wp__next-thumb img { width: 100%; height: 100%; object-fit: cover; }
.wp__next-scrim { position: absolute; inset: 0; background: rgba(0,0,0,.35); transition: background var(--t-fast); }
.wp__next-thumb:hover .wp__next-scrim { background: rgba(0,0,0,.15); }
.wp__next-play {
  position: absolute; inset: 0; margin: auto; width: 3.5rem; height: 3.5rem;
  display: grid; place-items: center; color: #fff;
}
.wp__ring { position: absolute; inset: 0; width: 100%; height: 100%; transform: rotate(-90deg); }
.wp__ring-bg, .wp__ring-fg { fill: rgba(7,10,18,.55); stroke-width: 3; }
.wp__ring-bg { stroke: rgba(255,255,255,.25); }
.wp__ring-fg { fill: none; stroke: var(--azure-bright); stroke-linecap: round;
  stroke-dasharray: 106.8; transition: stroke-dashoffset .1s linear; }
.wp__next-play :deep(svg:not(.wp__ring)) { position: relative; filter: drop-shadow(0 1px 4px rgba(0,0,0,.7)); }
.wp__next-title { margin-top: var(--s-2); font-size: var(--fs-sm); font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wp__tc {
  position: absolute; top: 5.5rem; left: 50%; transform: translateX(-50%);
  max-width: 34rem; padding: var(--s-4); text-align: center;
  background: rgba(10,14,24,.94); border: 1px solid color-mix(in srgb, var(--amber) 45%, transparent);
  border-radius: var(--r-md); color: var(--ink); backdrop-filter: blur(8px);
}
.wp__tc strong { color: var(--amber); }
.wp__tc .wp__nextacts { justify-content: center; }
.wp__nextacts { display: flex; gap: var(--s-2); margin-top: var(--s-3); }
.wp__btnmain {
  display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-4);
  border-radius: var(--r-sm); border: none; background: var(--azure); color: #fff;
  font-weight: 600; cursor: pointer;
}
.wp__btnmain:hover { background: var(--azure-bright); }
.wp__btnalt {
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); cursor: pointer;
  border: 1px solid var(--line-bright); background: transparent; color: var(--ink);
}

.wp__head {
  position: absolute; top: 0; left: 0; right: 0; display: flex; align-items: center;
  gap: var(--s-3); padding: var(--s-4) var(--s-5);
  background: linear-gradient(180deg, rgba(0,0,0,.75), transparent);
  transition: opacity var(--t-base), transform var(--t-base) var(--ease-silk);
}
.wp.is-idle .wp__head { opacity: 0; transform: translateY(-8px); pointer-events: none; }
.wp__titles h2 { font-size: var(--fs-md); color: #fff; font-weight: 600; }
.wp__titles p { font-size: var(--fs-xs); color: var(--ink-soft); }
.wp__badge {
  margin-left: auto; font-family: var(--font-mono); font-size: var(--fs-2xs);
  padding: 2px 8px; border-radius: var(--r-pill); color: var(--amber);
  border: 1px solid color-mix(in srgb, var(--amber) 40%, transparent);
  background: color-mix(in srgb, var(--amber) 12%, transparent);
}

.wp__bar {
  position: absolute; left: 0; right: 0; bottom: 0; padding: 0 var(--s-5) var(--s-3);
  background: linear-gradient(0deg, rgba(0,0,0,.85), transparent);
  transition: opacity var(--t-base), transform var(--t-base) var(--ease-silk);
}
.wp.is-idle .wp__bar { opacity: 0; transform: translateY(8px); pointer-events: none; }

.wp__timeline {
  position: relative; height: 5px; border-radius: 3px; background: rgba(255,255,255,.18);
  cursor: pointer; margin-bottom: var(--s-3);
}
.wp__timeline:hover { height: 8px; }
.wp__tl-buf { position: absolute; inset: 0 auto 0 0; border-radius: 3px; background: rgba(255,255,255,.28); }
.wp__tl-cur { position: absolute; inset: 0 auto 0 0; border-radius: 3px; background: var(--azure-bright); }
.wp__tl-knob {
  position: absolute; top: 50%; width: 14px; height: 14px; border-radius: 50%;
  background: var(--azure-bright); transform: translate(-50%, -50%);
  opacity: 0; transition: opacity var(--t-fast); box-shadow: 0 0 8px var(--azure-glow);
}
.wp__timeline:hover .wp__tl-knob { opacity: 1; }

.wp__preview {
  position: absolute; bottom: calc(100% + var(--s-3)); transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: var(--s-1);
  pointer-events: none; z-index: 3;
}
.wp__preview img {
  width: 10rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--r-sm);
  border: 1px solid rgba(255,255,255,.35); box-shadow: 0 6px 24px rgba(0,0,0,.6);
  background: #000;
}
.wp__preview-t {
  font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; color: #fff;
  padding: 2px 8px; border-radius: var(--r-pill); background: rgba(7,10,18,.85);
  border: 1px solid rgba(255,255,255,.2);
}

.wp__row { display: flex; align-items: center; gap: var(--s-2); }
.wp__ic {
  display: grid; place-items: center; width: 2.75rem; height: 2.75rem;
  border: none; background: transparent; color: #fff; cursor: pointer;
  border-radius: var(--r-sm); transition: background var(--t-fast);
}
.wp__ic:hover, .wp__ic:focus-visible { background: rgba(255,255,255,.14); }
.wp__sk { font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; }
.wp__skipop {
  display: inline-flex; align-items: center; gap: 5px; padding: var(--s-2) var(--s-3);
  border-radius: var(--r-pill); border: 1px solid rgba(255,255,255,.25);
  background: rgba(255,255,255,.08); color: #fff; font-size: var(--fs-xs); font-weight: 600;
  cursor: pointer; transition: all var(--t-fast);
}
.wp__skipop:hover { background: color-mix(in srgb, var(--azure) 40%, transparent); border-color: var(--azure-glow); }

.wp__vol { display: flex; align-items: center; gap: 4px; }
.wp__vol input[type='range'] {
  width: 6rem; accent-color: var(--azure-bright); cursor: pointer;
}
.wp__time { font-family: var(--font-mono); font-size: var(--fs-xs); color: #fff; white-space: nowrap; }
.wp__time em { font-style: normal; color: var(--ink-soft); }
.wp__gap { flex: 1; }

.wp__ctl {
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); cursor: pointer;
  border: none; background: transparent; color: #fff; font-size: var(--fs-xs); font-weight: 600;
  min-height: 2.75rem;   /* target 10-foot: mando/teclado a distancia */
  display: inline-flex; align-items: center; gap: 4px; transition: background var(--t-fast);
}
.wp__ctl:hover, .wp__ctl.is-on, .wp__ctl:focus-visible { background: rgba(255,255,255,.14); }

.wp__menuwrap { position: relative; }
.wp__menu {
  position: absolute; bottom: calc(100% + var(--s-2)); right: 0; min-width: 11rem;
  max-height: 18rem; overflow-y: auto; padding: var(--s-1);
  background: rgba(10,14,24,.96); border: 1px solid var(--line); border-radius: var(--r-md);
  backdrop-filter: blur(10px); box-shadow: var(--shadow-lg);
}
.wp__menu button {
  display: block; width: 100%; text-align: left; padding: var(--s-2) var(--s-3);
  border: none; background: transparent; color: var(--ink); font-size: var(--fs-xs);
  border-radius: var(--r-xs); cursor: pointer; min-height: 2.5rem;
}
.wp__menu button:hover { background: var(--azure-haze); color: #fff; }
.wp__menu button.is-sel { color: var(--azure-bright); font-weight: 700; }

/* panel de episodios */
.wp__eps {
  position: absolute; top: 0; right: 0; bottom: 0; width: min(21rem, 86vw);
  display: flex; flex-direction: column; z-index: 4;
  background: rgba(10,14,24,.94); border-left: 1px solid var(--line);
  backdrop-filter: blur(12px); box-shadow: var(--shadow-xl);
}
.wp-eps-enter-active, .wp-eps-leave-active { transition: transform var(--t-base) var(--ease-silk), opacity var(--t-base); }
.wp-eps-enter-from, .wp-eps-leave-to { transform: translateX(2rem); opacity: 0; }
.wp__eps-head {
  display: flex; align-items: center; justify-content: space-between; gap: var(--s-2);
  padding: var(--s-3) var(--s-3) var(--s-3) var(--s-4); border-bottom: 1px solid var(--line);
}
.wp__eps-head h3 { font-size: var(--fs-sm); font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wp__eps-list { flex: 1; overflow-y: auto; padding: var(--s-2); display: flex; flex-direction: column; gap: var(--s-1); }
.wp__ep {
  display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2);
  border: none; background: transparent; border-radius: var(--r-sm); cursor: pointer;
  text-align: left; transition: background var(--t-fast);
}
.wp__ep:hover, .wp__ep:focus-visible { background: rgba(255,255,255,.08); }
.wp__ep.is-cur { background: color-mix(in srgb, var(--azure) 22%, transparent); }
.wp__ep.is-off { opacity: .45; cursor: default; }
.wp__ep.is-off:hover { background: transparent; }
.wp__ep-th {
  position: relative; flex-shrink: 0; width: 6.5rem; aspect-ratio: 16 / 9;
  border-radius: var(--r-xs); overflow: hidden; background: rgba(255,255,255,.06);
}
.wp__ep-th img { width: 100%; height: 100%; object-fit: cover; }
.wp__ep-now { position: absolute; inset: 0; display: grid; place-items: center;
  color: #fff; background: rgba(7,10,18,.45); }
.wp__ep-pr { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(0,0,0,.5); }
.wp__ep-pr i { display: block; height: 100%; background: var(--azure-bright); }
.wp__ep-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.wp__ep-num { font-size: var(--fs-sm); font-weight: 600; color: #fff; }
.wp__ep-meta { font-size: var(--fs-2xs); color: var(--ink-soft); }
.wp__ep-check { flex-shrink: 0; color: var(--jade); }
</style>
