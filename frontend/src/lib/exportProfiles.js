/* Perfiles pequeños y explícitos para la exportación de tomos.
 *
 * Sólo describen ajustes; la validación final sigue estando en el backend. Mantenerlos fuera del
 * modal permite reutilizarlos en el exportador por lotes y probarlos sin montar una vista Vue.
 */
export const EXPORT_PROFILES = Object.freeze([
  { value: 'manual', label: 'Manual', hint: 'ajustes actuales' },
  { value: 'compatible', label: 'Compatible', hint: 'JPEG · 92' },
  { value: 'tablet', label: 'Tablet ligero', hint: 'WebP · 85 · mitad' },
  { value: 'quality', label: 'Alta calidad', hint: 'WebP · 92' },
  { value: 'maximum', label: 'Máxima calidad', hint: 'WebP · 100' },
])

const VALUES = Object.freeze({
  compatible: { format: 'cbz', codec: 'jpeg', quality: 92, downscaleHalf: false },
  tablet: { format: 'cbz', codec: 'webp', quality: 85, downscaleHalf: true },
  quality: { format: 'cbz', codec: 'webp', quality: 92, downscaleHalf: false },
  maximum: { format: 'cbz', codec: 'webp', quality: 100, downscaleHalf: false },
})

export function exportProfileValues(name) {
  const values = VALUES[name]
  return values ? { ...values } : null
}
