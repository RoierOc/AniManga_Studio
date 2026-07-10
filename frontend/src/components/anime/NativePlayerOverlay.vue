<script setup>
// Overlay del reproductor NATIVO embebido (libmpv en la shell). El vídeo lo pinta
// mpv por DEBAJO del WebView2 transparente (ver desktop/native): aquí va SOLO la UI,
// con la MISMA estética/funcionalidad que PlayerOverlay.vue (clases wp__*). "Airspace":
// mientras hay vídeo transparentamos la página y ocultamos el shell para ver el vídeo.
// Los controles hablan con el motor por el store (nativeBridge → Rust → mpv).
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
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
  { id: 'fast', label: 'Rápido (Modo A · M)' },
  { id: 'medium', label: 'Medio (A+A · VL)' },
  { id: 'high', label: 'A+A UL (Máx. calidad) · CTRL+8' },
  { id: 'ultra', label: 'A+A UL + Thin (Máx. + bordes) · CTRL+9' },
]
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]

const playing = computed(() => np.value && !np.value.paused)
const pos = computed(() => np.value?.pos || 0)
const duration = computed(() => np.value?.duration || 0)
const pct = computed(() => (duration.value > 0 ? Math.min(100, (pos.value / duration.value) * 100) : 0))
const muted = computed(() => store.nativeVol === 0)

const audioIndex = computed(() => (np.value ? np.value.aid - 1 : 0))
const subIndex = computed(() => (np.value ? np.value.sid - 1 : -1))

