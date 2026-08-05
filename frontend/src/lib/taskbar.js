/* Progreso dentro del icono de la barra de tareas de Windows (ITaskbarList3).
 *
 * Escalar un capítulo a 4K o bajar una temporada son tareas de minutos u horas. Hasta ahora la
 * única forma de saber cómo iban era traer la ventana al frente y mirar el Centro de Actividad.
 * Esto lo pinta en el icono: es de las pocas cosas que una app de escritorio puede hacer y una
 * web no, y por eso se nota como «nativo».
 *
 * No inventa estado: lee `activeCount` y `aggregatePct`, que ya son los que alimentan el anillo
 * del indicador de actividad. Una sola verdad.
 */
import { watch } from 'vue'
import { isNative, send } from './nativeBridge'

/** Traduce (nº de tareas, %) al par (estado, valor) que entiende el shell. */
export function estadoBarra(activas, pct) {
  if (!activas) return { state: 'none', value: 0 }
  // Una tarea sin porcentaje todavía (recién encolada, o sin total conocido) se pinta como
  // barra indeterminada. Mostrar 0 % parecería atascada, que es peor que no decir el número.
  if (!pct) return { state: 'indeterminate', value: 0 }
  return { state: 'normal', value: Math.min(100, Math.round(pct)) }
}

/* Se llama una vez desde App.vue. Fuera de la shell nativa no hace absolutamente nada — ni
 * siquiera monta el watcher. */
export function useTaskbarProgress(store) {
  if (!isNative()) return

  let ultimo = ''
  watch(
    () => [store.activeCount, store.aggregatePct],
    ([activas, pct]) => {
      const { state, value } = estadoBarra(activas, pct)
      // El SSE late cada 500 ms; sin esta guarda se cruzaría el puente IPC decenas de veces por
      // minuto para repintar exactamente el mismo píxel.
      const firma = `${state}:${value}`
      if (firma === ultimo) return
      ultimo = firma
      send('taskbar', { state, value })
    },
    { immediate: true },
  )
}
