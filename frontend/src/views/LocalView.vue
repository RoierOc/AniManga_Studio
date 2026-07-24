<script setup>
import { onMounted } from 'vue'
import { useCbzStore } from '@/stores/cbz'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useCbzStore()
onMounted(() => { if (!store.loaded) store.load() })
</script>

<template>
  <div class="loc">
    <header class="loc__head">
      <p class="eyebrow"><span class="tick" /> ARCHIVOS CBZ / CBR LOCALES</p>
      <h1>Local</h1>
    </header>

    <div v-if="store.loading" class="grid">
      <div v-for="n in 8" :key="n" class="skeleton" />
    </div>
    <EmptyState v-else-if="store.error || !store.items.length" icon="folder"
                :title="store.error || 'No hay archivos CBZ/CBR en tu carpeta de Mangas.'" />
    <div v-else class="grid">
      <article v-for="m in store.items" :key="m.title" class="lc" tabindex="0" @click="store.open(m)" @keydown.enter="store.open(m)">
        <div class="lc__poster">
          <img :src="store.cover(m.title)" :alt="m.title" loading="lazy" @error="$event.target.style.display='none'" class="lc__img" />
          <div class="lc__scrim" />
          <span class="lc__count">{{ m.volume_count }} vol.</span>
          <div class="lc__overlay"><h3 class="lc__title">{{ m.title }}</h3></div>
        </div>
      </article>
    </div>

    <!-- Volumes modal -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="store.current" class="ov" @click.self="store.close()">
          <div class="modal">
            <button class="modal__x" @click="store.close()"><Icon name="close" :size="18" /></button>
            <header class="modal__head">
              <img :src="store.cover(store.current.title)" class="modal__cover" :alt="store.current.title" @error="$event.target.style.display='none'" />
              <div>
                <h2 class="modal__title">{{ store.current.title }}</h2>
                <p class="modal__sub">{{ store.current.volume_count }} {{ store.current.volume_count === 1 ? 'volumen' : 'volúmenes' }}</p>
              </div>
            </header>
            <div class="modal__body">
              <div v-if="store.volumesLoading" class="center"><Spinner /></div>
              <ul v-else class="vols">
                <li v-for="v in store.volumes" :key="v.name" class="vol" @click="store.readVolume(v)">
                  <Icon name="library" :size="18" class="vol__ic" />
                  <div class="vol__info">
                    <span class="vol__name">{{ v.name }}</span>
                    <span class="vol__meta">{{ v.page_count }} pág. · {{ v.ext.replace('.', '').toUpperCase() }}</span>
                  </div>
                  <Icon name="play" :size="15" class="vol__play" />
                </li>
              </ul>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.loc { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.loc__head { padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 0.875rem; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(11.25rem, 1fr)); gap: var(--s-5); }
.skeleton { aspect-ratio: 2/3; border-radius: var(--r-md); background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); text-align: center; }

.lc { outline: none; cursor: pointer; transition: transform var(--t-base) var(--ease-snap); }
.lc:hover, .lc:focus-visible { transform: translateY(-6px); }
.lc__poster { position: relative; aspect-ratio: 2/3; border-radius: var(--r-md); overflow: hidden; background: var(--surface-2); border: 1px solid var(--line); transition: box-shadow var(--t-base), border-color var(--t-base); }
.lc:hover .lc__poster { border-color: var(--azure-glow); box-shadow: var(--shadow-lg), 0 0 0 1px var(--azure-glow); }
.lc__img { width: 100%; height: 100%; object-fit: cover; }
.lc__scrim { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 45%, rgba(5,7,13,.95) 100%); }
.lc__count { position: absolute; top: var(--s-2); left: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 600; padding: 2px 0.5rem; border-radius: var(--r-pill); background: rgba(7,10,18,.65); backdrop-filter: blur(6px); color: var(--ice); }
.lc__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: var(--s-3); }
.lc__title { font-size: var(--fs-sm); font-weight: 600; color: #fff; line-height: var(--lh-snug); text-shadow: 0 1px 6px rgba(0,0,0,.65); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(32.5rem, 100%); max-height: 86vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 2rem; height: 2rem; display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft); background: var(--surface); border: 1px solid var(--line); }
.modal__head { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); }
.modal__cover { width: 4.5rem; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-sm); }
.modal__title { font-size: var(--fs-lg); }
.modal__sub { color: var(--ink-faint); font-size: var(--fs-sm); margin-top: 2px; }
.modal__body { overflow-y: auto; padding: var(--s-3); }
.center { display: grid; place-items: center; padding: var(--s-6); }
.vols { display: flex; flex-direction: column; gap: 4px; }
.vol { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3); border-radius: var(--r-sm); cursor: pointer; transition: background var(--t-fast); }
.vol:hover { background: var(--surface); }
.vol:hover .vol__play { color: var(--azure-bright); transform: scale(1.15); }
.vol__ic { color: var(--ink-faint); }
.vol__info { flex: 1; min-width: 0; }
.vol__name { display: block; font-size: var(--fs-sm); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vol__meta { font-size: var(--fs-2xs); color: var(--ink-faint); }
.vol__play { color: var(--ink-faint); transition: all var(--t-fast) var(--ease-snap); }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 540px) { .loc { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(8.75rem, 1fr)); } }
</style>
