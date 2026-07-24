<script setup>
/* Diálogo de confirmación propio — sustituye al confirm() del navegador/SO, que en la shell
 * nativa sin marco abría un cuadro gris de Windows encima de la estética justo en los momentos
 * destructivos. Se abre con `ui.confirm({...}) → Promise<boolean>` (ver stores/ui.js). */
import { computed, watch, nextTick, ref } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useModal } from '@/lib/useModal'
import Icon from '@/components/ui/Icon.vue'

const ui = useUiStore()
const d = computed(() => ui.confirmDlg)
const paras = computed(() => (d.value?.body || '').split('\n').filter(Boolean))
const okBtn = ref(null)

// Foco al botón de confirmar al abrir → Enter confirma, Esc cancela, sin tocar el ratón.
watch(d, async (v) => { if (v) { await nextTick(); okBtn.value?.focus() } })
// Escape + trampa de foco compartidas. Va encima de cualquier otro modal en la pila, así que
// Escape lo cierra a ÉL y no la ficha que lo abrió.
const dlgEl = ref(null)
useModal(() => !!d.value, () => ui.resolveConfirm(false), dlgEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="cdlg">
      <div v-if="d" class="cdlg-ov" @click.self="ui.resolveConfirm(false)">
        <div ref="dlgEl" class="cdlg" role="alertdialog" aria-modal="true" :aria-label="d.title">
          <div class="cdlg__glyph" :class="{ 'is-danger': d.danger }">
            <Icon :name="d.danger ? 'trash' : 'spark'" :size="20" />
          </div>
          <h3 class="cdlg__title">{{ d.title }}</h3>
          <p v-for="(p, i) in paras" :key="i" class="cdlg__body">{{ p }}</p>
          <div class="cdlg__btns">
            <button class="cdlg__btn" @click="ui.resolveConfirm(false)">{{ d.cancelLabel }}</button>
            <button ref="okBtn" class="cdlg__btn cdlg__btn--go" :class="{ 'is-danger': d.danger }"
                    @click="ui.resolveConfirm(true)">{{ d.confirmLabel }}</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cdlg-ov {
  position: fixed; inset: 0; z-index: calc(var(--z-toast) + 1);
  display: grid; place-items: center; padding: var(--s-5);
  background: rgba(5, 7, 13, 0.6); backdrop-filter: blur(4px);
}
.cdlg {
  width: min(26rem, 92vw); padding: var(--s-5);
  border-radius: var(--r-lg); background: var(--glass-strong); backdrop-filter: blur(18px);
  border: 1px solid var(--line-2); box-shadow: var(--shadow-xl);
  display: flex; flex-direction: column; gap: var(--s-2);
}
.cdlg__glyph {
  width: 2.75rem; height: 2.75rem; display: grid; place-items: center; border-radius: var(--r-md);
  color: var(--azure-bright); background: var(--azure-haze);
  border: 1px solid color-mix(in srgb, var(--azure) 35%, transparent); margin-bottom: var(--s-1);
}
.cdlg__glyph.is-danger {
  color: var(--coral); background: color-mix(in srgb, var(--coral) 12%, transparent);
  border-color: color-mix(in srgb, var(--coral) 35%, transparent);
}
.cdlg__title { font-family: var(--font-display); font-size: var(--fs-lg); font-weight: 600; }
.cdlg__body { font-size: var(--fs-sm); color: var(--ink-soft); line-height: var(--lh-snug); }
.cdlg__btns { display: flex; justify-content: flex-end; gap: var(--s-2); margin-top: var(--s-3); }
.cdlg__btn {
  padding: var(--s-2) var(--s-4); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft);
  border: 1px solid var(--line-2); transition: all var(--t-fast);
}
.cdlg__btn:hover { color: var(--ink); border-color: var(--line-strong); }
.cdlg__btn--go { background: var(--azure); border-color: transparent; color: #fff; }
.cdlg__btn--go:hover { background: var(--azure-bright); color: #fff; }
.cdlg__btn--go.is-danger { background: var(--coral); }
.cdlg__btn--go.is-danger:hover { background: color-mix(in srgb, var(--coral) 85%, #fff); }

.cdlg-enter-active { transition: opacity var(--t-base) var(--ease-silk); }
.cdlg-enter-active .cdlg { transition: transform var(--t-base) var(--ease-snap); }
.cdlg-leave-active { transition: opacity var(--t-fast); }
.cdlg-enter-from, .cdlg-leave-to { opacity: 0; }
.cdlg-enter-from .cdlg { transform: translateY(10px) scale(.97); }
</style>