// Episodios para el panel lateral (cambiar sin salir), igual que el original.
const panelEps = computed(() => {
  const eps = (np.value?.anime?.episodes || []).filter((e) => e.num > 0 && e.ep_type !== 'special')
  return [...eps].sort((a, b) => a.num - b.num)
})
function isPlayable(e) {
  return !!(e.in_local || e.info_hash)
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
    if (open) { poke(); menuOpen.value = ''; epPanel.value = false }
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
watch(uiVisible, (v) => nativeSend('cursor', { hide: !v }))

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

function seekTo(e) {
  if (!duration.value) return
  const r = e.currentTarget.getBoundingClientRect()
  store.nativeSeek(((e.clientX - r.left) / r.width) * duration.value)
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
function goNext() {
  const n = nextEp.value
  if (n) store.playNative(np.value.anime, n)
}
function playFromPanel(e) {
  if (!isPlayable(e) || e.num === np.value.ep.num) return
  epPanel.value = false
  store.playNative(np.value.anime, e)
}

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

      <!-- botón grande de play cuando está en pausa -->
      <button
        v-if="!np.loading && np.paused"
        class="wp__bigplay"
        @click="togglePlay"
      >
        <Icon name="play" :size="34" />
      </button>

      <!-- cabecera -->
      <header class="wp__head">
        <button class="wp__ic" title="Volver" @click="close">
          <Icon name="chevron" :size="20" style="transform: rotate(90deg)" />
        </button>
        <div class="wp__titles">
          <h2>{{ np.ep?.title || `Episodio ${np.ep?.num}` }}</h2>
          <p>{{ np.anime?.title }}</p>
        </div>
      </header>

      <!-- barra de controles -->
      <footer class="wp__bar" @click.stop>
        <!-- timeline -->
        <div class="wp__timeline" @click="seekTo" @mousemove="onTlHover" @mouseleave="tlHover = -1">
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
          <button class="wp__ic" @click="togglePlay" :title="playing ? 'Pausa' : 'Reproducir'">
            <Icon :name="playing ? 'pause' : 'play'" :size="20" />
          </button>
          <button class="wp__ic" title="-10 s" @click="skip(-10)"><span class="wp__sk">-10</span></button>
          <button class="wp__ic" title="+10 s" @click="skip(10)"><span class="wp__sk">+10</span></button>
          <button class="wp__skipop" title="Saltar opening" @click="skipOp">
            <Icon name="spark" :size="13" /> Saltar OP
          </button>

          <div class="wp__vol">
            <button class="wp__ic" @click="toggleMute" :title="muted ? 'Quitar silencio' : 'Silenciar'">
              <span class="wp__sk">{{ muted ? '🔇' : '🔊' }}</span>
            </button>
            <!-- La barra llega a 100 (como mpv); la rueda del ratón la supera hasta 150,
                 que se refleja solo en el badge, no en el relleno de la barra. -->
            <input type="range" min="0" max="100" step="1" :value="Math.min(100, store.nativeVol)" @input="onVol"
                   :title="`Volumen ${store.nativeVol}% — rueda del ratón para superar el 100% (hasta 150%)`" />
            <span v-if="store.nativeVol > 100" class="wp__boost" title="Volumen aumentado por encima del 100% (rueda del ratón)">{{ store.nativeVol }}%</span>
          </div>

          <span class="wp__time">{{ fmt(pos) }} <em>/ {{ fmt(duration) }}</em></span>
          <span class="wp__gap" />

          <!-- audio -->
          <div class="wp__menuwrap" v-if="np.audioTracks.length > 1">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'audio' }"
                    @click="menuOpen = menuOpen === 'audio' ? '' : 'audio'">Audio</button>
            <div v-if="menuOpen === 'audio'" class="wp__menu">
              <button v-for="(t, i) in np.audioTracks" :key="'a' + i"
                      :class="{ 'is-sel': i === audioIndex }" @click="setAudio(i)">{{ trackLabel(t, i) }}</button>
            </div>
          </div>
          <!-- subtítulos: siempre disponible (delay/tamaño/config), incluso con 0-1 pistas -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'subs' }"
                    @click="menuOpen = menuOpen === 'subs' ? '' : 'subs'">Subtítulos</button>
            <div v-if="menuOpen === 'subs'" class="wp__menu">
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
          <!-- Anime4K -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'a4k', 'is-glow': np.tier !== 'off' }"
                    title="Anime4K (mejora de imagen por GPU)"
                    @click="menuOpen = menuOpen === 'a4k' ? '' : 'a4k'">
              <Icon name="spark" :size="13" /> {{ np.tier === 'off' ? 'Anime4K' : 'A4K·Alto' }}
            </button>
            <div v-if="menuOpen === 'a4k'" class="wp__menu">
              <div class="wp__subsize" @click.stop title="Ajusta los medios tonos (gamma) para igualar tu mpv. Doble clic = neutro.">
                <span>Gamma</span>
                <button :disabled="np.bright <= 0.5" aria-label="Menos gamma" @click="bumpBright(-0.05)">−</button>
                <b :class="{ 'is-zero': np.bright === 1 }" @dblclick="setBright(1)">{{ Math.round(np.bright * 100) }}%</b>
                <button :disabled="np.bright >= 3" aria-label="Más gamma" @click="bumpBright(0.05)">+</button>
              </div>
              <div class="wp__subsize" @click.stop title="Saturación: devuelve el punch que el gamma quita. Doble clic = neutro.">
                <span>Saturación</span>
                <button :disabled="np.sat <= 0.5" aria-label="Menos saturación" @click="bumpSat(-0.05)">−</button>
                <b :class="{ 'is-zero': np.sat === 1 }" @dblclick="setSat(1)">{{ Math.round((np.sat ?? 1) * 100) }}%</b>
                <button :disabled="np.sat >= 3" aria-label="Más saturación" @click="bumpSat(0.05)">+</button>
              </div>
              <button v-for="m in A4K_MODES" :key="m.id" :class="{ 'is-sel': m.id === np.tier }"
                      @click="setA4k(m.id)">{{ m.label }}</button>
            </div>
          </div>

          <!-- velocidad -->
          <div class="wp__menuwrap">
            <button class="wp__ctl" :class="{ 'is-on': menuOpen === 'speed' }"
                    @click="menuOpen = menuOpen === 'speed' ? '' : 'speed'">{{ np.speed }}×</button>
            <div v-if="menuOpen === 'speed'" class="wp__menu">
              <button v-for="s in SPEEDS" :key="s" :class="{ 'is-sel': s === np.speed }"
                      @click="setSpeed(s)">{{ s }}×</button>
            </div>
          </div>

          <button v-if="nextEp" class="wp__ctl" title="Siguiente episodio" @click="goNext">
            Siguiente <Icon name="chevron" :size="14" style="transform: rotate(-90deg)" />
          </button>
          <button v-if="panelEps.length > 1" class="wp__ctl" :class="{ 'is-on': epPanel }"
                  title="Lista de episodios" @click="epPanel = !epPanel">
            <Icon name="menu" :size="14" /> Episodios
          </button>
          <button class="wp__ic" :class="{ 'is-on': np.fullscreen }"
                  :title="np.fullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'" @click="toggleFs">
            <Icon :name="np.fullscreen ? 'collapse' : 'expand'" :size="18" />
          </button>
        </div>
      </footer>

      <!-- panel de episodios -->
      <Transition name="wp-eps">
        <aside v-if="epPanel" class="wp__eps" @click.stop @mousemove.stop="poke">
          <header class="wp__eps-head">
            <h3>{{ np.anime?.title }}</h3>
            <button class="wp__ic" title="Cerrar" @click="epPanel = false"><Icon name="close" :size="16" /></button>
          </header>
          <div class="wp__eps-list">
            <button v-for="e in panelEps" :key="e.num" class="wp__ep"
                    :class="{ 'is-cur': e.num === np.ep.num, 'is-off': !isPlayable(e) }"
                    @click="playFromPanel(e)">
              <span class="wp__ep-th">
                <img :src="`/api/anime/thumb/${np.anime.id}/${e.num}`" alt="" loading="lazy" @error="($event.target.style.visibility = 'hidden')" />
                <span v-if="e.num === np.ep.num" class="wp__ep-now"><Icon name="play" :size="16" /></span>
              </span>
              <span class="wp__ep-info">
                <span class="wp__ep-num">Episodio {{ e.num }}</span>
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

