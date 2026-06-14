<script setup>
// Hand-picked icon set. Stroke-based, 1.6 weight — HUD-leaning, not generic.
const PATHS = {
  library: '<path d="M4 19V5a1 1 0 0 1 1-1h4v16H5a1 1 0 0 1-1-1Z"/><path d="M9 4h5v16H9z"/><path d="m15 4.5 3.6.9a1 1 0 0 1 .7 1.2L16 20"/>',
  search:  '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/>',
  heart:   '<path d="M12 20s-7-4.3-9.3-8.6C1.2 8.4 2.6 5 6 5c2 0 3.2 1.2 4 2.4C10.8 6.2 12 5 14 5c3.4 0 4.8 3.4 3.3 6.4C19 15.7 12 20 12 20Z"/>',
  globe:   '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.7 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3Z"/>',
  folder:  '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h6a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  film:    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  menu:    '<path d="M4 7h16M4 12h16M4 17h16"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  play:    '<path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5Z"/>',
  download:'<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  spark:   '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m6 6 2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  close:   '<path d="m6 6 12 12M18 6 6 18"/>',
  check:   '<path d="m5 12 4.5 4.5L19 7"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.2 7l2.6 1.5M17.2 15.5l2.6 1.5M4.2 17l2.6-1.5M17.2 8.5l2.6-1.5"/>',
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
