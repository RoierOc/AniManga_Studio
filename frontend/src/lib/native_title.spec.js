import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const FRONTEND = fileURLToPath(new URL('../../', import.meta.url))
const SOURCE = join(FRONTEND, 'src')

// Sólo inspeccionamos etiquetas HTML nativas. `title` en <EmptyState>, <MediaCard>, etc. es una
// prop del componente y no debe convertirse: esos componentes necesitan su API semántica.
const NATIVE_TITLE = /<(?:a|area|audio|button|canvas|caption|details|dialog|div|embed|fieldset|figcaption|figure|footer|form|h[1-6]|header|iframe|img|input|label|li|main|menu|nav|ol|option|p|progress|section|select|summary|table|td|textarea|th|tr|video|aside)\b[^>]*?\s(?:(?::|v-bind:)?title)\s*=/gs

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(file)
    return /\.(?:vue|html)$/.test(entry.name) ? [file] : []
  })
}

function nativeTitles() {
  const files = [...sourceFiles(SOURCE), join(FRONTEND, 'index.html')]
  return files.flatMap((file) => {
    const text = readFileSync(file, 'utf8')
    return [...text.matchAll(NATIVE_TITLE)].map((match) => ({
      file: relative(FRONTEND, file),
      line: text.slice(0, match.index).split('\n').length,
      tag: match[0].replace(/\s+/g, ' ').trim(),
    }))
  })
}

describe('contrato de tooltips', () => {
  it('no deja tooltips nativos en etiquetas HTML', () => {
    expect(nativeTitles()).toEqual([])
  })
})