.wp__bigplay {
  position: absolute; z-index: 3; width: 5rem; height: 5rem; border-radius: 50%;
  display: grid; place-items: center; color: #fff;
  background: color-mix(in srgb, var(--azure) 80%, transparent);
  border: none; cursor: pointer; transition: transform var(--t-fast), background var(--t-fast);
}
.wp__bigplay:hover { transform: scale(1.08); background: var(--azure-bright); }

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
  padding: 2px 8px; border-radius: var(--r-pill); color: var(--azure-bright);
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
  position: relative; height: 5px; border-radius: 3px; background: rgba(255, 255, 255, 0.18);
  cursor: pointer; margin-bottom: var(--s-3);
}
.wp__timeline:hover { height: 8px; }
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
.wp__preview-img {
  width: 15rem; max-width: 40vw; aspect-ratio: 16 / 9; object-fit: cover; display: block;
  border-radius: var(--r-sm); border: 1px solid rgba(255, 255, 255, 0.22);
  background: #05070d; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.55);
  opacity: 0; transition: opacity var(--t-fast);
}
.wp__preview-t {
  font-family: var(--font-mono); font-size: var(--fs-xs); font-weight: 700; color: #fff;
  padding: 2px 8px; border-radius: var(--r-pill); background: rgba(7, 10, 18, 0.85);
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
  display: inline-flex; align-items: center; gap: 5px; padding: var(--s-2) var(--s-3);
  border-radius: var(--r-pill); border: 1px solid rgba(255, 255, 255, 0.25);
  background: rgba(255, 255, 255, 0.08); color: #fff; font-size: var(--fs-xs); font-weight: 600;
  cursor: pointer; transition: all var(--t-fast);
}
.wp__skipop:hover { background: color-mix(in srgb, var(--azure) 40%, transparent); border-color: var(--azure-glow); }

.wp__vol { display: flex; align-items: center; gap: 4px; }
.wp__vol input[type='range'] { width: 6rem; accent-color: var(--azure-bright); cursor: pointer; }
.wp__boost { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700;
  color: var(--cyan); padding: 1px 5px; border-radius: var(--r-pill); background: var(--cyan-glow); }
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
</style>
