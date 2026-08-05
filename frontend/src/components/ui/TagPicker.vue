<script setup>
/* Diálogo para etiquetar una obra. Se abre con `tags.openPicker(kind, id, título)`.
 *
 * Una etiqueta ES su texto: no hay ids ni catálogo que mantener. Las etiquetas que ya usaste
 * aparecen como sugerencias de un clic, que es lo que evita que la misma idea acabe escrita de
 * tres formas («releer», «Releer», «re-leer») y se parta en tres filtros.
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useTagsStore } from '@/stores/tags'
import { useModal } from '@/lib/useModal'
import Icon from '@/components/ui/Icon.vue'

const tags = useTagsStore()
const p = computed(() => tags.picker)
const texto = ref('')
const input = ref(null)
const dlgEl = ref(null)

useModal(() => !!p.value, () => tags.closePicker(), dlgEl)
watch(p, async (v) => { if (v) { texto.value = ''; await nextTick(); input.value?.focus() } })

// Lo que ya usaste en este dominio y aún no lleva esta obra, filtrado por lo que estés tecleando.
const sugerencias = computed(() => {
  if (!p.value) return []
  const puestas = new Set(p.value.tags.map(t => t.toLowerCase()))
  const q = texto.value.trim().toLowerCase()
  return tags.universe(p.value.kind)
    .filter(t => !puestas.has(t.toLowerCase()) && (!q || t.toLowerCase().includes(q)))
    .slice(0, 8)
})

function añadir(t) {
  const v = String(t || '').trim().slice(0, 40)
  if (!v || !p.value) return
  // Sin distinguir mayúsculas, igual que el backend: si no, «Finde» y «finde» serían dos pills.
  if (!p.value.tags.some(x => x.toLowerCase() === v.toLowerCase())) p.value.tags.push(v)
  texto.value = ''
}
function quitar(t) { p.value.tags = p.value.tags.filter(x => x !== t) }
// Retroceso con el campo vacío borra la última: el gesto estándar de un campo de chips.
function onBorrar() { if (!texto.value && p.value?.tags.length) p.value.tags.pop() }

/* Guardar CONFIRMA lo que haya a medio escribir.
 *
 * Sin esto, escribir «para el finde» y pulsar Guardar sin haber pulsado Enter antes guardaba
 * CERO etiquetas, en silencio: el texto se quedaba en el input y se tiraba con el diálogo. Es el
 * primer fallo que se llevó el usuario y es la trampa clásica de un campo de chips — quien
 * escribe algo y le da a Guardar está diciendo que lo quiere, no que se lo tiren. Lo mismo al
 * salir del campo (clic fuera). */
function guardar() { añadir(texto.value); tags.savePicker() }
</script>

