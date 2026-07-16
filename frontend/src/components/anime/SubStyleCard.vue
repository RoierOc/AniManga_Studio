<script setup>
// Estilo de subtítulos del reproductor nativo.
//
// El estilo NO se hornea en el archivo: lo aplica mpv en vivo (`sub-ass-style-overrides`), así que
// cambiar un control se ve al instante sobre el episodio que estés viendo, y vale también para las
// pistas que no tradujimos nosotros. Los carteles quedan fuera: el backend emite un override por
// estilo de DIÁLOGO, nunca uno global (ver `ass_style_overrides`).
import { computed, onMounted, ref } from 'vue'
import { useAnimeStore } from '@/stores/anime'
import { SUB_STYLE_DEFAULT } from '@/stores/anime'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'

const store = useAnimeStore()
const s = computed(() => store.nativeSubStyle)

onMounted(() => store.loadSubFonts())

const set = (k, v) => store.setNativeSubStyle({ [k]: String(v) })

const isDefault = computed(() =>
  Object.keys(SUB_STYLE_DEFAULT).every((k) => s.value[k] === SUB_STYLE_DEFAULT[k]))

// Fondo de la vista previa: arte real de tu biblioteca, borroso y oscurecido. Sólo decora —
// los subtítulos se leen sobre imagen, no sobre un color plano, y el arte nunca toca el texto.
const backdrop = computed(() => {
  const a = store.library.find((x) => x.cover)
  return a ? imgProxy(a.cover) : ''
})

const previewStyle = computed(() => {
  const px = Math.max(12, Number(s.value.size || 26))
  const o = Number(s.value.outline || 0)
  const sh = Number(s.value.shadow || 0)
  const shadows = []
  if (sh > 0) shadows.push(`${sh}px ${sh}px ${sh * 1.5}px rgba(0,0,0,.9)`)
  return {
    ...famStyle(s.value.font),
    fontSize: `${px}px`,
    fontWeight: s.value.bold === '-1' ? 700 : 400,
    WebkitTextStroke: o > 0 ? `${o}px rgba(0,0,0,.92)` : 'none',
    paintOrder: 'stroke fill',
    textShadow: shadows.join(', ') || 'none',
  }
})

const missing = computed(() =>
  s.value.font && store.subFonts.length && !store.subFonts.some((f) => f.family === s.value.font))

// Cada opción del desplegable se pinta CON su propia fuente (la WebView las tiene instaladas),
// así se elige viendo la letra en vez de leyendo un nombre.
const famStyle = (fam) => ({ fontFamily: fam ? `"${fam}", sans-serif` : 'inherit' })
</script>

