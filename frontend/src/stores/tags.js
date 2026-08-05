import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

/* Etiquetas propias del usuario sobre obras (manga, anime y Cine).
 *
 * Se cargan ENTERAS una vez: son cuatro cadenas por obra y el filtro necesita el conjunto de
 * todas las etiquetas existentes, no las de una obra suelta. Una petición por tarjeta sería
 * absurda para este volumen.
 *
 * `kind` separa los dominios porque sus identidades no son compatibles: un manga se identifica
 * por su carpeta, un anime por su id de AniList y una serie/película por `<kind>:<id>` de
 * Sonarr/Radarr. Ver `src/api/tags.py`.
 */
export const useTagsStore = defineStore('tags', {
  state: () => ({
    manga: {},                 // { [carpeta]: [etiquetas] }
    anime: {},                 // { [idAniList]: [etiquetas] }
    media: {},                 // { ['series:12'|'movie:3']: [etiquetas] }
    loaded: false,
    picker: null,              // { kind, id, title, tags: [] } mientras el diálogo está abierto
  }),

  getters: {
    // Etiquetas de UNA obra. Devuelve siempre un array para que la plantilla no compruebe nulos.
    forWork: (s) => (kind, id) => s[kind]?.[String(id)] || [],
    // Todas las etiquetas usadas en un dominio, para las sugerencias y los pills de filtro.
    universe: (s) => (kind) => [...new Set(Object.values(s[kind] || {}).flat())]
      .sort((a, b) => a.localeCompare(b, 'es')),
  },

  actions: {
    async load() {
      if (this.loaded) return
      try {
        const d = await api.get('/api/tags') || {}
        this.manga = d.manga || {}
        this.anime = d.anime || {}
        this.media = d.media || {}
        this.loaded = true
      } catch (_) {
        // Que no se hayan podido leer NO es que no haya ninguna: dejar `loaded` en false para
        // reintentar en la siguiente vista, en vez de fingir una biblioteca sin etiquetar.
      }
    },

    async setTags(kind, id, tags) {
      const ui = useUiStore()
      const key = String(id)
      const antes = this[kind][key]
      // Optimista: etiquetar es un gesto de un clic y esperar al servidor lo haría sentir lento.
      if (tags.length) this[kind][key] = [...tags]
      else delete this[kind][key]
      try {
        await api.post('/api/tags/set', { kind, id: key, tags })
      } catch (_) {
        if (antes) this[kind][key] = antes; else delete this[kind][key]
        ui.toast('No se pudieron guardar las etiquetas', 'error')
      }
    },

    openPicker(kind, id, title) {
      this.picker = { kind, id: String(id), title, tags: [...this.forWork(kind, id)] }
    },
    closePicker() { this.picker = null },
    async savePicker() {
      const p = this.picker
      if (!p) return
      this.picker = null
      await this.setTags(p.kind, p.id, p.tags)
    },
  },
})
