<script setup>
// Overlay del reproductor NATIVO embebido (libmpv en la shell). El vídeo lo pinta
// mpv por DEBAJO del WebView2 transparente (ver desktop/native): aquí va SOLO la UI,
// con la MISMA estética/funcionalidad que PlayerOverlay.vue (clases wp__*). "Airspace":
// mientras hay vídeo transparentamos la página y ocultamos el shell para ver el vídeo.
// Los controles hablan con el motor por el store (nativeBridge → Rust → mpv).
import { computed, nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { send as nativeSend } from '@/lib/nativeBridge'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const np = computed(() => store.nativePlayer)

const uiVisible = ref(true)
const menuOpen = ref('')
const epPanel = ref(false)
const tlHover = ref(-1)
const tlLeft = ref(0)
const lastVol = ref(store.nativeVol || 100)
let hideTimer = null

const A4K_MODES = [
  { id: 'off', label: 'Desactivado' },
  { id: 'high', label: 'Calidad · A HQ' },
  { id: 'ultra', label: 'Muy alta · Doble CNN HQ' },
  { id: 'maximo', label: 'Máxima calidad · UL + Soft M + Thin' },
]
const A4K_TIER_LABEL = { high: 'A4K·HQ', ultra: 'A4K·Doble', maximo: 'A4K·Máximo' }
// Imagen real (series/películas): Anime4K no aplica —está entrenado en line art—, así que el
// menú ofrece los shaders genéricos. Los tiers los resuelve `tier_shaders()` en player.rs.
const LIVE_MODES = [
  { id: 'off', label: 'Desactivado' },
  { id: 'live', label: 'FSRCNNX + SSimSuperRes (máx. detalle)' },
  { id: 'live_lite', label: 'SSimSuperRes (ligero)' },
]
const scaleModes = computed(() => (np.value?.isLive ? LIVE_MODES : A4K_MODES))
// El botón se llama por lo que hay detrás: en anime es Anime4K; en imagen real no lo es, así que
// decir "Anime4K" ahí era mentira. Nombre genérico: "Shaders".
const scaleName = computed(() => (np.value?.isLive ? 'Shaders' : 'Anime4K'))
const scaleLabel = computed(() => {
  // Ya horneado: decirlo en el botón, o «Desactivado» parece que has perdido la calidad.
  if (np.value?.yaHorneado && np.value?.tier === 'off') return 'A4K·Horneado'
  if (np.value?.tier === 'off') return scaleName.value
  return np.value?.isLive ? 'Shaders·On' : (A4K_TIER_LABEL[np.value?.tier] || 'A4K·On')
})
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]

const playing = computed(() => np.value && !np.value.paused)
const pos = computed(() => np.value?.pos || 0)
const duration = computed(() => np.value?.duration || 0)
const tlEl = ref(null)          // la barra: hace falta para medirla y capturar el puntero
const arrastrando = ref(false)
const posArrastre = ref(0)
let ultimoEnvio = 0

// Durante el arrastre la barra sigue al PUNTERO, no a la posición del vídeo: si esperase a que
// mpv confirme cada salto, el pulgar se quedaría atrás y el gesto se sentiría pegajoso.
const pct = computed(() => {
  const d = duration.value
  if (d <= 0) return 0
  return Math.min(100, ((arrastrando.value ? posArrastre.value : pos.value) / d) * 100)
})
const muted = computed(() => store.nativeVol === 0)

const audioIndex = computed(() => (np.value ? np.value.aid - 1 : 0))
const subIndex = computed(() => (np.value ? np.value.sid - 1 : -1))

// Episodios para el panel lateral (cambiar sin salir), igual que el original.
// `playlist` la pasa quien abre el reproductor desde otro dominio (series/películas); el anime
// la deriva de su biblioteca. Misma forma en ambos casos → el panel no sabe de dónde viene.
const panelEps = computed(() => {
  const eps = (np.value?.playlist || np.value?.anime?.episodes || [])
    .filter((e) => e.num > 0 && e.ep_type !== 'special')
  return [...eps].sort((a, b) => a.num - b.num)
})
function isPlayable(e) {
  return !!(e.in_local || e.info_hash)
}
// Miniatura: la del episodio si la trae (series, vía Sonarr), si no la ruta de anime.
function epThumb(e) {
  return e.thumb || (np.value?.anime?.id ? `/api/anime/thumb/${np.value.anime.id}/${e.num}` : '')
}

