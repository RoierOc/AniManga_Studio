<script setup>
/* Carpetas de la biblioteca de manga, repartidas en varios discos.
 *
 * La biblioteca era UNA carpeta hasta que el disco se llenó. Aquí se añaden más (cada una es un
 * PAR: originales + escalados, en el mismo disco) y se elige en cuál caen las obras nuevas.
 *
 * Lo importante, y lo que hay que dejar claro en la propia tarjeta: una obra sigue siendo UNA
 * aunque sus capítulos acaben repartidos entre discos — se lee, se exporta y se escala entera.
 * Ver `src/api/roots.py`.
 */
import { onMounted, ref } from 'vue'
import { useRootsStore } from '@/stores/roots'
import { formatBytes } from '@/lib/format'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'
import FolderPicker from '@/components/ui/FolderPicker.vue'

const roots = useRootsStore()
const nueva = ref('')
const abierto = ref(false)
const explorando = ref(false)

/* Se rellena con la ruta de WINDOWS (`D:\Manga`): es la que el usuario reconoce, y `roots.py`
 * la traduce con `wslpath`. La de Linux se guardaría igual, pero al leerla nadie sabría si
 * `/Manga_Upscaler_project/…` está en D: o dentro del disco virtual de WSL — que es C:. */
function elegida({ path, win }) {
  nueva.value = win || path
  abierto.value = true
}

async function añadir() {
  const v = nueva.value.trim()
  if (!v) return
  if (await roots.add(v)) { nueva.value = ''; abierto.value = false }
}

// % ocupado del disco: "libre" tiene que verse de un vistazo, no leyendo dos cifras.
function usado(r) {
  return r.total ? Math.round(100 * (r.total - r.free) / r.total) : 0
}

onMounted(() => roots.load(true))
</script>

<template>
  <section class="rts">
    <div class="rts__title">
      <Icon name="folder" :size="16" /> Carpetas de manga
      <span v-if="roots.list.length > 1" class="rts__n">{{ roots.list.length }} discos</span>
      <button class="rts__btn rts__btn--xs rts__add" :disabled="roots.busy" @click="abierto = !abierto"
              data-tip="Añadir carpeta"><Icon name="plus" :size="13" /></button>
    </div>

    <p class="rts__hint">
      Cada carpeta es un disco. Una obra puede acabar repartida entre varios y la app la sigue
      tratando como una sola: se lee, se escala y se exporta entera.
    </p>

    <div v-if="abierto" class="rts__new">
      <input v-model="nueva" type="text" class="rts__inp" placeholder="D:\Manga  ·  /mnt/d/Manga"
             @keydown.enter.prevent="añadir" />
      <button class="rts__btn" @click="explorando = true"><Icon name="folder" :size="14" /> Explorar</button>
      <button class="rts__btn rts__btn--go" :disabled="roots.busy || !nueva.trim()" @click="añadir">Añadir</button>
      <p class="rts__hint rts__hint--sub">
        Se crea también su carpeta de escalados al lado (<code>…_Upscaled</code>): el 4K nunca
        cruza de disco, así lo que hay en un disco viaja entero con él.
      </p>
    </div>

    <div v-if="!roots.loaded && !roots.list.length" class="rts__load">
      <Spinner :size="20" /> Leyendo carpetas…
    </div>

    <ul v-else class="rts__list">
      <li v-for="r in roots.list" :key="r.id" class="rts__row" :class="{ 'is-off': !r.online }">
        <span class="rts__ic" :class="{ 'is-active': r.id === roots.active }">
          <Icon :name="r.online ? 'folder' : 'alert'" :size="15" />
        </span>

        <div class="rts__body">
          <div class="rts__head">
            <b class="rts__label">{{ r.label }}</b>
            <span v-if="r.id === roots.active" class="rts__badge">Nuevas obras aquí</span>
            <!-- "El disco no está" no puede parecerse a "está vacío": se dice tal cual. -->
            <span v-if="!r.online" class="rts__badge rts__badge--off">Disco no disponible</span>
          </div>
          <span class="rts__path">{{ r.manga }}</span>
          <div v-if="r.total" class="rts__bar"><span class="rts__fill" :style="{ width: usado(r) + '%' }" /></div>
          <span v-if="r.total" class="rts__free">
            <b>{{ formatBytes(r.free) }} libres</b><em>&nbsp;de {{ formatBytes(r.total) }}</em>
          </span>
          <!-- Bajo WSL la raíz de Linux es un fichero dentro de una unidad de Windows: el
               espacio que canta el sistema de archivos puede ser enorme mientras la unidad que
               lo aloja está llena. Se dice el porqué, o el número parece un error nuestro. -->
          <span v-if="r.capped_by" class="rts__capped">
            <Icon name="alert" :size="11" />
            El disco virtual de WSL dice {{ formatBytes(r.fs_free) }}, pero vive dentro de
            {{ r.capped_by }} y ése es el techo real.
          </span>
        </div>

        <div class="rts__acts">
          <button v-if="r.id !== roots.active && r.online" class="rts__btn rts__btn--xs"
                  @click="roots.setActive(r.id)" data-tip="Las descargas nuevas caerán aquí">Usar</button>
          <button v-if="r.removable" class="rts__btn rts__btn--xs rts__btn--danger" :disabled="roots.busy"
                  @click="roots.remove(r.id)" data-tip="Quitar de la biblioteca (no borra nada)">
            <Icon name="close" :size="12" />
          </button>
        </div>
      </li>
    </ul>

    <FolderPicker v-model:open="explorando" title="Elegir carpeta de manga" @pick="elegida" />
  </section>
