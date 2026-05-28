import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useMangaStore } from './manga'

export const useCbzStore = defineStore('cbz', {
  state: () => ({
    items: [],            // [{title, volume_count, first_volume}]
    loading: false,
    loaded: false,
    error: '',
    current: null,        // manga whose volumes modal is open
    volumes: [],          // [{name, page_count, ext}]
    volumesLoading: false,
  }),

  actions: {
    cover: (title) => `/api/cbz/cover?manga=${encodeURIComponent(title)}`,

    async load() {
      this.loading = true; this.error = ''
      try {
        const d = await api.get('/api/cbz/list')
        if (Array.isArray(d)) this.items = d
        else { this.items = []; this.error = d?.error || '' }
      } catch (_) { this.items = []; this.error = 'No se pudo leer la carpeta de CBZ.' }
      finally { this.loading = false; this.loaded = true }
    },

    async open(manga) {
      this.current = manga
      this.volumes = []
      this.volumesLoading = true
      try { this.volumes = await api.get(`/api/cbz/volumes?manga=${encodeURIComponent(manga.title)}`) || [] }
      catch (_) { useUiStore().toast('No se pudieron leer los volúmenes', 'error') }
      finally { this.volumesLoading = false }
    },
    close() { this.current = null },

    async readVolume(vol) {
      const ui = useUiStore()
      try {
        const d = await api.get(`/api/cbz/pages?manga=${encodeURIComponent(this.current.title)}&volume=${encodeURIComponent(vol.name)}`)
        const pages = d.pages || []
        if (!pages.length) { ui.toast('Volumen vacío', 'error'); return }
        useMangaStore().openReaderRaw(this.current.title, pages, vol.name.replace(/\.[^.]+$/, ''))
      } catch (_) { ui.toast('No se pudo abrir el volumen', 'error') }
    },
  },
})