<template>
  <section class="card">
    <div class="card__title">
      <Icon name="spark" :size="16" /> Estilo de subtítulos
      <span class="tag">reproductor nativo</span>
    </div>

    <div class="ss">
      <div class="ss__ctrls">
        <label class="fld">
          <span>Fuente <em>· {{ store.subFonts.length }} instaladas en Windows</em></span>
          <select class="ss__sel" :value="s.font" :style="famStyle(s.font)" @change="set('font', $event.target.value)">
            <option value="">Dejar la del archivo</option>
            <option v-for="f in store.subFonts" :key="f.family" :value="f.family" :style="famStyle(f.family)">
              {{ f.family }}
            </option>
          </select>
          <p v-if="missing" class="hint hint--warn">
            «{{ s.font }}» no está instalada: libass usará otra parecida sin avisar.
          </p>
        </label>

        <label class="fld">
          <span>Cuerpo <em>· {{ s.size || '—' }}</em></span>
          <input type="range" min="14" max="48" step="1" :value="s.size || 26" @input="set('size', $event.target.value)" />
        </label>

        <label class="fld">
          <span>Borde <em>· {{ s.outline || '0' }}</em></span>
          <input type="range" min="0" max="4" step="1" :value="s.outline || 0" @input="set('outline', $event.target.value)" />
        </label>

        <label class="fld">
          <span>Sombra <em>· {{ s.shadow || '0' }}</em></span>
          <input type="range" min="0" max="4" step="1" :value="s.shadow || 0" @input="set('shadow', $event.target.value)" />
        </label>

        <div class="ss__row">
          <button class="btn" :class="{ 'btn--accent': s.bold === '-1' }"
                  @click="set('bold', s.bold === '-1' ? '0' : '-1')">
            <Icon name="check" v-if="s.bold === '-1'" :size="13" /> Negrita
          </button>
          <button class="btn" :disabled="isDefault" @click="store.resetNativeSubStyle()">
            <Icon name="refresh" :size="13" /> Restablecer
          </button>
        </div>
      </div>

      <div class="ss__prev">
        <div v-if="backdrop" class="ss__bg" :style="{ backgroundImage: `url('${backdrop}')` }" />
        <div class="ss__scrim" />
        <p class="ss__line" :style="previewStyle">Vaya, qué frío hace hoy…</p>
      </div>
    </div>

    <p class="hint">
      Se aplica al instante sobre lo que estés viendo, sin re-traducir nada, y también a los
      subtítulos que ya venían en el archivo. Los <strong>carteles y canciones</strong> conservan
      su tipografía: el grupo los alineó con el arte del vídeo.
      <br />La vista previa es orientativa — el tamaño real se juzga mejor en el reproductor, que
      cambia en vivo.
    </p>
  </section>
</template>

<style scoped>
/* .card/.fld/.btn/.tag/.hint viven en SettingsView con <style scoped>, así que NO llegan aquí.
   Se replican con los mismos tokens para que la tarjeta sea autónoma y encaje igual. */
.card { padding: var(--s-5); border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface); }
.card__title { display: flex; align-items: center; gap: var(--s-2); font-weight: 600; color: var(--ink); margin-bottom: var(--s-4); }
.card__title :deep(svg) { color: var(--azure); }
.tag { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: var(--tracking-caps); color: var(--coral); border: 1px solid color-mix(in srgb, var(--coral) 35%, transparent); border-radius: var(--r-pill); padding: 1px 8px; }
.fld { display: flex; flex-direction: column; gap: 6px; font-size: var(--fs-xs); color: var(--ink-faint); }
.fld em { font-style: normal; color: var(--ink-ghost); }
.hint { font-size: var(--fs-xs); color: var(--ink-faint); margin-top: var(--s-3); line-height: 1.5; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-sm); color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.btn:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn--accent { background: var(--azure); color: #fff; border-color: transparent; font-weight: 600; }
.btn--accent:hover:not(:disabled) { background: var(--azure-bright); color: #fff; }

.ss { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: var(--s-4); align-items: start; }
@media (max-width: 60rem) { .ss { grid-template-columns: 1fr; } }
.ss__ctrls { display: flex; flex-direction: column; gap: var(--s-3); min-width: 0; }
.ss__sel { width: 100%; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); border: 1px solid var(--line);
           background: var(--surface); color: var(--ink); font-size: var(--fs-sm); }
.ss__row { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.ss__prev { position: relative; aspect-ratio: 16 / 9; border-radius: var(--r-md); overflow: hidden;
            border: 1px solid var(--line-2); display: grid; place-items: end center; padding-bottom: 8%;
            background: #05070d; }
.ss__bg { position: absolute; inset: -6%; background-size: cover; background-position: center; filter: blur(10px) saturate(1.1); }
.ss__scrim { position: absolute; inset: 0; background: linear-gradient(0deg, rgba(5,7,13,.72) 0%, rgba(5,7,13,.28) 55%, rgba(5,7,13,.42) 100%); }
.ss__line { position: relative; margin: 0; padding: 0 var(--s-4); color: #fff; text-align: center; line-height: 1.25;
            transition: font-size var(--t-fast); }
.hint--warn { color: var(--warn); }
</style>