<template>
  <Teleport to="body">
    <Transition name="tgp">
      <div v-if="p" class="tgp-ov" @click.self="tags.closePicker()">
        <div ref="dlgEl" class="tgp" role="dialog" aria-modal="true" aria-label="Etiquetas">
          <header class="tgp__head">
            <span class="tgp__glyph"><Icon name="spark" :size="18" /></span>
            <div class="tgp__titles">
              <h3 class="tgp__title">Etiquetas</h3>
              <p class="tgp__sub">{{ p.title }}</p>
            </div>
            <button class="tgp__x" @click="tags.closePicker()" data-tip="Cerrar"><Icon name="close" :size="16" /></button>
          </header>

          <div class="tgp__field" @click="input?.focus()">
            <span v-for="t in p.tags" :key="t" class="tgp__chip">
              {{ t }}
              <button @click.stop="quitar(t)" :aria-label="`Quitar ${t}`"><Icon name="close" :size="11" /></button>
            </span>
            <input ref="input" v-model="texto" type="text" maxlength="40"
                   :placeholder="p.tags.length ? 'Añadir otra…' : 'Escribe una etiqueta y pulsa Enter'"
                   @keydown.enter.prevent="añadir(texto)"
                   @keydown.,.prevent="añadir(texto)"
                   @keydown.delete="onBorrar"
                   @blur="añadir(texto)" />
          </div>

          <p class="tgp__hint">Enter o coma para separar. Al guardar se añade también lo que estés escribiendo.</p>

          <div v-if="sugerencias.length" class="tgp__sugs">
            <span class="tgp__sugslbl">Ya usadas</span>
            <button v-for="s in sugerencias" :key="s" class="tgp__sug" @click="añadir(s)">
              <Icon name="plus" :size="11" /> {{ s }}
            </button>
          </div>

          <div class="tgp__btns">
            <button class="tgp__btn" @click="tags.closePicker()">Cancelar</button>
            <button class="tgp__btn tgp__btn--go" @click="guardar">Guardar</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.tgp-ov {
  position: fixed; inset: 0; z-index: calc(var(--z-toast) + 1);
  display: grid; place-items: center; padding: var(--s-5);
  background: rgba(5, 7, 13, 0.6); backdrop-filter: blur(4px);
}
.tgp {
  width: min(28rem, 92vw); padding: var(--s-5);
  border-radius: var(--r-lg); background: var(--glass-strong); backdrop-filter: blur(18px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-xl);
  display: flex; flex-direction: column; gap: var(--s-4);
}
.tgp__head { display: flex; align-items: center; gap: var(--s-3); }
.tgp__glyph {
  width: 2.5rem; height: 2.5rem; display: grid; place-items: center; border-radius: var(--r-md);
  color: var(--azure-bright); background: var(--azure-haze); flex-shrink: 0;
  border: 1px solid color-mix(in srgb, var(--azure) 35%, transparent);
}
.tgp__titles { flex: 1; min-width: 0; }
.tgp__title { font-family: var(--font-display); font-size: var(--fs-lg); font-weight: 600; }
.tgp__sub { font-size: var(--fs-xs); color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tgp__x { width: 1.75rem; height: 1.75rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-faint); }
.tgp__x:hover { color: var(--ink); }

/* Campo de chips: el input crece en la misma caja que las etiquetas ya puestas. */
.tgp__field {
  display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2);
  min-height: 3rem; padding: var(--s-2) var(--s-3); cursor: text;
  border: 1px solid var(--line-2); border-radius: var(--r-md); background: var(--surface);
}
.tgp__field:focus-within { border-color: var(--azure); }
.tgp__field input { flex: 1; min-width: 8rem; background: none; border: 0; font-size: var(--fs-sm); color: var(--ink); }
.tgp__field input::placeholder { color: var(--ink-ghost); }
.tgp__chip {
  display: inline-flex; align-items: center; gap: var(--s-1);
  padding: 2px var(--s-2); border-radius: var(--r-pill);
  font-size: var(--fs-xs); font-weight: 600; color: var(--azure-bright);
  background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 35%, transparent);
}
.tgp__chip button { display: grid; place-items: center; color: inherit; opacity: .6; }
.tgp__chip button:hover { opacity: 1; }

.tgp__hint { font-size: var(--fs-2xs); color: var(--ink-ghost); margin-top: calc(var(--s-3) * -1); }
.tgp__sugs { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); }
.tgp__sugslbl { font-size: var(--fs-2xs); color: var(--ink-ghost); text-transform: uppercase; letter-spacing: .06em; }
.tgp__sug {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 2px var(--s-2); border-radius: var(--r-pill);
  font-size: var(--fs-xs); color: var(--ink-soft); border: 1px solid var(--line-2);
}
.tgp__sug:hover { color: var(--ink); border-color: var(--azure); }

.tgp__btns { display: flex; justify-content: flex-end; gap: var(--s-2); }
.tgp__btn {
  padding: var(--s-2) var(--s-4); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft);
  border: 1px solid var(--line-2); transition: all var(--t-fast);
}
.tgp__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.tgp__btn--go { background: var(--azure); border-color: transparent; color: #fff; }
.tgp__btn--go:hover { background: var(--azure-bright); color: #fff; }

.tgp-enter-active { transition: opacity var(--t-base) var(--ease-silk); }
.tgp-enter-active .tgp { transition: transform var(--t-base) var(--ease-snap); }
.tgp-leave-active { transition: opacity var(--t-fast); }
.tgp-enter-from, .tgp-leave-to { opacity: 0; }
.tgp-enter-from .tgp { transform: translateY(10px) scale(.97); }
</style>