</template>

<style scoped>
.rts { display: flex; flex-direction: column; gap: var(--s-3); }
.rts__title { display: flex; align-items: center; gap: var(--s-2); font-size: var(--fs-md); font-weight: 600; color: var(--ink); }
.rts__title :deep(svg) { color: var(--azure); }
.rts__n { margin-left: auto; font-size: var(--fs-xs); color: var(--ink-faint); font-weight: 500; }
.rts__add { margin-left: var(--s-2); }
.rts__n + .rts__add { margin-left: 0; }

.rts__hint { font-size: var(--fs-xs); color: var(--ink-faint); }
.rts__hint--sub { flex-basis: 100%; }
.rts__hint code { font-family: var(--font-mono); font-size: var(--fs-2xs); padding: 0 var(--s-1);
  border-radius: var(--r-xs); background: var(--void); border: 1px solid var(--line); }

.rts__btn {
  display: inline-flex; align-items: center; gap: 0.375rem;
  padding: var(--s-2) var(--s-3); border-radius: var(--r-sm);
  font-size: var(--fs-sm); color: var(--ink-soft);
  border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0;
}
.rts__btn:hover:not(:disabled) { color: var(--ink); border-color: var(--line-strong); }
.rts__btn:disabled { opacity: .5; cursor: default; }
.rts__btn--xs { padding: 3px var(--s-2); font-size: var(--fs-xs); }
.rts__btn--go { background: var(--azure); border-color: transparent; color: #fff; font-weight: 600; }
.rts__btn--go:hover:not(:disabled) { background: var(--azure-bright); color: #fff; }
.rts__btn--danger { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 40%, transparent); }

.rts__new { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.rts__inp {
  flex: 1; min-width: 14rem; padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm); background: var(--void);
  border: 1px solid var(--line); color: var(--ink); font-size: var(--fs-sm);
}
.rts__inp:focus { outline: none; border-color: var(--azure); }

.rts__load { display: flex; align-items: center; gap: var(--s-2); color: var(--ink-faint); font-size: var(--fs-sm); }

.rts__list { display: flex; flex-direction: column; gap: var(--s-2); }
.rts__row {
  display: flex; align-items: flex-start; gap: var(--s-3);
  padding: var(--s-3); border-radius: var(--r-md);
  border: 1px solid var(--line-2); background: var(--surface-2);
}
.rts__row.is-off { opacity: .65; }

.rts__ic {
  width: 2rem; height: 2rem; flex-shrink: 0; display: grid; place-items: center;
  border-radius: var(--r-sm); color: var(--ink-faint); background: var(--surface);
}
.rts__ic.is-active { color: var(--azure-bright); background: var(--azure-haze); }

.rts__body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.rts__head { display: flex; align-items: center; gap: var(--s-2); flex-wrap: wrap; }
.rts__label { font-size: var(--fs-sm); }
.rts__badge {
  padding: 1px var(--s-2); border-radius: var(--r-pill);
  font-size: var(--fs-2xs); font-weight: 600; color: var(--azure-bright);
  background: var(--azure-haze); border: 1px solid color-mix(in srgb, var(--azure) 35%, transparent);
}
.rts__badge--off {
  color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent);
  border-color: color-mix(in srgb, var(--warn) 35%, transparent);
}
.rts__path {
  font-family: var(--font-mono); font-size: var(--fs-2xs); color: var(--ink-ghost);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rts__bar { height: 4px; border-radius: var(--r-pill); background: var(--void); overflow: hidden; margin-top: 2px; }
.rts__fill { display: block; height: 100%; background: var(--azure); }
.rts__free { font-size: var(--fs-2xs); color: var(--ink-faint); }
.rts__free em { font-style: normal; color: var(--ink-ghost); }
.rts__capped { display: flex; align-items: center; gap: 4px; font-size: var(--fs-2xs); color: var(--warn); }

.rts__acts { display: flex; align-items: center; gap: var(--s-2); flex-shrink: 0; }
</style>
