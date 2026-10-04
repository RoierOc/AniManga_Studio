import { defineStore } from 'pinia'
import { api } from '@/lib/api'
import { useUiStore } from './ui'

/* Hornear anime con Anime4K — la mitad de cliente de `api/anime_upscale.py`.
 *
 * Va en su propio store para mantener el procesamiento separado de la biblioteca de anime.
 *
 * El sondeo se enciende SOLO mientras hay algo en marcha. Un `setInterval` permanente para una
 * cosa que se usa cuatro veces al mes es una petición cada 3 s durante toda la sesión.
 */

let sonda = null

export const useAnimeUpscaleStore = defineStore('animeUpscale', {
  state: () => ({
    disponible: true,
    motivo: '',
    actual: null,        // { id, nombre, estado, porcentaje, quedan, video, salida }
    cola: [],
    hechos: [],
    // Las calidades y sus tiempos MEDIDOS los manda el backend: aquí no se hardcodean, así que
    // añadir un preset no obliga a tocar el frontend.
    calidades: [],
    porDefecto: 'maxima',
  }),

  getters: {
    // ¿Este episodio concreto está en la cola o en el horno? Lo pinta la tarjeta.
    trabajoDe: (s) => (path) => {
      if (!path) return null
      const todos = [s.actual, ...s.cola].filter(Boolean)
      return todos.find((t) => t.video === path || t.salida === path) || null
    },
    hayAlgo: (s) => !!s.actual || s.cola.length > 0,

    // Minutos aproximados de horno para ESTE episodio. Si conocemos su duración se usa; si no, se
    // supone un episodio de 24 min, que es lo que dura casi todo lo que hay en la biblioteca.
    // Redondeado a entero: dar «6,8 min» finge una precisión que no existe, porque el tiempo real
    // depende de si la GPU está libre.
    minutos: () => (calidad, duracionSeg) => {
      const min = (duracionSeg && duracionSeg > 60) ? duracionSeg / 60 : 24
      return Math.max(1, Math.round(min * (calidad.min_por_min || 0)))
    },
  },

  actions: {
    async refrescar() {
      try {
        const d = await api.get('/api/anime/upscale/status')
        // `por_defecto` llega en snake_case; el resto casa por nombre.
        this.$patch({ ...d, porDefecto: d.por_defecto || this.porDefecto })
        if (this.hayAlgo) this.sondear()
        else this.parar()
      } catch (_) { this.parar() }
    },

    sondear() {
      if (sonda) return
      sonda = setInterval(async () => {
        try {
          const d = await api.get('/api/anime/upscale/status')
          const antes = this.actual?.id
          this.$patch(d)
          // Al terminar uno, avisar y refrescar la lista: el episodio ahora apunta al horneado.
          if (antes && this.actual?.id !== antes) {
            const t = this.hechos.find((h) => h.id === antes)
            const ui = useUiStore()
            if (t?.estado === 'hecho') ui.toast(`Escalado listo: ${t.nombre}`, 'success')
            else if (t?.estado === 'error') ui.toast(`Falló el escalado: ${t.error || ''}`, 'error')
          }
          if (!this.hayAlgo) this.parar()
        } catch (_) { this.parar() }
      }, 3000)
    },

    parar() {
      if (sonda) { clearInterval(sonda); sonda = null }
    },

    async hornear(path, calidad) {
      const ui = useUiStore()
      try {
        await api.post('/api/anime/upscale/start', { path, calidad })
        const c = this.calidades.find((x) => x.id === calidad)
        ui.toast(`En cola para escalar · ${c?.etiqueta || 'Anime4K'}`, 'success')
        await this.refrescar()
      } catch (e) { ui.toast(e?.message || 'No se pudo encolar', 'error') }
    },

    async cancelar(id) {
      try { await api.post('/api/anime/upscale/cancel', { id }) } catch (_) { /* ya no estaba */ }
      await this.refrescar()
    },

    async descartar(path) {
      const ui = useUiStore()
      try {
        await api.post('/api/anime/upscale/discard', { path })
        ui.toast('Versión escalada borrada; vuelve el original', 'success')
      } catch (e) { ui.toast(e?.message || 'No se pudo borrar', 'error') }
    },
  },
})
