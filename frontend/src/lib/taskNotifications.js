export const TASK_NOTIFICATIONS_KEY = 'anime-task-notifications'

const ACTIVE = new Set(['queued', 'running'])
const TERMINAL = new Set(['done', 'error', 'cancelled'])
const KINDS = new Set([
  'download', 'upscale', 'export', 'translate', 'subtitle', 'anime_upscale',
  'subtitle_batch', 'versiondl',
])

function storageOrBrowser(storage) {
  if (storage) return storage
  try { return globalThis.localStorage } catch { return null }
}

export function readTaskNotificationsEnabled(storage) {
  try { return storageOrBrowser(storage)?.getItem(TASK_NOTIFICATIONS_KEY) === 'true' } catch { return false }
}

export function setTaskNotificationsEnabled(enabled, storage) {
  try { storageOrBrowser(storage)?.setItem(TASK_NOTIFICATIONS_KEY, enabled ? 'true' : 'false') } catch {}
}

// Keep only active IDs: initial terminal snapshots never replay, and each transition can notify once.
export function taskNotificationUpdate(previous, tasks, enabled, foreground) {
  const next = new Map()
  const notifications = []

  for (const task of tasks || []) {
    if (!task?.id || (!TERMINAL.has(task.status) && !ACTIVE.has(task.status))) continue
    const kind = KINDS.has(task.kind) ? task.kind : 'job'
    const key = `${kind}:${task.id}`

    if (ACTIVE.has(task.status)) {
      next.set(key, task.status)
      continue
    }

    if (previous?.has(key) && task.status !== 'cancelled' && enabled && !foreground)
      notifications.push({ status: task.status, kind })
  }

  return { next, notifications }
}
