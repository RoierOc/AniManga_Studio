import { describe, expect, it } from 'vitest'
import { filterSettingsSections, normalizeSettingsSearch } from './settingsSearch'

const sections = [
  { id: 'anime', label: 'Anime', description: 'Descargas y subtítulos', keywords: ['qBittorrent', 'carpeta'] },
  { id: 'conexiones', label: 'Conexiones', description: 'Claves API', keywords: ['.env', 'servicios'] },
  { id: 'almacenamiento', label: 'Almacenamiento', description: 'Uso de disco y caché', keywords: ['raíces'] },
]

describe('búsqueda de Ajustes', () => {
  it('normaliza mayúsculas, espacios y acentos', () => {
    expect(normalizeSettingsSearch('  CACHÉ  ')).toBe('cache')
  })

  it('devuelve todas las secciones sin consulta', () => {
    expect(filterSettingsSections('', sections)).toBe(sections)
  })

  it('encuentra una sección por sus etiquetas de contenido', () => {
    expect(filterSettingsSections('carpeta descargas', sections).map((s) => s.id)).toEqual(['anime'])
    expect(filterSettingsSections('api', sections).map((s) => s.id)).toEqual(['conexiones'])
    expect(filterSettingsSections('cache', sections).map((s) => s.id)).toEqual(['almacenamiento'])
  })

  it('exige todos los términos y deja claro cuando no hay coincidencias', () => {
    expect(filterSettingsSections('anime api', sections)).toEqual([])
    expect(filterSettingsSections('qbit', sections).map((s) => s.id)).toEqual(['anime'])
  })
})
