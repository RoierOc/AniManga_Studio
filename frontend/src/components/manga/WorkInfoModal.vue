<script setup>
/* Ficha de Obra (Fase 1) — informativa. Muestra los metadatos unificados del meta-source.
 * La lectura/versiones "mejor versión" inline llega en la Fase 2 (WorkDetail). Por ahora, si
 * la obra es legible, un CTA lleva a Explorar con el título precargado para leerla desde una
 * fuente; si es novela/other, se ofrecen enlaces externos de referencia. */
import { computed } from 'vue'
import { useDiscoveryStore } from '@/stores/discovery'
import { useMangaStore } from '@/stores/manga'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import NovelVersions from '@/components/manga/NovelVersions.vue'

const disco = useDiscoveryStore()
const w = computed(() => disco.work)
const inLibrary = computed(() => disco.inLibrary(w.value))
const adding = computed(() => disco.isAdding(w.value))

const TYPE_LABEL = { manga: 'Manga', manhwa: 'Manhwa', manhua: 'Manhua', novel: 'Novela', other: 'Otro' }
const STATUS_LABEL = { ongoing: 'En curso', completed: 'Completo', hiatus: 'En pausa', cancelled: 'Cancelado', unknown: '' }

// Sin `&w=` (ese parámetro del proxy clampa a 64px, era la causa de la mala calidad). La
// portada del CDN de MangaBaka ya viene dimensionada y nítida.
const cover = computed(() => imgProxy(w.value?.cover || ''))

// Enlaces externos de referencia a partir de los IDs cruzados que trae la Obra.
const externalLinks = computed(() => {
  const ids = w.value?.ids || {}
  const out = []
  if (ids.anilist) out.push({ label: 'AniList', url: `https://anilist.co/manga/${ids.anilist}` })
  if (ids.mal) out.push({ label: 'MyAnimeList', url: `https://myanimelist.net/manga/${ids.mal}` })
  if (ids.mangaupdates) out.push({ label: 'MangaUpdates', url: `https://www.mangaupdates.com/series/${ids.mangaupdates}` })
  return out
})

// Handoff a la ficha centralizada: abre el manga en el MangaModal sembrado con la identidad de
// la Obra, aterriza en Versiones y calcula la «mejor versión». Ahí se compara, lee y descarga.
function openCentralized() {
  const manga = useMangaStore()
  const work = w.value
  disco.closeWork()
  manga.openFromWork(work)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="wm">
      <div v-if="w" class="wm" @click.self="disco.closeWork()">
        <div class="wm__card">
          <button class="wm__x" @click="disco.closeWork()" aria-label="Cerrar"><Icon name="close" :size="18" /></button>

          <div class="wm__hero">
            <img v-if="cover" :src="cover" :alt="w.title" class="wm__cover" />
            <div v-else class="wm__cover wm__cover--empty"><Icon name="library" :size="28" /></div>
            <div class="wm__head">
              <div class="wm__badges">
                <span class="wm__type">{{ TYPE_LABEL[w.type] || 'Obra' }}</span>
                <span v-if="w.year" class="wm__meta">{{ w.year }}</span>
                <span v-if="STATUS_LABEL[w.status]" class="wm__meta">{{ STATUS_LABEL[w.status] }}</span>
                <span v-if="w.rating" class="wm__rating"><Icon name="spark" :size="11" /> {{ w.rating }}</span>
              </div>
              <h2 class="wm__title">{{ w.title }}</h2>
              <p v-if="w.title_native && w.title_native !== w.title" class="wm__native">{{ w.title_native }}</p>
              <p v-if="w.authors?.length" class="wm__authors">{{ w.authors.join(', ') }}</p>
            </div>
          </div>

          <div class="wm__body">
            <div v-if="disco.workLoading && w._partial" class="wm__loading"><Spinner :size="18" /> Cargando ficha…</div>

            <div v-if="w.genres?.length" class="wm__chips">
              <span v-for="g in w.genres.slice(0, 12)" :key="g" class="wm__chip">{{ g }}</span>
            </div>

            <p v-if="w.synopsis" class="wm__synopsis">{{ w.synopsis }}</p>
            <p v-else-if="!disco.workLoading" class="wm__synopsis wm__synopsis--empty">Sin sinopsis disponible.</p>

            <div class="wm__actions">
              <button v-if="w.readable" class="wm__cta" @click="openCentralized">
                <Icon name="spark" :size="16" /> Ver versiones y leer
              </button>
              <span v-else-if="w.type !== 'novel'" class="wm__note"><Icon name="library" :size="14" /> Ficha informativa (sin lectura en la app)</span>
              <button class="wm__add" :class="{ 'is-added': inLibrary }" :disabled="inLibrary || adding" @click="disco.addToLibrary(w)">
                <Icon :name="inLibrary ? 'check' : 'plus'" :size="15" />
                {{ inLibrary ? 'En biblioteca' : 'Añadir a biblioteca' }}
              </button>
              <a v-for="l in externalLinks" :key="l.url" class="wm__link" :href="l.url" target="_blank" rel="noopener">{{ l.label }} ↗</a>
            </div>

            <!-- Novelas: la lectura NO viene del meta-source (es un catálogo), sino de los
                 plugins de novelas. Se busca el título ahí y se elige versión. -->
            <NovelVersions v-if="w.type === 'novel'" :title="w.title" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.wm { position: fixed; inset: 0; z-index: 120; display: grid; place-items: center; padding: var(--s-4);
  background: rgba(5, 7, 13, .72); backdrop-filter: blur(8px); }
.wm__card { position: relative; width: min(46rem, 100%); max-height: 90vh; overflow-y: auto;
  background: var(--surface); border: 1px solid var(--line-2, var(--line)); border-radius: var(--r-lg, 18px);
  box-shadow: var(--shadow-lg); }
.wm__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 2; width: 34px; height: 34px;
  display: grid; place-items: center; border-radius: var(--r-sm); color: var(--ink-soft);
  background: rgba(7,10,18,.5); border: 1px solid var(--line); transition: all var(--t-fast); }
