import { computed, ref, watch } from 'vue'

const ARROWS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'])

export function gridMoveIndex(index, key, length, columns = 1) {
  if (!Number.isInteger(index) || length < 1) return index
  const step = Math.max(1, Number(columns) || 1)
  if (key === 'ArrowLeft') return Math.max(0, index - 1)
  if (key === 'ArrowRight') return Math.min(length - 1, index + 1)
  if (key === 'ArrowUp') return Math.max(0, index - step)
  if (key === 'ArrowDown') return Math.min(length - 1, index + step)
  return index
}

function gridColumns(grid, items) {
  if (typeof getComputedStyle === 'function') {
    const template = getComputedStyle(grid).gridTemplateColumns
    const columns = template && template !== 'none' ? template.trim().split(/\s+/).filter(Boolean).length : 0
    if (columns) return columns
  }

  const firstTop = items[0]?.getBoundingClientRect?.().top
  if (firstTop == null) return 1
  const columns = items.findIndex((item) => Math.abs(item.getBoundingClientRect().top - firstTop) > 1)
  return columns > 0 ? columns : items.length || 1
}

export function useGridKeyboard(getVisibleKeys) {
  const selected = ref(new Set())
  const count = computed(() => selected.value.size)

  function clear() { selected.value = new Set() }

  function toggle(key) {
    const next = new Set(selected.value)
    const normalized = String(key)
    next.has(normalized) ? next.delete(normalized) : next.add(normalized)
    selected.value = next
  }

  function onKey(e) {
    const item = e.target?.closest?.('[data-grid-item]')
    if (!item || item !== e.target) return

    if (e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault()
      toggle(item.dataset.gridKey)
      return
    }
    if (e.key === 'Escape' && selected.value.size) {
      e.preventDefault()
      clear()
      return
    }
    if (!ARROWS.has(e.key)) return

    const grid = e.currentTarget
    const items = [...grid.querySelectorAll('[data-grid-item]')]
    const index = items.indexOf(item)
    if (index < 0) return
    const next = gridMoveIndex(index, e.key, items.length, gridColumns(grid, items))
    if (next === index) return
    e.preventDefault()
    items[next].focus()
    items[next].scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }

  watch(getVisibleKeys, (keys) => {
    const visible = new Set((keys || []).map(String))
    const next = new Set([...selected.value].filter((key) => visible.has(key)))
    if (next.size !== selected.value.size) selected.value = next
  }, { flush: 'post' })

  return { selected, count, clear, onKey }
}
