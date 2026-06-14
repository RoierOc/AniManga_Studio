import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

import './styles/tokens.css'
import './styles/base.css'

const app = createApp(App)
app.use(createPinia())

// Last-resort handler for errors not caught by a component boundary (event handlers,
// async). Logs instead of crashing silently; the in-app boundary (App.vue) shows UI.
app.config.errorHandler = (err, _instance, info) => {
  console.error('[app error]', info, err)
}

app.mount('#app')
