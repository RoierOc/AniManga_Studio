<script setup>
import { useUiStore } from '@/stores/ui'
import Icon from './Icon.vue'
const ui = useUiStore()
</script>

<template>
  <Teleport to="body">
    <div class="toaster">
      <TransitionGroup name="toast">
        <div v-for="t in ui.toasts" :key="t.id" class="toast" :class="`toast--${t.type}`">
          <span class="toast__bar" />
          <span class="toast__msg">{{ t.message }}</span>
          <button v-if="t.action" class="toast__action" @click="ui.runToastAction(t.id)">{{ t.action.label }}</button>
          <button class="toast__x" @click="ui.dismissToast(t.id)"><Icon name="close" :size="14" /></button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toaster {
  position: fixed; right: var(--s-5); bottom: var(--s-5);
  z-index: var(--z-toast);
  display: flex; flex-direction: column; gap: var(--s-2);
  max-width: 360px;
}
.toast {
  position: relative;
  display: flex; align-items: center; gap: var(--s-3);
  padding: var(--s-3) var(--s-4) var(--s-3) var(--s-5);
  background: var(--glass-strong);
  backdrop-filter: blur(16px);
  border: 1px solid var(--line-2);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-lg);
  font-size: var(--fs-sm);
  overflow: hidden;
}
.toast__bar { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--azure); }
.toast--ok    .toast__bar { background: var(--ok); }
.toast--warn  .toast__bar { background: var(--warn); }
.toast--error .toast__bar { background: var(--danger); }
.toast__msg { flex: 1; color: var(--ink-soft); }
.toast__action { flex-shrink: 0; padding: 4px 12px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 700; color: var(--azure-bright); border: 1px solid color-mix(in srgb, var(--azure) 35%, transparent); transition: all var(--t-fast); }
.toast__action:hover { background: var(--azure-haze); color: #fff; border-color: var(--azure); }
.toast__x { color: var(--ink-faint); display: grid; place-items: center; transition: color var(--t-fast); }
.toast__x:hover { color: var(--ink); }

.toast-enter-active { transition: all var(--t-base) var(--ease-snap); }
.toast-leave-active { transition: all var(--t-fast) var(--ease-silk); }
.toast-enter-from { opacity: 0; transform: translateX(40px); }
.toast-leave-to   { opacity: 0; transform: translateX(40px); }
</style>
