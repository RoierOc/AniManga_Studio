import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { hydratePrefs, startPrefSync } from '@/lib/prefs'
import { reloadDensity } from '@/lib/density'
import { installTooltips } from '@/lib/tooltip'

import './styles/tokens.css'
import './styles/base.css'

const app = createApp(App)
app.use(createPinia())

// Last-resort handler for errors not caught by a component boundary (event handlers,
// async). Logs instead of crashing silently; the in-app boundary (App.vue) shows UI.
app.config.errorHandler = (err, _instance, info) => {
  console.error('[app error]', info, err)
}

// Seed portable UI prefs from the backend (synced profile) before mounting so
// components read the restored values; then keep them mirrored back.
hydratePrefs().finally(() => {
  reloadDensity()   // `grid-density` viaja en el perfil: pintarla ANTES del primer render
  app.mount('#app')
  installTooltips()   // tooltips propios: sustituyen al `title` del SO (lib/tooltip.js)
  startPrefSync()
})
