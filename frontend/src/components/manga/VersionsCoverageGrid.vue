<script setup>
// Heatmap capítulo×fuente: qué capítulos tiene cada fuente candidata, y cuál está
// ASIGNADA a cada uno ahora mismo. Renderizado en <canvas> (no un nodo DOM por celda)
// para que series largas (500+ capítulos) sigan siendo rápidas de pintar/interactuar.
// Arrastre horizontal sobre la fila de una fuente selecciona un rango de capítulos y
// emite `assign-range` para que el llamador confirme la asignación.
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'

const props = defineProps({
  sources: { type: Array, default: () => [] },   // [{sourceKind,sourceId,mangaId,sourceName,sourceLang,chapters[],count,completeness,quality}]
  assigned: { type: Object, default: () => ({}) }, // {chapterNorm: {sourceId,mangaId,...}}
  totalKnownChapters: { type: Number, default: 0 },
})
const emit = defineEmits(['assign-range', 'select-source'])

const ROW_H = 22
const ROW_GAP = 3
const PAD_L = 4
const canvasEl = ref(null)
const wrapEl = ref(null)
const colWidth = ref(6)
const drag = ref(null)       // { rowIdx, startCol, endCol }
const hoverInfo = ref(null)  // { x, y, text }

// Columnas = el conjunto REAL de capítulos conocidos (unión de todas las fuentes), ordenado
// numéricamente — NO "col+1": los capítulos suelen tener decimales (12.5) o huecos, así que
// asumir enteros consecutivos desalineaba el color de cada celda Y el rango que se seleccionaba
// al arrastrar (dos bugs reportados: "no se marcan/seleccionan correctamente").
const _chnum = (x) => { const n = parseFloat(x); return Number.isFinite(n) ? n : 0 }
const allChapters = computed(() => {
  const set = new Set()
  for (const s of props.sources) for (const c of (s.chapters || [])) set.add(c)
  for (const c of Object.keys(props.assigned || {})) set.add(c)
  return [...set].sort((a, b) => _chnum(a) - _chnum(b))
})
const chapterCols = computed(() => Math.max(1, allChapters.value.length))

function sourceKey(s) { return `${s.sourceId}_${s.mangaId}` }
function chnAt(col) { return allChapters.value[col] }

// Estado por celda: 'assigned' (cubierto Y es la fuente asignada a ese capítulo), 'alt'
// (cubierto pero otra fuente distinta está asignada, o nada asignado) o null (no cubierto).
function cellKind(src, col) {
  const chn = chnAt(col)
  const covered = chn != null && src.chaptersSet.has(chn)
  if (!covered) return null
  const a = props.assigned[chn]
  const isAssigned = a && String(a.sourceId) === String(src.sourceId) && String(a.mangaId) === String(src.mangaId)
  return isAssigned ? 'assigned' : 'alt'
}

const rows = computed(() => props.sources.map(s => ({ ...s, chaptersSet: new Set(s.chapters) })))

// `ctx.fillStyle` de un <canvas> NO entiende `var(--token)` (las custom properties solo se
// resuelven dentro de la cascada CSS del DOM, no en el contexto 2D) — asignar un string con
// var() se ignora en silencio y la celda queda invisible. Hay que resolver el valor real con
// getComputedStyle antes de pintar.
function cssVar(name, fallback) {
  if (!canvasEl.value) return fallback
  const v = getComputedStyle(canvasEl.value).getPropertyValue(name).trim()
  return v || fallback
}

function draw() {
  const canvas = canvasEl.value
  if (!canvas) return
  const dpr = window.devicePixelRatio || 1
  const w = Math.max(1, chapterCols.value * colWidth.value + PAD_L)
  const h = Math.max(1, rows.value.length * (ROW_H + ROW_GAP))
  canvas.width = Math.round(w * dpr)
  canvas.height = Math.round(h * dpr)
  canvas.style.width = w + 'px'
  canvas.style.height = h + 'px'
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)
  const cyanColor = cssVar('--cyan', '#2dd4bf')
  const azureColor = cssVar('--azure', '#3b82f6')
  const inkColor = cssVar('--ink', '#e6e9ef')
  rows.value.forEach((src, ri) => {
    const y = ri * (ROW_H + ROW_GAP)
    for (let col = 0; col < chapterCols.value; col++) {
      const kind = cellKind(src, col)
      if (!kind) continue
      ctx.globalAlpha = kind === 'assigned' ? 1 : 0.55
      ctx.fillStyle = kind === 'assigned' ? cyanColor : azureColor
      ctx.fillRect(PAD_L + col * colWidth.value, y, Math.max(1, colWidth.value - 1), ROW_H)
    }
  })
  ctx.globalAlpha = 1
  // resaltado de arrastre en curso
  if (drag.value) {
    const { rowIdx, startCol, endCol } = drag.value
    const lo = Math.min(startCol, endCol), hi = Math.max(startCol, endCol)
    const y = rowIdx * (ROW_H + ROW_GAP)
    ctx.strokeStyle = inkColor
    ctx.lineWidth = 2
    ctx.strokeRect(PAD_L + lo * colWidth.value, y, (hi - lo + 1) * colWidth.value, ROW_H)
  }
}

