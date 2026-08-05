import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

/* Carpetas (discos) donde vive la biblioteca de manga.
 *
 * La biblioteca era UNA carpeta hasta que el disco se llenó. Ahora son varias, y la regla
 * importante es que una obra sigue siendo UNA aunque sus capítulos estén repartidos: el backend
 * las funde (`src/api/roots.py`) y aquí sólo se elige DÓNDE cae lo nuevo.
 *
 * `pick` es lo que se manda como `root` al descargar/escalar. Vacío = decide el backend (el disco
 * donde ya vive la obra, o el activo si es nueva), que es lo que quiere el 99% de las veces.
 */
export const useRootsStore = defineStore('roots', {
  state: () => ({
    list: [],        // [{id,label,manga,upscaled,free,total,online,active,removable}]
    active: 'base',
    pick: '',        // destino elegido a mano para la próxima descarga/escalado
    loaded: false,
    busy: false,
  }),

  getters: {
    // Sólo hay algo que elegir si el usuario añadió un segundo disco.
    multi: (s) => s.list.length > 1,
    byId: (s) => (id) => s.list.find(r => r.id === id) || null,
    // Lo que se manda al backend: '' si no hay nada que elegir.
    target: (s) => (s.list.length > 1 ? s.pick : ''),
  },

  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      try {
        const d = await api.get('/api/roots')
        this.list = d.roots || []
        this.active = d.active || 'base'
        this.loaded = true
        // Si la carpeta elegida ya no está (se quitó), volver a "automático" en vez de
        // seguir mandando un id muerto que el backend ignoraría en silencio.
        if (this.pick && !this.byId(this.pick)) this.pick = ''
      } catch (_) {
        // Que no se hayan podido leer no es que no haya ninguna: se reintenta.
      }
    },

    async add(manga, label = '') {
      const ui = useUiStore()
      this.busy = true
      try {
        await api.post('/api/roots/add', { manga, label })
        await this.load(true)
        ui.toast('Carpeta añadida', 'success')
        return true
      } catch (e) {
        ui.toast(e?.message || 'No se pudo añadir la carpeta', 'error')
        return false
      } finally { this.busy = false }
    },

    async remove(id) {
      const ui = useUiStore()
      const ok = await ui.confirm({
        title: '¿Quitar esta carpeta?',
        body: 'Deja de mirarse desde la app. No se borra nada del disco: lo que hay ahí sigue ahí.',
      })
      if (!ok) return
      this.busy = true
      try {
        await api.post('/api/roots/remove', { id })
        await this.load(true)
      } catch (e) {
        ui.toast(e?.message || 'No se pudo quitar', 'error')
      } finally { this.busy = false }
    },

    async setActive(id) {
      const prev = this.active
      this.active = id
      try { await api.post('/api/roots/active', { id }) }
      catch (_) { this.active = prev; useUiStore().toast('No se pudo cambiar', 'error') }
    },

    // En qué discos vive una obra concreta (y cuántas páginas hay en cada uno).
    async where(title) {
      try { return await api.get(`/api/roots/where/${encodeURIComponent(title)}`) }
      catch (_) { return null }
    },
  },
})
