import { ref } from 'vue'

// Sólo sesión: filtros y posiciones, nunca datos de biblioteca ni pantallas montadas.
const fields = new Map()
export const viewPositions = new Map()

export function rememberedRef(key, initial) {
  if (!fields.has(key)) fields.set(key, ref(initial))
  return fields.get(key)
}

export function viewMemoryKey(st) {
  const sub = st.view === 'anime' ? st.sub : st.view === 'media' ? st.tabs?.media
    : st.view === 'library' ? st.tabs?.lib : st.view === 'explore' ? st.tabs?.exp : ''
  return JSON.stringify([st.view, sub || '', st.view === 'anime' ? st.animeDetail || st.preview?.al_id || '' : '',
    st.view === 'media' ? [st.mediaDetail?.kind || '', st.mediaDetail?.id || ''] : '', st.manga?.id || '', st.reader?.chapter ?? '',
    st.novel?.novelId || '', st.novelReader?.chapterIndex ?? ''])
}
