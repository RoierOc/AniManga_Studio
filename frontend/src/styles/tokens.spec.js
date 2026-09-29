import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')
const hex = (name) => css.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'))?.[1]

function luminance(color) {
  const channels = color.slice(1).match(/../g).map(x => parseInt(x, 16) / 255)
    .map(x => x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
}

describe('contraste del texto terciario sobre superficies base', () => {
  it('mantiene una relación WCAG AA mínima de 4.5:1', () => {
    const text = luminance(hex('ink-faint'))
    for (const token of ['surface', 'surface-2', 'surface-3']) {
      const background = luminance(hex(token))
      expect((text + 0.05) / (background + 0.05), token).toBeGreaterThanOrEqual(4.5)
    }
  })
})