function fmt(s) {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`
}
function trackLabel(t, i) {
  const base = t.lang || t.codec || `pista ${i + 1}`
  return t.title ? `${i + 1}. ${base} · ${t.title}` : `${i + 1}. ${base}`
}

// Airspace: transparenta la página y oculta el shell mientras hay vídeo nativo.
watch(
  () => !!np.value,
  (open) => {
    document.documentElement.classList.toggle('native-video', open)
    if (open) {
      poke(); menuOpen.value = ''; epPanel.value = false
      // El motor se REUTILIZA entre episodios: `sub-pos` sobrevive al cambio de archivo, así que
      // hay que reponerlo al abrir o el subtítulo arranca donde lo dejó el episodio anterior.
      nextTick(() => liftSubs(uiVisible.value))
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => document.documentElement.classList.remove('native-video'))

function poke() {
  uiVisible.value = true
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => {
    if (np.value && playing.value && !menuOpen.value && !epPanel.value) uiVisible.value = false
  }, 3000)
}

// El cursor Win32 lo gestiona el host (en composición el WebView no controla el cursor
// de la ventana). Avisar a Rust para ocultarlo cuando la UI se oculta y viceversa.
watch(uiVisible, (v) => { nativeSend('cursor', { hide: !v }); liftSubs(v) })

/* Al sacar los controles, la barra tapaba justo la línea de subtítulo: mover el ratón para
 * buscar un momento te dejaba sin leer. Ahora el subtítulo SUBE lo que ocupa la barra y baja al
 * ocultarse.
 *
 * Se hace con `sub-pos` de mpv (100 = abajo del todo), que mueve el diálogo pero NO los carteles
 * con `\pos` — el mismo criterio que el override por estilo: no tocar lo que el grupo casó con el
 * arte. El desplazamiento se MIDE de la barra real, no es una constante: si cambia el diseño o el
 * tamaño de la ventana, sigue cuadrando.
 */
const barEl = ref(null)
function liftSubs(up) {
  const h = up ? (barEl.value?.offsetHeight || 0) : 0
  const pct = h ? Math.min(30, Math.round((h / (window.innerHeight || 1080)) * 100)) : 0
  nativeSend('setprop', { name: 'sub-pos', value: String(100 - pct) })
}

function togglePlay() {
  if (!np.value || np.value.loading) return
  store.nativePause(!np.value.paused)
  poke()
}
function skip(d) {
  if (!np.value) return
  store.nativeSeek(Math.max(0, (np.value.pos || 0) + d))
  poke()
}
function skipOp() { store.nativeSkipOp(); poke() }
function toggleFs() { store.toggleNativeFullscreen(); poke() }
function close() { store.closeNative() }

// Arrastrar la barra para buscar, como en YouTube: se mantiene pulsado y el vídeo sigue al
// puntero. `setPointerCapture` hace que el gesto continúe aunque el cursor se salga de la barra
// (o de la ventana), que es justo lo que uno hace al arrastrar deprisa.
function posDeEvento(e) {
  const r = tlEl.value.getBoundingClientRect()
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))   // acotado: ver nativeSeek
  return f * duration.value
}

function tlDown(e) {
  if (!duration.value) return
  arrastrando.value = true
  posArrastre.value = posDeEvento(e)
  tlEl.value.setPointerCapture?.(e.pointerId)
  store.nativeSeek(posArrastre.value)
  poke()
}

function tlMove(e) {
  onTlHover(e)                       // miniatura de previsualización, se arrastre o no
  if (!arrastrando.value) return
  posArrastre.value = posDeEvento(e)
  // Un salto por cada píxel de movimiento ahogaría a mpv sin que se note mejor: se limita a
  // ~8/s mientras se arrastra y el definitivo se manda al soltar.
  const ahora = Date.now()
  if (ahora - ultimoEnvio > 120) { ultimoEnvio = ahora; store.nativeSeek(posArrastre.value) }
  poke()
}

function tlUp(e) {
  if (!arrastrando.value) return
  arrastrando.value = false
  store.nativeSeek(posDeEvento(e))   // el definitivo, sin limitar
  poke()
}
function onTlHover(e) {
  const r = e.currentTarget.getBoundingClientRect()
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
  tlHover.value = f * duration.value
  tlLeft.value = e.clientX - r.left
}

// Miniatura de la barra de progreso bajo el cursor: índice = floor(t/intervalo)+1.
const scrubUrl = computed(() => {
  const t = np.value?.thumbs
  if (!t?.url || tlHover.value < 0) return ''
  const idx = Math.max(1, Math.floor(tlHover.value / (t.interval || 10)) + 1)
  return `${t.url}/${idx}`
})

// Volumen hasta 150% (mpv con volume-max=150, fijado al cargar el episodio).
const VOL_MAX = 150
function onVol(e) { setVol(Number(e.target.value)) }
function setVol(v) { v = Math.min(VOL_MAX, Math.max(0, v)); store.setNativeVol(v); if (v > 0) lastVol.value = v; poke() }
function toggleMute() { setVol(muted.value ? lastVol.value || 100 : 0) }
function onWheel(e) {
  setVol(store.nativeVol + (e.deltaY < 0 ? 5 : -5))
}

function setAudio(i) { store.setNativeAudio(i); menuOpen.value = ''; poke() }
function setSub(i) { store.setNativeSub(i); menuOpen.value = ''; poke() }
function setA4k(id) { store.setNative4kTier(id); menuOpen.value = ''; poke() }
function setSpeed(s) { store.setNativeSpeed(s); menuOpen.value = ''; poke() }
function bumpSubScale(d) { store.setNativeSubScale((np.value.subScale || 1) + d); poke() }
function bumpSubSync(d) { store.setNativeSubSync((np.value.subSync || 0) + d); poke() }
function bumpBright(d) { store.setNativeBright((np.value.bright || 1) + d); poke() }
function setBright(v) { store.setNativeBright(v); poke() }
function bumpSat(d) { store.setNativeSat((np.value.sat || 1) + d); poke() }
function setSat(v) { store.setNativeSat(v); poke() }

const nextEp = computed(() => (np.value ? require_next() : null))
function require_next() {
  // nextUnwatchedEp vive en el store; exponemos "siguiente" simple por número.
  const eps = panelEps.value
  const cur = np.value.ep.num
  return eps.find((e) => e.num > cur && isPlayable(e)) || null
}
// Cambiar de episodio SIN salir tiene que conservar el dominio: si esto es una serie occidental,
// el progreso del episodio nuevo debe seguir yendo a su almacén (cada item de `playlist` trae su
// propio progressKey), no a la biblioteca de anime.
function playEp(e) {
  const cur = np.value
  store.playNative(cur.anime, e, e.pos || 0, {
    progressKey: e.progressKey || null,
    playlist: cur.playlist,
    onProgress: cur.onProgress,
  })
}
function goNext() {
  const n = nextEp.value
  if (n) playEp(n)
}
function playFromPanel(e) {
  if (!isPlayable(e) || e.num === np.value.ep.num) return
  epPanel.value = false
  playEp(e)
}

// ── Avisos del reproductor (cues) — sistema extensible estilo Netflix ──────────
// Cada cue declara cuándo aparece (segundos RESTANTES para el final), cuánto dura y si
// aplica. Reutilizable para futuros avisos (créditos, fin de temporada, «te puede
// gustar», etc.): basta añadir una entrada a PLAYER_CUES y un bloque de render en la
// plantilla con v-if="activeCue === '<id>'". Se muestra como máximo uno a la vez, una
// sola vez por episodio, y se auto-oculta (5 s) salvo que el ratón esté encima.
const PLAYER_CUES = [
  { id: 'next-ep', remaining: 88, hold: 5000, applies: () => !!nextEp.value },
]
const activeCue = ref(null)          // id del aviso visible, o null
const shownCues = ref(new Set())     // ids ya mostrados en este episodio (no repetir)
const cueHover = ref(false)          // ratón sobre la tarjeta → no auto-ocultar
let cueTimer = null

// Miniatura del siguiente episodio para la tarjeta (misma fuente que el panel).
const nextThumb = computed(() =>
  (np.value && nextEp.value) ? `/api/anime/thumb/${np.value.anime.id}/${nextEp.value.num}` : '')

function armCueTimer(ms) {
  clearTimeout(cueTimer)
  cueTimer = setTimeout(() => { if (!cueHover.value) activeCue.value = null }, ms)
}
function dismissCue() { clearTimeout(cueTimer); activeCue.value = null; cueHover.value = false }
function cueEnter() { cueHover.value = true; clearTimeout(cueTimer) }
function cueLeave(ms = 1500) { cueHover.value = false; if (activeCue.value) armCueTimer(ms) }
function cuePlayNext() { dismissCue(); goNext() }

function evalCues() {
  if (!np.value || activeCue.value || !duration.value) return
  const remaining = duration.value - pos.value
  for (const cue of PLAYER_CUES) {
    if (shownCues.value.has(cue.id)) continue
    // Ventana estrecha: dispara al CRUZAR el umbral viendo normal; si saltas muy por
    // debajo (p.ej. al final directo) no lo lanza tarde.
    if (remaining <= cue.remaining && remaining >= cue.remaining - 6 && duration.value > cue.remaining + 8) {
      if (cue.applies && !cue.applies()) continue
      activeCue.value = cue.id
      shownCues.value.add(cue.id)
      armCueTimer(cue.hold)
      break
    }
  }
}
// Reinicia el estado de cues al cambiar de episodio.
watch(() => (np.value ? `${np.value.anime?.id}:${np.value.ep?.num}` : ''), () => {
  shownCues.value = new Set(); dismissCue()
})
watch(pos, evalCues)

// Atajos de teclado (como YouTube/mpv): espacio/k = pausa, ←/→ = ±5 s, ↑/↓ = volumen,
// f = pantalla completa, m = silenciar, j/l = ±10 s. Solo actúa con vídeo cargado y
// fuera de campos de texto. preventDefault evita el scroll de la página con espacio/flechas.
function onKey(e) {
  if (!np.value) return
  const tag = (e.target?.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return
  switch (e.key) {
    case ' ': case 'k': e.preventDefault(); togglePlay(); break
    case 'ArrowLeft': e.preventDefault(); skip(-5); break
    case 'ArrowRight': e.preventDefault(); skip(5); break
    case 'j': e.preventDefault(); skip(-10); break
    case 'l': e.preventDefault(); skip(10); break
    case 'ArrowUp': e.preventDefault(); setVol(store.nativeVol + 5); break
    case 'ArrowDown': e.preventDefault(); setVol(store.nativeVol - 5); break
    case 'm': toggleMute(); break
    case 'f': toggleFs(); break
    default: return
  }
  poke()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div
      v-if="np"
      class="wp"
      :class="{ 'is-idle': !uiVisible }"
      @mousemove="poke"
      @click.self="togglePlay"
      @dblclick.self="toggleFs"
      @wheel.prevent="onWheel"
    >
      <!-- carga -->
      <div v-if="np.loading" class="wp__center wp__center--soft">
        <div class="wp__spinner" />
        <p>Cargando episodio…</p>
      </div>

      <!-- Sin botón permanente de play: clic sobre el vídeo pausa/reanuda (estilo YouTube/
           Netflix). La barra de controles ya se queda visible en pausa como referencia. -->

      <!-- cabecera -->
      <header class="wp__head">
        <button class="wp__ic" data-tip="Volver" @click="close">
          <Icon name="chevron" :size="20" style="transform: rotate(90deg)" />
        </button>
        <div class="wp__titles">
          <h2>{{ np.ep?.title || `Episodio ${np.ep?.num}` }}</h2>
          <p>{{ np.anime?.title }}</p>
        </div>
      </header>

      <!-- barra de controles -->
      <footer ref="barEl" class="wp__bar" @click.stop>
        <!-- timeline -->
        <div ref="tlEl" class="wp__timeline" :class="{ 'is-drag': arrastrando }"
             @pointerdown.prevent="tlDown" @pointermove="tlMove"
             @pointerup="tlUp" @pointercancel="tlUp"
             @mouseleave="arrastrando || (tlHover = -1)">
          <div class="wp__tl-cur" :style="{ width: pct + '%' }" />
          <div class="wp__tl-knob" :style="{ left: pct + '%' }" />
          <div v-if="tlHover >= 0" class="wp__preview" :style="{ left: tlLeft + 'px' }">
            <img v-if="scrubUrl" :key="scrubUrl" class="wp__preview-img" :src="scrubUrl" alt=""
                 @load="(e) => (e.target.style.opacity = 1)"
                 @error="(e) => (e.target.style.opacity = 0)" />
            <span class="wp__preview-t">{{ fmt(tlHover) }}</span>
          </div>
        </div>

        <div class="wp__row">
          <button class="wp__ic" @click="togglePlay" :data-tip="playing ? 'Pausa' : 'Reproducir'">
            <Icon :name="playing ? 'pause' : 'play'" :size="20" />
          </button>
          <button class="wp__ic" data-tip="-10 s" @click="skip(-10)"><span class="wp__sk">-10</span></button>
          <button class="wp__ic" data-tip="+10 s" @click="skip(10)"><span class="wp__sk">+10</span></button>
          <button class="wp__skipop" data-tip="Saltar opening" @click="skipOp">
            <Icon name="spark" :size="13" /> Saltar OP
          </button>

          <div class="wp__vol">
            <button class="wp__ic" @click="toggleMute" :data-tip="muted ? 'Quitar silencio' : 'Silenciar'">
              <span class="wp__sk">{{ muted ? '🔇' : '🔊' }}</span>
            </button>
            <!-- La barra llega a 100 (como mpv); la rueda del ratón la supera hasta 150,
                 que se refleja solo en el badge, no en el relleno de la barra. -->
            <input type="range" min="0" max="100" step="1" :value="Math.min(100, store.nativeVol)" @input="onVol"
                   :data-tip="`Volumen ${store.nativeVol}% — rueda del ratón para superar el 100% (hasta 150%)`" />
            <span v-if="store.nativeVol > 100" class="wp__boost" data-tip="Volumen aumentado por encima del 100% (rueda del ratón)">{{ store.nativeVol }}%</span>
          </div>

          <span class="wp__time">{{ fmt(pos) }} <em>/ {{ fmt(duration) }}</em></span>
          <span class="wp__gap" />

          <!-- audio -->
          <div class="wp__menuwrap" v-if="np.audioTracks.length > 1">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'audio' }"
                    @click="menuOpen = menuOpen === 'audio' ? '' : 'audio'">Audio</button>
            <div v-if="menuOpen === 'audio'" class="wp__menu" @wheel.stop>
              <button v-for="(t, i) in np.audioTracks" :key="'a' + i"
                      :class="{ 'is-sel': i === audioIndex }" @click="setAudio(i)">{{ trackLabel(t, i) }}</button>
            </div>
          </div>
          <!-- subtítulos: siempre disponible (delay/tamaño/config), incluso con 0-1 pistas -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'subs' }"
                    @click="menuOpen = menuOpen === 'subs' ? '' : 'subs'">Subtítulos</button>
            <div v-if="menuOpen === 'subs'" class="wp__menu" @wheel.stop>
              <div v-if="!np.subTracks.length" class="wp__subsize wp__subnote" @click.stop>
                <span>Este episodio no tiene pistas de subtítulos</span>
              </div>
              <div v-if="subIndex >= 0" class="wp__subsize" @click.stop>
                <span>Tamaño</span>
                <button :disabled="np.subScale <= 0.5" aria-label="Más pequeños" @click="bumpSubScale(-0.1)">−</button>
                <b>{{ Math.round(np.subScale * 100) }}%</b>
                <button :disabled="np.subScale >= 2" aria-label="Más grandes" @click="bumpSubScale(0.1)">+</button>
              </div>
              <div v-if="subIndex >= 0" class="wp__subsize" @click.stop>
                <span>Sincronía</span>
                <button aria-label="Adelantar" @click="bumpSubSync(-0.1)">−</button>
                <b :class="{ 'is-zero': np.subSync === 0 }" @dblclick="bumpSubSync(-np.subSync)">{{ np.subSync > 0 ? '+' : '' }}{{ np.subSync.toFixed(1) }}s</b>
                <button aria-label="Retrasar" @click="bumpSubSync(0.1)">+</button>
              </div>
              <button :class="{ 'is-sel': subIndex === -1 }" @click="setSub(-1)">Sin subtítulos</button>
              <button v-for="(t, i) in np.subTracks" :key="'s' + i"
                      :class="{ 'is-sel': i === subIndex }" @click="setSub(i)">{{ trackLabel(t, i) }}</button>
            </div>
          </div>
          <!-- Shaders (Anime4K en anime, genéricos en imagen real) -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'a4k', 'is-glow': np.tier !== 'off' }"
                    :data-tip="`${scaleName} (mejora de imagen por GPU)`"
                    @click="menuOpen = menuOpen === 'a4k' ? '' : 'a4k'">
              <Icon name="spark" :size="13" /> {{ scaleLabel }}
            </button>
            <div v-if="menuOpen === 'a4k'" class="wp__menu" @wheel.stop>
              <div class="wp__subsize" @click.stop data-tip="Ajusta los medios tonos (gamma) para igualar tu mpv. Doble clic = neutro.">
                <span>Gamma</span>
                <button :disabled="np.bright <= 0.5" aria-label="Menos gamma" @click="bumpBright(-0.05)">−</button>
                <b :class="{ 'is-zero': np.bright === 1 }" @dblclick="setBright(1)">{{ Math.round(np.bright * 100) }}%</b>
                <button :disabled="np.bright >= 3" aria-label="Más gamma" @click="bumpBright(0.05)">+</button>
              </div>
              <div class="wp__subsize" @click.stop data-tip="Saturación: devuelve el punch que el gamma quita. Doble clic = neutro.">
                <span>Saturación</span>
                <button :disabled="np.sat <= 0.5" aria-label="Menos saturación" @click="bumpSat(-0.05)">−</button>
                <b :class="{ 'is-zero': np.sat === 1 }" @dblclick="setSat(1)">{{ Math.round((np.sat ?? 1) * 100) }}%</b>
                <button :disabled="np.sat >= 3" aria-label="Más saturación" @click="bumpSat(0.05)">+</button>
              </div>
              <p v-if="np.yaHorneado" class="wp__nota">
                Este episodio ya está horneado con Anime4K: los shaders van dentro del vídeo.
                Volver a activarlos pasa la red dos veces.
              </p>
              <button v-for="m in scaleModes" :key="m.id" :class="{ 'is-sel': m.id === np.tier }"
                      @click="setA4k(m.id)">{{ m.label }}</button>
            </div>
          </div>

          <!-- velocidad -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'speed' }"
                    @click="menuOpen = menuOpen === 'speed' ? '' : 'speed'">{{ np.speed }}×</button>
            <div v-if="menuOpen === 'speed'" class="wp__menu" @wheel.stop>
              <button v-for="s in SPEEDS" :key="s" :class="{ 'is-sel': s === np.speed }"
                      @click="setSpeed(s)">{{ s }}×</button>
            </div>
          </div>

          <button v-if="nextEp" class="wp__ctl" data-tip="Siguiente episodio" @click="goNext">
            Siguiente <Icon name="skip-next" :size="15" />
          </button>
          <button v-if="panelEps.length > 1" class="wp__ctl" :class="{ 'is-on': epPanel }"
                  data-tip="Lista de episodios" @click="epPanel = !epPanel">
            <Icon name="menu" :size="14" /> Episodios
          </button>
          <button class="wp__ic" :class="{ 'is-on': np.fullscreen }"
                  :data-tip="np.fullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'" @click="toggleFs">
            <Icon :name="np.fullscreen ? 'collapse' : 'expand'" :size="18" />
          </button>
        </div>
      </footer>

      <!-- Avisos del reproductor (cues). Extensible: añade más bloques v-if="activeCue === '<id>'"
           para créditos, fin de temporada, etc. Por ahora: "Siguiente episodio" estilo Netflix. -->
      <Transition name="wp-cue">
        <div v-if="activeCue === 'next-ep' && nextEp" class="wp__cue"
             @mouseenter="cueEnter" @mouseleave="cueLeave()" @click.stop>
          <button class="wp__cue-main" @click="cuePlayNext">
            <span class="wp__cue-thumb">
              <img v-if="nextThumb" :src="nextThumb" alt="" loading="lazy" decoding="async"
                   @error="($event.target.style.visibility = 'hidden')" />
              <span class="wp__cue-play"><Icon name="play" :size="18" /></span>
            </span>
            <span class="wp__cue-info">
              <span class="wp__cue-eyebrow">A continuación</span>
              <span class="wp__cue-title">Episodio {{ nextEp.num }}<template v-if="nextEp.title"> · {{ nextEp.title }}</template></span>
              <span class="wp__cue-cta">Reproducir</span>
            </span>
          </button>
          <button class="wp__cue-close" data-tip="Descartar" @click.stop="dismissCue"><Icon name="close" :size="14" /></button>
        </div>
      </Transition>

      <!-- panel de episodios -->
      <Transition name="wp-eps">
        <aside v-if="epPanel" class="wp__eps" @click.stop @mousemove.stop="poke" @wheel.stop>
          <header class="wp__eps-head">
            <h3>{{ np.anime?.title }}</h3>
            <button class="wp__ic" data-tip="Cerrar" @click="epPanel = false"><Icon name="close" :size="16" /></button>
          </header>
          <div class="wp__eps-list">
            <button v-for="e in panelEps" :key="e.num" class="wp__ep"
                    :class="{ 'is-cur': e.num === np.ep.num, 'is-off': !isPlayable(e) }"
                    @click="playFromPanel(e)">
              <span class="wp__ep-th">
                <img v-if="epThumb(e)" :src="epThumb(e)" alt="" loading="lazy" decoding="async" @error="($event.target.style.visibility = 'hidden')" />
                <span v-if="e.num === np.ep.num" class="wp__ep-now"><Icon name="play" :size="16" /></span>
              </span>
              <span class="wp__ep-info">
                <span class="wp__ep-num">{{ e.label || `Episodio ${e.num}` }}</span>
                <span class="wp__ep-meta">{{ e.title || (e.in_local ? 'Local' : 'Torrent') }}</span>
              </span>
            </button>
          </div>
        </aside>
      </Transition>
    </div>
  </Teleport>
</template>

<style scoped>
.wp {
  position: fixed; inset: 0; z-index: 300; background: transparent; /* el vídeo va debajo */
  display: flex; align-items: center; justify-content: center;
}
.wp.is-idle { cursor: none; }

.wp__center {
  position: absolute; inset: 0; display: flex; flex-direction: column; gap: var(--s-3);
  align-items: center; justify-content: center; color: var(--ink); pointer-events: none; z-index: 3;
}
.wp__center--soft { background: rgba(0, 0, 0, 0.25); }
.wp__spinner {
  width: 2.8rem; height: 2.8rem; border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, 0.25); border-top-color: var(--azure-bright);
  animation: wp-spin 0.8s linear infinite;
}
@keyframes wp-spin { to { transform: rotate(360deg); } }

.wp__head {
  position: absolute; z-index: 5; top: 0; left: 0; right: 0; display: flex; align-items: center;
  gap: var(--s-3); padding: var(--s-4) var(--s-5);
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.75), transparent);
  transition: opacity var(--t-base), transform var(--t-base) var(--ease-silk);
}
.wp.is-idle .wp__head { opacity: 0; transform: translateY(-8px); pointer-events: none; }
.wp__titles h2 { font-size: var(--fs-md); color: #fff; font-weight: 600; }
.wp__titles p { font-size: var(--fs-xs); color: var(--ink-soft); }
.wp__badge {
  margin-left: auto; font-family: var(--font-mono); font-size: var(--fs-2xs);
  padding: 2px 0.5rem; border-radius: var(--r-pill); color: var(--azure-bright);
  border: 1px solid color-mix(in srgb, var(--azure) 40%, transparent);
  background: color-mix(in srgb, var(--azure) 12%, transparent);
}

.wp__bar {
  position: absolute; z-index: 5; left: 0; right: 0; bottom: 0; padding: 0 var(--s-5) var(--s-3);
  background: linear-gradient(0deg, rgba(0, 0, 0, 0.85), transparent);
  transition: opacity var(--t-base), transform var(--t-base) var(--ease-silk);
}
.wp.is-idle .wp__bar { opacity: 0; transform: translateY(8px); pointer-events: none; }

.wp__timeline {
  position: relative; height: 0.3125rem; border-radius: 3px; background: rgba(255, 255, 255, 0.18);
  cursor: pointer; margin-bottom: var(--s-3);
}
.wp__timeline:hover { height: 0.5rem; }
.wp__tl-cur { position: absolute; inset: 0 auto 0 0; border-radius: 3px; background: var(--azure-bright); }
.wp__tl-knob {
  position: absolute; top: 50%; width: 0.875rem; height: 0.875rem; border-radius: 50%;
  background: var(--azure-bright); transform: translate(-50%, -50%);
  opacity: 0; transition: opacity var(--t-fast); box-shadow: 0 0 8px var(--azure-glow);
}
.wp__timeline:hover .wp__tl-knob { opacity: 1; }
/* Durante el arrastre no se depende del :hover — el puntero puede estar fuera de la barra. */
.wp__timeline.is-drag { height: 0.5rem; }
.wp__timeline.is-drag .wp__tl-knob { opacity: 1; transform: translate(-50%, -50%) scale(1.15); }
.wp__timeline.is-drag { cursor: grabbing; }

.wp__preview {
  position: absolute; bottom: calc(100% + var(--s-3)); transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: var(--s-1);
  pointer-events: none; z-index: 3;
}
.wp__preview-img {
  width: 15rem; max-width: 40vw; aspect-ratio: 16 / 9; object-fit: cover; display: block;
  border-radius: var(--r-sm); border: 1px solid rgba(255, 255, 255, 0.22);
  background: #05070d; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.55);
  opacity: 0; transition: opacity var(--t-fast);
}
.wp__preview-t {
  font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; color: #fff;
  padding: 2px 0.5rem; border-radius: var(--r-pill); background: rgba(7, 10, 18, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.wp__row { display: flex; align-items: center; gap: var(--s-2); }
.wp__ic {
  display: grid; place-items: center; width: 2.75rem; height: 2.75rem;
  border: none; background: transparent; color: #fff; cursor: pointer;
  border-radius: var(--r-sm); transition: background var(--t-fast);
}
.wp__ic:hover, .wp__ic.is-on, .wp__ic:focus-visible { background: rgba(255, 255, 255, 0.14); }
.wp__sk { font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; }

.wp__skipop {
  display: inline-flex; align-items: center; gap: 0.3125rem; padding: var(--s-2) var(--s-3);
  border-radius: var(--r-pill); border: 1px solid rgba(255, 255, 255, 0.25);
  background: rgba(255, 255, 255, 0.08); color: #fff; font-size: var(--fs-xs); font-weight: 600;
  cursor: pointer; transition: all var(--t-fast);
}
.wp__skipop:hover { background: color-mix(in srgb, var(--azure) 40%, transparent); border-color: var(--azure-glow); }

.wp__vol { display: flex; align-items: center; gap: 4px; }
.wp__vol input[type='range'] { width: 6rem; accent-color: var(--azure-bright); cursor: pointer; }
.wp__boost { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700;
  color: var(--cyan); padding: 1px 0.3125rem; border-radius: var(--r-pill); background: var(--cyan-glow); }
.wp__subnote span { margin-right: 0; color: var(--ink-dim); font-size: var(--fs-2xs); }
.wp__time { font-family: var(--font-mono); font-size: var(--fs-xs); color: #fff; white-space: nowrap; }
.wp__time em { font-style: normal; color: var(--ink-soft); }
.wp__gap { flex: 1; }

.wp__ctl {
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); cursor: pointer;
  border: none; background: transparent; color: #fff; font-size: var(--fs-xs); font-weight: 600;
  min-height: 2.75rem; display: inline-flex; align-items: center; gap: 4px; transition: background var(--t-fast);
}
.wp__ctl:hover, .wp__ctl.is-on, .wp__ctl:focus-visible { background: rgba(255, 255, 255, 0.14); }
.wp__ctl.is-glow { color: var(--cyan); text-shadow: 0 0 8px var(--cyan-glow); }

.wp__menuwrap { position: relative; }
.wp__menu {
  position: absolute; bottom: calc(100% + var(--s-2)); right: 0; min-width: 11rem;
  max-height: 18rem; overflow-y: auto; padding: var(--s-1);
  background: rgba(10, 14, 24, 0.96); border: 1px solid var(--line); border-radius: var(--r-md);
  backdrop-filter: blur(10px); box-shadow: var(--shadow-lg);
}
.wp__menu button {
  display: block; width: 100%; text-align: left; padding: var(--s-2) var(--s-3);
  border: none; background: transparent; color: var(--ink); font-size: var(--fs-xs);
  border-radius: var(--r-xs); cursor: pointer; min-height: 2.5rem;
}
.wp__nota {
  margin: 0 0 var(--s-1); padding: var(--s-2) var(--s-3); max-width: 17rem;
  font-size: var(--fs-xs); line-height: 1.4; color: var(--ink-dim);
  background: var(--azure-haze); border-radius: var(--r-xs);
}
.wp__menu button:hover { background: var(--azure-haze); color: #fff; }
.wp__menu button.is-sel { color: var(--azure-bright); font-weight: 700; }

/* stepper de tamaño/sincronía de subtítulos (fijo arriba; las pistas scrollean debajo) */
.wp__subsize {
  position: sticky; top: calc(-1 * var(--s-1)); z-index: 1;
  display: flex; align-items: center; gap: var(--s-2);
  margin: calc(-1 * var(--s-1)) calc(-1 * var(--s-1)) var(--s-1);
  padding: var(--s-2) var(--s-3);
  background: rgba(10, 14, 24, 0.96); border-bottom: 1px solid var(--line);
}
.wp__subsize span { font-size: var(--fs-xs); color: var(--ink-dim); margin-right: auto; }
.wp__subsize b { font-size: var(--fs-xs); color: var(--ink); min-width: 2.6rem; text-align: center; }
.wp__subsize b.is-zero { color: var(--ink-dim); }
.wp__subsize button {
  display: grid; place-items: center; width: 1.8rem; min-height: 1.8rem; padding: 0;
  border-radius: var(--r-xs); font-size: var(--fs-sm); line-height: 1;
  background: rgba(255, 255, 255, 0.08); color: #fff; border: none; cursor: pointer;
}
.wp__subsize button:disabled { opacity: 0.35; cursor: default; }

.wp__eps {
  position: absolute; top: 0; right: 0; bottom: 0; width: min(21rem, 86vw);
  display: flex; flex-direction: column; z-index: 6;
  background: rgba(10, 14, 24, 0.94); border-left: 1px solid var(--line);
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
.wp__ep:hover, .wp__ep:focus-visible { background: rgba(255, 255, 255, 0.08); }
.wp__ep.is-cur { background: color-mix(in srgb, var(--azure) 22%, transparent); }
.wp__ep.is-off { opacity: 0.45; cursor: default; }
.wp__ep.is-off:hover { background: transparent; }
.wp__ep-th {
  position: relative; flex-shrink: 0; width: 6.5rem; aspect-ratio: 16 / 9;
  border-radius: var(--r-xs); overflow: hidden; background: rgba(255, 255, 255, 0.06);
}
.wp__ep-th img { width: 100%; height: 100%; object-fit: cover; }
.wp__ep-now { position: absolute; inset: 0; display: grid; place-items: center;
  color: #fff; background: rgba(7, 10, 18, 0.45); }
.wp__ep-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.wp__ep-num { font-size: var(--fs-sm); font-weight: 600; color: #fff; }
.wp__ep-meta { font-size: var(--fs-2xs); color: var(--ink-soft);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Aviso "Siguiente episodio" (Netflix-style). Flota abajo-derecha, sobre los controles,
   visible aunque la UI esté oculta. Miniatura del próximo episodio + CTA. */
.wp__cue {
  position: absolute; right: var(--s-5); bottom: 5.5rem; z-index: 6;
  display: flex; align-items: stretch; gap: var(--s-1);
  padding: var(--s-2); border-radius: var(--r-md);
  background: var(--glass-strong); backdrop-filter: blur(14px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-lg);
  max-width: min(24rem, 72vw);
}
.wp__cue-main { display: flex; align-items: center; gap: var(--s-3); min-width: 0;
  padding: var(--s-1); border-radius: var(--r-sm); transition: background var(--t-fast); }
.wp__cue-main:hover { background: rgba(255,255,255,.06); }
.wp__cue-thumb { position: relative; flex-shrink: 0; width: 5.25rem; aspect-ratio: 16/9;
  border-radius: var(--r-sm); overflow: hidden; background: #0a0e18; display: grid; place-items: center; }
.wp__cue-thumb img { width: 100%; height: 100%; object-fit: cover; }
.wp__cue-play { position: absolute; inset: 0; display: grid; place-items: center;
  color: #fff; background: rgba(7,10,18,.35); transition: background var(--t-fast); }
.wp__cue-main:hover .wp__cue-play { background: rgba(7,10,18,.15); }
.wp__cue-info { display: flex; flex-direction: column; gap: 1px; min-width: 0; text-align: left; }
.wp__cue-eyebrow { font-size: var(--fs-2xs); font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--azure-bright); }
.wp__cue-title { font-size: var(--fs-sm); font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wp__cue-cta { margin-top: 2px; font-size: var(--fs-2xs); font-weight: 700; color: var(--ink-soft); }
.wp__cue-close { flex-shrink: 0; width: 1.625rem; height: 1.625rem; display: grid; place-items: center;
  border-radius: var(--r-sm); color: var(--ink-soft); transition: color var(--t-fast); align-self: flex-start; }
.wp__cue-close:hover { color: #fff; }
/* Aparición/desaparición suave y discreta */
.wp-cue-enter-active { transition: transform var(--t-base) var(--ease-silk), opacity var(--t-base); }
.wp-cue-leave-active { transition: transform var(--t-base) var(--ease-silk), opacity var(--t-base); }
.wp-cue-enter-from, .wp-cue-leave-to { transform: translateY(1rem); opacity: 0; }

</style>

<style>
/* Airspace global: transparenta la página y oculta el shell bajo el player nativo,
   para que el vídeo (que libmpv pinta por DEBAJO del WebView) sea visible. */
html.native-video,
html.native-video body,
html.native-video #app { background: transparent !important; }
/* Nada de scroll sobre el vídeo (evita la barra azul a la derecha). */
html.native-video,
html.native-video body { overflow: hidden !important; }
/* base.css pinta un glow cósmico OPACO fijo a pantalla completa (body::before) +
   grano (body::after): hay que quitarlos o taparían el vídeo. */
html.native-video body::before,
html.native-video body::after { display: none !important; }
html.native-video .shell { visibility: hidden !important; }
/* …salvo la barra de título: en modo ventana (no fullscreen) sigue visible sobre el vídeo
   para poder arrastrar/cerrar la ventana sin salir del reproductor. La cabecera del player
   baja su alto para no solaparla. En fullscreen la barra se oculta (regla global .native-fs). */
html.native-video.native-shell:not(.native-fs) .tb { visibility: visible !important; }
html.native-video:not(.native-fs) .wp__head { top: var(--titlebar-h); }
/* El panel de episodios también arranca en top:0 y su cabecera quedaba TAPADA por la barra de
   título de la ventana (✕/min/max). En modo ventana empieza bajo la barra; en fullscreen (sin
   barra) sigue de arriba abajo. */
html.native-video:not(.native-fs) .wp__eps { top: var(--titlebar-h); }
</style>
