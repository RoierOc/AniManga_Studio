<script setup>
/* "Buscar para leer" de NOVELAS — equivalente al "Ver versiones" del manga.
 * Busca el título en las fuentes curadas (plugins LNReader vía /api/novels/find) y deja
 * elegir de cuál leerla. Al añadir, la novela entra en la biblioteca con su origen
 * (pluginId+path), que es lo que el lector de texto necesita después. */
import { computed } from 'vue'
import { useNovelsStore } from '@/stores/novels'
import { imgProxy } from '@/lib/img'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({ title: { type: String, required: true } })
const novels = useNovelsStore()

const searched = computed(() => novels.findQuery === props.title && !novels.finding)
const results = computed(() => (novels.findQuery === props.title ? novels.versions : []))
const failed = computed(() => (searched.value ? novels.failedSources : []))
// Con qué títulos se buscó (el original + sus variantes): explica un cero sin resultados.
const tried = computed(() => (searched.value ? novels.tried : []))

// Marca en ámbar las versiones muy por debajo de la más completa: casi siempre es una
// traducción parada a medias, no otra edición.
function isShort(v) {
  const c = novels.chapterCount(v)
  const max = Math.max(...results.value.map(x => novels.chapterCount(x) || 0))
  return c != null && max > 0 && c < max * 0.6
}

// El idioma es lo primero que se mira al elegir versión: se muestra como píldora, no enterrado.
const LANG = { 'Español': 'ES', English: 'EN', 'Français': 'FR', 'Português': 'PT' }
function langShort(l) { return LANG[l] || (l || '').slice(0, 2).toUpperCase() }

function search() {
  novels.loadLibrary()
  novels.findVersions(props.title)
}
</script>

<template>
  <div class="nv">
    <button v-if="!searched && !novels.finding" class="nv__cta" @click="search">
      <Icon name="search" :size="16" /> Buscar para leer
    </button>

    <div v-if="novels.finding" class="nv__loading">
      <Spinner :size="18" /> Buscando en fuentes de novelas…
    </div>

    <template v-if="searched">
      <p v-if="!results.length" class="nv__empty">
        Ninguna de las fuentes consultadas tiene esta novela.
        <span v-if="tried.length > 1" class="nv__tried">Se probó como: {{ tried.join(' · ') }}.</span>
        Las novelas licenciadas casi nunca están en fuentes abiertas; puedes añadir más fuentes en
        Ajustes → Novelas.
      </p>

      <ul v-else class="nv__list">
        <li v-for="v in results" :key="v.pluginId + v.path" class="nv__item">
          <img v-if="v.cover" :src="imgProxy(v.cover, 160)" :alt="v.name" class="nv__cover" />
          <div v-else class="nv__cover nv__cover--empty"><Icon name="library" :size="16" /></div>
          <div class="nv__info">
            <span class="nv__name">{{ v.name }}</span>
            <span class="nv__src">
              <span class="nv__lang" :class="{ 'is-es': (v.lang || '').startsWith('Espa') }">{{ langShort(v.lang) }}</span>
              {{ v.sourceName || v.pluginId }}
              <!-- Nº de capítulos: sin esto se elige a ciegas (una versión puede tener 23 y otra 285) -->
              <template v-if="novels.chapterCount(v) != null">
                · <b :class="{ 'is-short': isShort(v) }">{{ novels.chapterCount(v) }} cap.</b>
              </template>
              <template v-else>· <span class="nv__dim">contando…</span></template>
            </span>
          </div>
          <button class="nv__read" data-tip="Ver capítulos y leer" @click="novels.openDetail({ title, cover: v.cover, novel: v })">
            <Icon name="book" :size="14" /> Abrir
          </button>
          <button class="nv__add" :class="{ 'is-added': novels.inLibrary(v) }"
                  :disabled="novels.inLibrary(v) || novels.isAdding(v)"
                  @click="novels.addToLibrary(v, title)">
            <Icon :name="novels.inLibrary(v) ? 'check' : 'plus'" :size="14" />
            {{ novels.inLibrary(v) ? 'Añadida' : 'Añadir' }}
          </button>
        </li>
      </ul>

      <!-- "falló ≠ no había": si una fuente no respondió, se dice — no se hace pasar por vacío. -->
      <p v-if="failed.length" class="nv__note">
        <Icon name="alert" :size="12" /> Sin respuesta de: {{ failed.join(', ') }}
      </p>
      <button class="nv__again" @click="search">Buscar de nuevo</button>
    </template>
  </div>
</template>

<style scoped>
.nv { display: flex; flex-direction: column; gap: var(--s-3); }
.nv__cta, .nv__again { display: inline-flex; align-items: center; gap: var(--s-2); align-self: flex-start;
  padding: var(--s-2) var(--s-4); border-radius: var(--r-sm); font-size: var(--fs-sm); font-weight: 600;
  color: var(--azure-bright); background: var(--azure-haze); border: 1px solid var(--line);
  transition: all var(--t-fast); }
.nv__cta:hover, .nv__again:hover { background: var(--surface-2, var(--surface)); color: var(--ink); }
.nv__again { font-size: var(--fs-xs); font-weight: 500; color: var(--ink-faint); background: none; }

.nv__loading { display: flex; align-items: center; gap: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); }
.nv__empty { color: var(--ink-faint); font-size: var(--fs-sm); }

.nv__list { display: flex; flex-direction: column; gap: var(--s-2); }
.nv__item { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2);
  border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface-2, transparent); }
.nv__cover { width: 2.6rem; flex: none; aspect-ratio: 3 / 4; object-fit: cover; border-radius: var(--r-sm); }
.nv__cover--empty { display: grid; place-items: center; color: var(--ink-faint); border: 1px solid var(--line); }
.nv__info { min-width: 0; flex: 1; display: flex; flex-direction: column; }
.nv__name { font-size: var(--fs-sm); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nv__src { font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-faint); }
.nv__add { display: inline-flex; align-items: center; gap: 4px; flex: none; padding: 0.3125rem var(--s-3);
  border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright);
  background: var(--azure-haze); border: 1px solid var(--line); transition: all var(--t-fast); }
.nv__add:hover:not(:disabled) { color: var(--ink); }
.nv__add.is-added { color: var(--ok, #4ade80); }
.nv__add:disabled { cursor: default; opacity: .8; }

.nv__read { display: inline-flex; align-items: center; gap: 4px; flex: none; padding: 0.3125rem var(--s-3);
  border-radius: var(--r-pill); font-size: var(--fs-2xs); font-weight: 600; color: var(--ink-soft);
  border: 1px solid var(--line); transition: all var(--t-fast); }
.nv__read:hover { color: var(--azure-bright); background: var(--azure-haze); }

.nv__lang { display: inline-block; padding: 1px 0.3125rem; margin-right: 4px; border-radius: var(--r-xs, 3px);
  font-size: var(--fs-2xs); font-weight: 700; color: var(--ink-soft); background: var(--surface-2, var(--void));
  border: 1px solid var(--line); }
.nv__lang.is-es { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 35%, transparent); }

.nv__tried { display: block; margin: 3px 0; font-family: var(--font-mono); font-size: var(--fs-2xs); opacity: .8; }
.nv__src b { color: var(--ink-soft); font-weight: 600; }
.nv__src b.is-short { color: var(--warn, #fbbf24); }
.nv__dim { opacity: .5; }

.nv__note { display: inline-flex; align-items: center; gap: 0.3125rem; font-size: var(--fs-2xs); color: var(--ink-faint); }
</style>
