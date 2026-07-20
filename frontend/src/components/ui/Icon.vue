<script setup>
// Hand-picked icon set. Stroke-based, 1.6 weight — HUD-leaning, not generic.
const PATHS = {
  library: '<path d="M4 19V5a1 1 0 0 1 1-1h4v16H5a1 1 0 0 1-1-1Z"/><path d="M9 4h5v16H9z"/><path d="m15 4.5 3.6.9a1 1 0 0 1 .7 1.2L16 20"/>',
  search:  '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/>',
  alert:   '<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
  heart:   '<path d="M12 20s-7-4.3-9.3-8.6C1.2 8.4 2.6 5 6 5c2 0 3.2 1.2 4 2.4C10.8 6.2 12 5 14 5c3.4 0 4.8 3.4 3.3 6.4C19 15.7 12 20 12 20Z"/>',
  globe:   '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.7 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3Z"/>',
  folder:  '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h6a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  film:    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  menu:    '<path d="M4 7h16M4 12h16M4 17h16"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  // Paleta de pintor → escalado a color (APISR).
  palette: '<path d="M12 3a9 9 0 1 0 0 18c.9 0 1.5-.7 1.5-1.5 0-.4-.2-.8-.4-1-.3-.3-.4-.6-.4-1 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.4-4-7-9-7Z"/><circle cx="7.5" cy="10.5" r="1"/><circle cx="12" cy="7.5" r="1"/><circle cx="16.5" cy="10.5" r="1"/>',
  // Triángulo + barra → saltar al episodio siguiente.
  'skip-next': '<path d="M6 5.5v13a1 1 0 0 0 1.5.87l9-6.5a1 1 0 0 0 0-1.74l-9-6.5A1 1 0 0 0 6 5.5Z"/><path d="M18 5v14"/>',
  play:    '<path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5Z"/>',
  download:'<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  spark:   '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m6 6 2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  // Barras ascendentes → panel de estadísticas.
  chart:   '<path d="M4 20V4"/><path d="M4 20h16"/><rect x="7" y="13" width="3" height="4" rx="0.5"/><rect x="12" y="9" width="3" height="8" rx="0.5"/><rect x="17" y="5" width="3" height="12" rx="0.5"/>',
  close:   '<path d="m6 6 12 12M18 6 6 18"/>',
  check:   '<path d="m5 12 4.5 4.5L19 7"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.2 7l2.6 1.5M17.2 15.5l2.6 1.5M4.2 17l2.6-1.5M17.2 8.5l2.6-1.5"/>',
  pause:   '<path d="M9 5v14M15 5v14"/>',
  external:'<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/>',
  clock:   '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  upload:  '<path d="M12 15V3m0 0 4 4m-4-4-4 4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  plus:    '<path d="M12 5v14M5 12h14"/>',
  screen:  '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  refresh: '<path d="M20 11A8 8 0 0 0 6.3 6.3L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 13a8 8 0 0 0 13.7 4.7L20 15.5"/><path d="M20 20v-4.5h-4.5"/>',
  // Cuatro flechas hacia las esquinas → entrar a pantalla completa.
  expand:  '<path d="M8 3H4a1 1 0 0 0-1 1v4"/><path d="M16 3h4a1 1 0 0 1 1 1v4"/><path d="M16 21h4a1 1 0 0 0 1-1v-4"/><path d="M8 21H4a1 1 0 0 1-1-1v-4"/>',
  // Cuatro flechas hacia el centro → salir de pantalla completa.
  collapse:'<path d="M3 8h4a1 1 0 0 0 1-1V3"/><path d="M21 8h-4a1 1 0 0 1-1-1V3"/><path d="M21 16h-4a1 1 0 0 0-1 1v4"/><path d="M3 16h4a1 1 0 0 1 1 1v4"/>',
  trash:   '<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7v13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7"/><path d="M10 11v6M14 11v6"/>',
  // Grid 2x2 → cobertura por capítulo (heatmap capítulo×fuente).
  grid:    '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  // Sol → brillo del lector.
  sun:     '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  // Libro cerrado → modo PAGINADO (una página tras otra).
  book:    '<path d="M5 4a1 1 0 0 1 1-1h13v18H6a1 1 0 0 1-1-1V4Z"/><path d="M9 3v18"/>',
  // Tira vertical continua → modo WEBTOON (scroll).
  scroll:  '<rect x="7" y="2.5" width="10" height="19" rx="1.5"/><path d="M9.5 7h5M9.5 11h5M9.5 15h5"/>',
  // Libro abierto → doble página.
  'book-open': '<path d="M12 6C9.5 4.5 6.5 4.5 4 5.5v13C6.5 17.5 9.5 17.5 12 19M12 6c2.5-1.5 5.5-1.5 8-.5v13c-2.5-1C17.5 17.5 14.5 17.5 12 19M12 6v13"/>',
  // Flechas horizontales → dirección de lectura (Izq↔Der).
  'arrows-h': '<path d="M8 8 4 12l4 4M16 8l4 4-4 4M4 12h16"/>',
}
defineProps({ name: { type: String, required: true }, size: { type: [Number, String], default: 20 } })
</script>

<template>
  <svg
    :width="size" :height="size" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round"
    v-html="PATHS[name] || ''"
  />
</template>