function coordsToCell(evt) {
  const rect = canvasEl.value.getBoundingClientRect()
  const x = evt.clientX - rect.left
  const y = evt.clientY - rect.top
  const rowIdx = Math.floor(y / (ROW_H + ROW_GAP))
  const col = Math.floor((x - PAD_L) / colWidth.value)
  if (rowIdx < 0 || rowIdx >= rows.value.length || col < 0 || col >= chapterCols.value) return null
  return { rowIdx, col }
}

function onMouseDown(evt) {
  const hit = coordsToCell(evt)
  if (!hit) return
  drag.value = { rowIdx: hit.rowIdx, startCol: hit.col, endCol: hit.col }
}
function onMouseMoveCanvas(evt) {
  const hit = coordsToCell(evt)
  if (drag.value && hit && hit.rowIdx === drag.value.rowIdx) {
    drag.value = { ...drag.value, endCol: hit.col }
    draw()
  }
  if (hit) {
    const src = rows.value[hit.rowIdx]
    const chn = chnAt(hit.col)
    const has = chn != null && src.chaptersSet.has(chn)
    hoverInfo.value = { x: evt.clientX, y: evt.clientY,
      text: `${src.sourceName} · cap ${chn ?? '?'}${has ? '' : ' (no disponible)'}` }
  } else {
    hoverInfo.value = null
  }
}
function onMouseUp() {
  if (!drag.value) return
  const { rowIdx, startCol, endCol } = drag.value
  const src = rows.value[rowIdx]
  drag.value = null
  draw()
  const lo = Math.min(startCol, endCol)
  const hi = Math.max(startCol, endCol)
  if (lo === hi) { emit('select-source', src); return }
  // Solo los capítulos del rango que ESTA fuente realmente tiene — evita mandar huecos.
  const chapters = allChapters.value.slice(lo, hi + 1).filter(c => src.chaptersSet.has(c))
  if (!chapters.length) return
  emit('assign-range', { source: src, chapters })
}
function onMouseLeave() { hoverInfo.value = null }

let ro = null
onMounted(() => {
  nextTick(draw)
  ro = new ResizeObserver(() => draw())
  if (wrapEl.value) ro.observe(wrapEl.value)
})
onBeforeUnmount(() => { if (ro) ro.disconnect() })
watch(() => [props.sources, props.assigned, props.totalKnownChapters], () => nextTick(draw), { deep: true })
</script>

<template>
  <div class="cvg" ref="wrapEl">
    <div class="cvg__rows">
      <div v-for="s in rows" :key="sourceKey(s)" class="cvg__rowlabel" :style="{ height: ROW_H + 'px', marginBottom: ROW_GAP + 'px' }">
        <span class="cvg__name">{{ s.sourceName }}</span>
        <span class="cvg__meta">{{ s.count }} cap.<template v-if="s.completeness != null"> · {{ Math.round(s.completeness * 100) }}%</template></span>
      </div>
    </div>
    <div class="cvg__scroll">
      <canvas ref="canvasEl" class="cvg__canvas"
        @mousedown="onMouseDown" @mousemove="onMouseMoveCanvas" @mouseup="onMouseUp" @mouseleave="onMouseLeave" />
    </div>
    <div v-if="hoverInfo" class="cvg__tip" :style="{ left: hoverInfo.x + 12 + 'px', top: hoverInfo.y + 12 + 'px' }">{{ hoverInfo.text }}</div>
  </div>
</template>

<style scoped>
.cvg { display: flex; gap: var(--s-2); align-items: flex-start; }
.cvg__rows { display: flex; flex-direction: column; flex-shrink: 0; width: 9rem; }
.cvg__rowlabel { display: flex; flex-direction: column; justify-content: center; font-size: var(--fs-2xs); overflow: hidden; }
.cvg__name { color: var(--ink); white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.cvg__meta { color: var(--ink-faint); font-family: var(--font-mono); }
.cvg__scroll { overflow-x: auto; flex: 1; min-width: 0; }
.cvg__canvas { display: block; cursor: crosshair; }
.cvg__tip { position: fixed; z-index: 50; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm);
  padding: 3px 8px; font-size: var(--fs-2xs); color: var(--ink); pointer-events: none; white-space: nowrap; }
</style>
