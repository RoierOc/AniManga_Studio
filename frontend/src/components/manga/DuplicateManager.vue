<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '@/lib/api'
import { imgProxy } from '@/lib/img'
import { useModal } from '@/lib/useModal'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['update:open', 'open'])

const result = ref({ groups: [], count: 0, checked: { folders: 0, tracking: 0 } })
const loading = ref(false)
const error = ref('')
const modalEl = ref(null)

const groups = computed(() => result.value.groups || [])
const checkedLabel = computed(() => {
  const checked = result.value.checked || {}
  return `${checked.folders || 0} carpetas · ${checked.tracking || 0} seguimientos revisados`
})

function close() { emit('update:open', false) }

async function load() {
  loading.value = true
  error.value = ''
  try {
    result.value = await api.get('/api/library/duplicates')
  } catch (e) {
    error.value = e?.message || e?.body || 'No se pudo revisar la biblioteca.'
  } finally {
    loading.value = false
  }
}

watch(() => props.open, (isOpen) => { if (isOpen) load() })

function openRecord(record) {
  emit('open', record)
  close()
}

function recordMeta(record) {
  if (record.kind === 'folder') {
    const parts = [`${record.chapter_count || 0} caps.`, `${record.page_count || 0} pág.`]
    if (record.upscaled) parts.push(`${record.upscaled} en 4K`)
    return parts.join(' · ')
  }
  return [record.source_name || 'Seguimiento', record.status || 'Sin estado']
    .filter(Boolean).join(' · ')
}

function recordLabel(record) {
  return record.kind === 'folder' ? 'Carpeta local' : 'Entrada seguida'
}

useModal(() => props.open, close, modalEl)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="open" class="dupov" @click.self="close">
        <section ref="modalEl" class="dup" role="dialog" aria-modal="true" aria-labelledby="dup-title">
          <header class="dup__head">
            <div>
              <p class="dup__eyebrow"><span class="dup__tick" /> HIGIENE DE BIBLIOTECA</p>
              <h2 id="dup-title">Revisar duplicados</h2>
              <p class="dup__sub">{{ checkedLabel }}</p>
            </div>
            <div class="dup__actions">
              <button class="dup__icon" :disabled="loading" data-tip="Volver a revisar"
                      aria-label="Volver a revisar" @click="load">
                <Spinner v-if="loading" :size="15" /><Icon v-else name="refresh" :size="15" />
              </button>
              <button class="dup__icon" data-tip="Cerrar" aria-label="Cerrar" @click="close">
                <Icon name="close" :size="17" />
              </button>
            </div>
          </header>

          <div class="dup__body">
            <div v-if="loading" class="dup__loading"><Spinner :size="22" /> Revisando identidades locales…</div>
            <ErrorState v-else-if="error" title="No se pudo revisar la biblioteca." :detail="error" @retry="load" />
            <EmptyState v-else-if="!groups.length" icon="check"
                        title="No se detectaron duplicados."
                        hint="Se revisaron las carpetas y los seguimientos sin modificar nada." />
            <template v-else>
              <p class="dup__intro">
                {{ result.count }} {{ result.count === 1 ? 'grupo necesita' : 'grupos necesitan' }} revisión.
                Las coincidencias por título son orientativas; no se fusiona nada automáticamente.
              </p>

              <section v-for="group in groups" :key="group.id" class="dupgroup">
                <header class="dupgroup__head">
                  <div class="dupgroup__label">
                    <span class="dupgroup__badge" :class="`is-${group.confidence}`">
                      <Icon :name="group.confidence === 'exact' ? 'check' : 'alert'" :size="12" />
                      {{ group.confidence === 'exact' ? 'Identidad exacta' : 'Revisar' }}
                    </span>
                    <span class="dupgroup__scope">{{ group.scope === 'folders' ? 'Carpetas' : 'Seguimientos' }}</span>
                  </div>
                  <span class="dupgroup__reason">{{ group.reason }}</span>
                </header>

                <ul class="dupgroup__records">
                  <li v-for="record in group.records" :key="record.id" class="duprec">
                    <img v-if="record.cover" :src="imgProxy(record.cover, 48)" class="duprec__cover"
                         :alt="record.name" loading="lazy" decoding="async" />
                    <div v-else class="duprec__cover duprec__cover--empty" aria-hidden="true">
                      <Icon :name="record.kind === 'folder' ? 'folder' : 'heart'" :size="16" />
                    </div>
                    <div class="duprec__info">
                      <strong>{{ record.name }}</strong>
                      <span>{{ recordLabel(record) }} · {{ recordMeta(record) }}</span>
                      <span v-if="record.md_id || record.al_id" class="duprec__ids">
                        {{ record.md_id ? 'MD verificado' : `AniList ${record.al_id}` }}
                      </span>
                    </div>
                    <button class="duprec__open" data-tip="Abrir en la biblioteca" aria-label="Abrir en la biblioteca"
                            @click="openRecord(record)">
                      <Icon name="library" :size="14" /> Abrir
                    </button>
                  </li>
                </ul>
              </section>
            </template>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dupov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center;
  padding: var(--s-5); background: rgba(7, 10, 18, .74); backdrop-filter: blur(8px); }
