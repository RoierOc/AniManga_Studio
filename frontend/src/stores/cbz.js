import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'
import { useMangaStore } from './manga'

let progressTimer = null
let pendingProgress = null

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
        const saved = Number.isInteger(vol.progress_page) ? vol.progress_page : 0
        const page = vol.read ? 0 : Math.max(0, Math.min(saved, pages.length - 1))
        useMangaStore().openReaderRaw(this.current.title, pages,
          vol.name.replace(/\.[^.]+$/, ''), vol.name, page)
      } catch (_) { ui.toast('No se pudo abrir el volumen', 'error') }
    },

    queueProgress(manga, volume, page, pageCount, read) {
      if (pendingProgress?.manga === manga && pendingProgress?.volume === volume)
        read = read || pendingProgress.read
      pendingProgress = { manga, volume, page, page_count: pageCount, read: !!read }
      clearTimeout(progressTimer)
      progressTimer = setTimeout(() => this.flushProgress(), 800)
    },

    async flushProgress() {
      clearTimeout(progressTimer)
      progressTimer = null
      const progress = pendingProgress
      pendingProgress = null
      if (!progress) return
      try {
        await api.post('/api/cbz/progress', progress)
      } catch (_) {
        useUiStore().toast('No se pudo guardar el progreso del tomo', 'error')
      }
    },

  },
})
