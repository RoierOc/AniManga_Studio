import { describe, expect, it } from 'vitest'
import {
  TASK_NOTIFICATIONS_KEY,
  readTaskNotificationsEnabled,
  setTaskNotificationsEnabled,
  taskNotificationUpdate,
} from './taskNotifications'

describe('avisos de tareas', () => {
  it('avisa una sola vez al pasar de activa a completada', () => {
    const active = taskNotificationUpdate(new Map(), [
      { id: 'export-1', kind: 'export', status: 'running' },
    ], true, true)
    const done = taskNotificationUpdate(active.next, [
      { id: 'export-1', kind: 'export', status: 'done' },
    ], true, false)
    const repeated = taskNotificationUpdate(done.next, [
      { id: 'export-1', kind: 'export', status: 'done' },
    ], true, false)

    expect(done.notifications).toEqual([{ status: 'done', kind: 'export' }])
    expect(repeated.notifications).toEqual([])
  })

  it('avisa los errores, pero nunca una cancelación', () => {
    const active = taskNotificationUpdate(new Map(), [
      { id: 'up-1', kind: 'upscale', status: 'queued' },
      { id: 'dl-1', kind: 'download', status: 'running' },
    ], true, false)
    const ended = taskNotificationUpdate(active.next, [
      { id: 'up-1', kind: 'upscale', status: 'error' },
      { id: 'dl-1', kind: 'download', status: 'cancelled' },
    ], true, false)

    expect(ended.notifications).toEqual([{ status: 'error', kind: 'upscale' }])
  })

  it('no emite al estar desactivado ni repite el fallo cuando se activa después', () => {
    const active = taskNotificationUpdate(new Map(), [
      { id: 'sub-1', kind: 'subtitle', status: 'running' },
    ], false, false)
    const ended = taskNotificationUpdate(active.next, [
      { id: 'sub-1', kind: 'subtitle', status: 'error' },
    ], false, false)
    const enabledLater = taskNotificationUpdate(ended.next, [
      { id: 'sub-1', kind: 'subtitle', status: 'error' },
    ], true, false)

    expect(ended.notifications).toEqual([])
    expect(enabledLater.notifications).toEqual([])
  })

  it('suprime los avisos mientras la app tiene el foco', () => {
    const active = taskNotificationUpdate(new Map(), [
      { id: 'dl-1', kind: 'download', status: 'running' },
    ], true, true)
    expect(taskNotificationUpdate(active.next, [
      { id: 'dl-1', kind: 'download', status: 'done' },
    ], true, true).notifications).toEqual([])
  })

  it('la preferencia es opt-in y se puede persistir', () => {
    const values = new Map()
    const storage = {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
    }
    expect(readTaskNotificationsEnabled(storage)).toBe(false)
    setTaskNotificationsEnabled(true, storage)
    expect(values.get(TASK_NOTIFICATIONS_KEY)).toBe('true')
    expect(readTaskNotificationsEnabled(storage)).toBe(true)
  })
})