.wm__x:hover { color: var(--ink); background: var(--surface-2, var(--surface)); }

.wm__hero { display: flex; gap: var(--s-4); padding: var(--s-5); border-bottom: 1px solid var(--line); }
.wm__cover { width: 8.5rem; flex: none; aspect-ratio: 3 / 4.3; object-fit: cover; border-radius: var(--r-md);
  background: var(--surface-2, var(--void)); box-shadow: var(--shadow-md); }
.wm__cover--empty { display: grid; place-items: center; color: var(--ink-faint); border: 1px solid var(--line); }
.wm__head { min-width: 0; display: flex; flex-direction: column; justify-content: flex-end; }
.wm__badges { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); margin-bottom: var(--s-2); }
.wm__type { font-family: var(--font-mono); font-size: var(--fs-2xs); font-weight: 700; letter-spacing: .05em;
  text-transform: uppercase; color: var(--azure-bright); background: var(--azure-haze); padding: 3px 9px; border-radius: var(--r-pill); }
.wm__meta { font-size: var(--fs-xs); color: var(--ink-faint); }
.wm__rating { display: inline-flex; align-items: center; gap: 3px; font-family: var(--font-mono); font-size: var(--fs-2xs);
  font-weight: 700; color: var(--cyan); background: var(--cyan-glow, rgba(56,189,248,.12)); padding: 3px 8px; border-radius: var(--r-pill); }
.wm__title { font-family: var(--font-display); font-size: var(--fs-xl); font-weight: 700; line-height: var(--lh-tight); color: var(--ink); }
.wm__native { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: 2px; }
.wm__authors { color: var(--ink-faint); font-size: var(--fs-xs); margin-top: var(--s-2); }

.wm__body { padding: var(--s-5); display: flex; flex-direction: column; gap: var(--s-4); }
.wm__loading { display: flex; align-items: center; gap: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); }
.wm__chips { display: flex; flex-wrap: wrap; gap: var(--s-1); }
.wm__chip { font-size: var(--fs-2xs); color: var(--ink-soft); border: 1px solid var(--line); border-radius: var(--r-pill);
  padding: 3px 10px; text-transform: capitalize; }
.wm__synopsis { color: var(--ink-soft); font-size: var(--fs-sm); line-height: var(--lh-base, 1.6); white-space: pre-line; }
.wm__synopsis--empty { color: var(--ink-faint); font-style: italic; }

.wm__actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); }
.wm__cta { display: inline-flex; align-items: center; gap: var(--s-2); padding: var(--s-3) var(--s-5); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 650; color: #0b0f1a; background: var(--azure); box-shadow: var(--shadow-md);
  transition: transform var(--t-fast) var(--ease-silk), box-shadow var(--t-fast); }
.wm__cta:hover { transform: translateY(-1px); box-shadow: var(--glow-azure, var(--shadow-lg)); }
.wm__note { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-faint); font-size: var(--fs-sm); }
.wm__add { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-3) var(--s-4); border-radius: var(--r-md);
  font-size: var(--fs-sm); font-weight: 600; color: var(--ink); border: 1px solid var(--line-strong);
  transition: all var(--t-fast); }
.wm__add:hover:not(:disabled) { border-color: var(--azure); color: var(--azure-bright); }
.wm__add.is-added { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 45%, transparent);
  background: color-mix(in srgb, var(--jade) 12%, transparent); cursor: default; }
.wm__link { font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line);
  padding: var(--s-2) var(--s-3); border-radius: var(--r-md); transition: all var(--t-fast); }
.wm__link:hover { color: var(--ink); border-color: var(--line-strong); }
.wm__soon { display: flex; align-items: center; gap: 6px; font-size: var(--fs-2xs); color: var(--ink-faint);
  font-family: var(--font-mono); letter-spacing: .02em; }

.wm-enter-active, .wm-leave-active { transition: opacity var(--t-base); }
.wm-enter-from, .wm-leave-to { opacity: 0; }
.wm-enter-active .wm__card { transition: transform var(--t-base) var(--ease-silk); }
.wm-enter-from .wm__card { transform: translateY(14px) scale(.98); }
@media (max-width: 560px) { .wm__hero { flex-direction: column; } .wm__cover { width: 6.5rem; } }
@media (prefers-reduced-motion: reduce) { .wm-enter-active, .wm-leave-active, .wm-enter-active .wm__card { transition: none; } }
</style>