.dup { position: relative; width: min(48rem, 100%); max-height: 88vh; display: flex; flex-direction: column;
  overflow: hidden; background: var(--glass-strong); border: .0625rem solid var(--line-2);
  border-radius: var(--r-lg); box-shadow: var(--shadow-xl); outline: none; }
.dup__head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s-4);
  padding: var(--s-5); border-bottom: .0625rem solid var(--line); }
.dup__eyebrow { display: flex; align-items: center; gap: var(--s-2); margin-bottom: var(--s-2);
  font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); }
.dup__tick { width: .875rem; height: .0625rem; background: var(--azure); box-shadow: 0 0 .5rem var(--azure-glow); }
.dup h2 { font-size: var(--fs-xl); line-height: var(--lh-tight); }
.dup__sub { margin-top: var(--s-1); color: var(--ink-faint); font-size: var(--fs-xs); }
.dup__actions { display: flex; gap: var(--s-2); }
.dup__icon { display: grid; place-items: center; width: 2.125rem; height: 2.125rem; border: .0625rem solid var(--line);
  border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); }
.dup__icon:hover:not(:disabled) { color: var(--azure-bright); border-color: var(--azure); }
.dup__icon:disabled { opacity: .55; }
.dup__body { min-height: 0; overflow-y: auto; padding: var(--s-4) var(--s-5) var(--s-5); }
.dup__loading { display: flex; align-items: center; justify-content: center; gap: var(--s-3); min-height: 12rem;
  color: var(--ink-faint); font-size: var(--fs-sm); }
.dup__intro { margin-bottom: var(--s-4); color: var(--ink-soft); font-size: var(--fs-sm); line-height: var(--lh-body); }
.dupgroup { overflow: hidden; margin-bottom: var(--s-3); border: .0625rem solid var(--line); border-radius: var(--r-md); background: color-mix(in srgb, var(--surface) 58%, transparent); }
.dupgroup:last-child { margin-bottom: 0; }
.dupgroup__head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--s-2);
  padding: var(--s-3) var(--s-4); border-bottom: .0625rem solid var(--line); }
.dupgroup__label { display: flex; align-items: center; gap: var(--s-2); }
.dupgroup__badge { display: inline-flex; align-items: center; gap: var(--s-1); padding: .1875rem .5rem;
  border-radius: var(--r-pill); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; }
.dupgroup__badge.is-exact { color: var(--jade); background: color-mix(in srgb, var(--jade) 12%, transparent); }
.dupgroup__badge.is-review { color: var(--amber, #fbbf24); background: color-mix(in srgb, var(--amber, #fbbf24) 12%, transparent); }
.dupgroup__scope, .dupgroup__reason { color: var(--ink-faint); font-size: var(--fs-xs); }
.dupgroup__reason { font-family: var(--font-mono); font-size: var(--fs-2xs); }
.dupgroup__records { display: flex; flex-direction: column; }
.duprec { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); }
.duprec + .duprec { border-top: .0625rem solid var(--line); }
.duprec__cover { width: 2.5rem; height: 3.5rem; flex: none; object-fit: cover; border-radius: var(--r-sm); background: var(--surface-2); }
.duprec__cover--empty { display: grid; place-items: center; color: var(--ink-ghost); border: .0625rem solid var(--line); }
.duprec__info { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: .125rem; }
.duprec__info strong { overflow: hidden; color: var(--ink); font-size: var(--fs-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.duprec__info span { overflow: hidden; color: var(--ink-faint); font-size: var(--fs-2xs); text-overflow: ellipsis; white-space: nowrap; }
.duprec__info .duprec__ids { color: var(--cyan); }
.duprec__open { display: inline-flex; align-items: center; gap: var(--s-1); flex: none; padding: var(--s-2) var(--s-3);
  border: .0625rem solid var(--line-2); border-radius: var(--r-sm); color: var(--ink-soft); font-size: var(--fs-xs); font-weight: 600; }
.duprec__open:hover { color: var(--azure-bright); border-color: var(--azure); background: var(--azure-haze); }
.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .dup { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .dup { transform: scale(.95) translateY(.75rem); }
@media (max-width: 40rem) {
  .dupov { padding: var(--s-3); }
  .dup__head, .dup__body { padding-left: var(--s-4); padding-right: var(--s-4); }
  .duprec { align-items: flex-start; flex-wrap: wrap; }
  .duprec__open { margin-left: auto; }
}
</style>
