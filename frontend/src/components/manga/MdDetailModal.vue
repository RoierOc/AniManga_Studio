<script setup>
import { computed } from 'vue'
import { useMangadexStore } from '@/stores/mangadex'
import { useMangaStore } from '@/stores/manga'
import { taskId } from '@/lib/manga'
import Icon from '@/components/ui/Icon.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store = useMangadexStore()
const manga = useMangaStore()        // for live download progress via its `downloads` map
const d = computed(() => store.detail)

const LANG_FLAG = { en: '🇬🇧', es: '🇪🇸', 'es-la': '🌎', ja: '🇯🇵', 'pt-br': '🇧🇷', fr: '🇫🇷', ko: '🇰🇷', zh: '🇨🇳', 'zh-hk': '🇭🇰', it: '🇮🇹', de: '🇩🇪', ru: '🇷🇺' }
const flag = (l) => LANG_FLAG[l] || l

function dlState(ch) {
  const t = manga.downloads[taskId(d.value.title, ch.chapter, 'download')]
  return t && !['done', 'error', 'cancelled'].includes(t.status) ? t : null
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="d" class="ov" @click.self="store.closeDetail()">
        <div class="modal">
          <button class="modal__x" @click="store.closeDetail()"><Icon name="close" :size="18" /></button>

          <header class="modal__head" :style="d.cover ? `--bg:url('${d.cover}')` : ''">
            <div class="modal__head-scrim" />
            <img v-if="d.cover" :src="d.cover" class="modal__cover" :alt="d.title" />
            <div class="modal__info">
              <h2 class="modal__title">{{ d.title }}</h2>
              <p class="modal__by" v-if="d.author">{{ d.author }}<span v-if="d.artist && d.artist !== d.author"> · {{ d.artist }}</span></p>
              <div class="modal__chips">
                <span v-if="d.status" class="chip">{{ d.status }}</span>
                <span v-if="d.year" class="chip">{{ d.year }}</span>
                <span v-if="store.score(d)" class="chip chip--score">★ {{ (store.score(d) / 10).toFixed(1) }}</span>
              </div>
              <div class="modal__acts">
                <button class="abtn abtn--accent" @click="store.addLocal(d)"><Icon name="heart" :size="13" /> Mi biblioteca</button>
                <button v-if="store.authed" class="abtn" @click="store.follow(d)"><Icon name="spark" :size="13" /> Seguir</button>
              </div>
            </div>
          </header>

          <p v-if="d.description" class="modal__desc">{{ d.description }}</p>

          <div class="modal__chapters">
            <div class="modal__chhead">
              <span>Capítulos</span>
              <select v-if="store.detailLangs.length > 1" v-model="store.detailLang" class="langsel">
                <option v-for="l in store.detailLangs" :key="l" :value="l">{{ flag(l) }} {{ l }}</option>
              </select>
            </div>

            <div v-if="store.detailLoading" class="center"><Spinner /></div>
            <div v-else-if="!store.detailChapters.length" class="empty">Sin capítulos.</div>
            <ul v-else class="chaps">
              <li v-for="ch in store.detailChapters" :key="ch.id" class="chap">
                <div class="chap__main">
                  <span class="chap__num">{{ ch.chapter === 'one_shot' ? 'One-shot' : `Cap. ${ch.chapter}` }}</span>
                  <span v-if="ch.title" class="chap__title">{{ ch.title }}</span>
                  <span class="chap__grp">{{ (ch.groups || []).join(', ') }}<template v-if="ch.pages"> · {{ ch.pages }} pág.</template></span>
                </div>
                <div v-if="dlState(ch)" class="chap__prog">
                  <Spinner :size="13" /><span>{{ dlState(ch).progress || dlState(ch).status }}</span>
                </div>
                <button v-else class="chap__dl" @click="store.downloadChapter(ch)"><Icon name="download" :size="14" /> Descargar</button>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ov { position: fixed; inset: 0; z-index: var(--z-modal); display: grid; place-items: center; padding: var(--s-5); background: rgba(7,10,18,.72); backdrop-filter: blur(8px); }
.modal { position: relative; width: min(760px, 100%); max-height: 88vh; display: flex; flex-direction: column; background: var(--glass-strong); border: 1px solid var(--line-2); border-radius: var(--r-lg); box-shadow: var(--shadow-xl); overflow: hidden; }
.modal__x { position: absolute; top: var(--s-3); right: var(--s-3); z-index: 3; width: 34px; height: 34px; display: grid; place-items: center; border-radius: var(--r-sm); color: #fff; background: rgba(7,10,18,.5); border: 1px solid var(--line); transition: all var(--t-fast); }
.modal__x:hover { background: rgba(7,10,18,.8); }

.modal__head { position: relative; display: flex; gap: var(--s-4); padding: var(--s-5); }
.modal__head::before { content: ''; position: absolute; inset: 0; background: var(--bg) center/cover; opacity: .18; filter: blur(20px); }
.modal__head-scrim { position: absolute; inset: 0; background: linear-gradient(180deg, transparent, var(--glass-strong)); }
.modal__cover { position: relative; width: 110px; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--r-md); box-shadow: var(--shadow-md); flex-shrink: 0; }
.modal__info { position: relative; min-width: 0; padding-right: var(--s-6); }
.modal__title { font-size: var(--fs-xl); line-height: var(--lh-snug); }
.modal__by { color: var(--ink-soft); font-size: var(--fs-sm); margin-top: 2px; }
.modal__chips { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.chip { font-size: var(--fs-2xs); padding: 2px 8px; border-radius: var(--r-pill); background: var(--surface-2); border: 1px solid var(--line); color: var(--ink-soft); text-transform: capitalize; }
.chip--score { color: var(--gold); }
.modal__acts { display: flex; gap: var(--s-2); margin-top: var(--s-4); }
.abtn { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); }
.abtn:hover { color: var(--ink); border-color: var(--line-strong); }
.abtn--accent { background: var(--azure); color: #fff; border-color: transparent; }
.abtn--accent:hover { background: var(--azure-bright); color: #fff; }

.modal__desc { padding: 0 var(--s-5) var(--s-3); font-size: var(--fs-sm); color: var(--ink-soft); line-height: var(--lh-body); max-height: 110px; overflow-y: auto; }
.modal__chapters { flex: 1; overflow: hidden; display: flex; flex-direction: column; border-top: 1px solid var(--line); }
.modal__chhead { display: flex; align-items: center; justify-content: space-between; padding: var(--s-3) var(--s-5); font-weight: 600; }
.langsel { padding: var(--s-1) var(--s-3); border-radius: var(--r-sm); background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); font-size: var(--fs-sm); }
.center { display: grid; place-items: center; padding: var(--s-6); }
.empty { text-align: center; color: var(--ink-faint); padding: var(--s-6); }
.chaps { overflow-y: auto; padding: 0 var(--s-3) var(--s-3); }
.chap { display: flex; align-items: center; gap: var(--s-3); padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); transition: background var(--t-fast); }
.chap:hover { background: var(--surface); }
.chap__main { flex: 1; min-width: 0; }
.chap__num { font-weight: 600; font-size: var(--fs-sm); }
.chap__title { color: var(--ink-soft); font-size: var(--fs-xs); margin-left: var(--s-2); }
.chap__grp { display: block; font-size: var(--fs-2xs); color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chap__dl { display: inline-flex; align-items: center; gap: 6px; padding: var(--s-2) var(--s-3); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); border: 1px solid var(--line-2); transition: all var(--t-fast); flex-shrink: 0; }
.chap__dl:hover { color: #fff; background: var(--azure); border-color: transparent; }
.chap__prog { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--cyan); flex-shrink: 0; }

.modal-enter-active, .modal-leave-active { transition: opacity var(--t-base); }
.modal-enter-active .modal { transition: transform var(--t-base) var(--ease-snap); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal { transform: scale(.95) translateY(12px); }

@media (max-width: 560px) { .modal__head { flex-direction: column; } .modal__info { padding-right: 0; } }
</style>
