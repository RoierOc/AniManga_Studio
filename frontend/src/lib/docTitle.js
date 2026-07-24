/* Título de la ventana según lo que estés haciendo.
 *
 * `document.title` no se tocaba nunca: la barra de título propia, la barra de tareas de Windows y
 * Alt-Tab decían siempre «AniManga Studio», estuvieras leyendo un capítulo o viendo un episodio.
 * En una app de escritorio eso es información gratis que se tira.
 *
 * Lo específico manda sobre lo general: reproductor > lector de novela > lector de manga > ficha
 * abierta > sección. Devuelve también el texto para que `TitleBar` lo pinte junto a la marca.
 */
import { computed } from 'vue'
import { useUiStore, VIEWS } from '@/stores/ui'
import { useMangaStore } from '@/stores/manga'
import { useAnimeStore } from '@/stores/anime'
import { useNovelsStore } from '@/stores/novels'

const APP = 'AniManga Studio'
const EXTRA = { settings: 'Ajustes', workshop: 'Taller', kitchen: 'Cocina del diseño' }

export function useDocTitle() {
  const ui = useUiStore()
  const manga = useMangaStore()
  const anime = useAnimeStore()
  const novels = useNovelsStore()

  // Contexto actual, de lo más específico a lo más general.
  const context = computed(() => {
    const np = anime.nativePlayer
    if (np) {
      const ep = np.ep?.num != null ? `Ep. ${np.ep.num}` : ''
      return [np.anime?.title, ep].filter(Boolean).join(' · ')
    }
    if (novels.reader) {
      return [novels.reader.title, novels.reader.chapterName].filter(Boolean).join(' · ')
    }
    if (manga.reader) {
      const ch = manga.reader.chapter != null ? `Cap. ${manga.reader.chapter}` : ''
      const name = manga.current?.name || manga.reader.title
      return [name, ch].filter(Boolean).join(' · ')
    }
    if (manga.current) return manga.current.name
    if (anime.detailId) return anime.detail?.title || ''
    if (EXTRA[ui.currentView]) return EXTRA[ui.currentView]
    const item = VIEWS.flatMap(g => g.items).find(
      i => i.id === ui.currentView && (!i.sub || i.sub === anime.sub),
    ) || VIEWS.flatMap(g => g.items).find(i => i.id === ui.currentView)
    return item?.label || ''
  })

  const title = computed(() => (context.value ? `${context.value} — ${APP}` : APP))
  return { context, title, APP }
}
