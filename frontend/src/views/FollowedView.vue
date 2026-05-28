<script setup>
import { onMounted } from 'vue'
import { useMangadexStore } from '@/stores/mangadex'
import MdCard from '@/components/manga/MdCard.vue'
import MdDetailModal from '@/components/manga/MdDetailModal.vue'
import Icon from '@/components/ui/Icon.vue'

const store = useMangadexStore()
onMounted(() => { if (!store.followedLoaded) store.loadFollowed() })
</script>

<template>
  <div class="fl">
    <header class="fl__head">
      <p class="eyebrow"><span class="tick" /> TU LISTA EN MANGADEX</p>
      <h1>Seguidos</h1>
    </header>

    <div v-if="store.loading && !store.followed.length" class="grid">
      <div v-for="n in 12" :key="n" class="skeleton" />
    </div>
    <div v-else-if="!store.followed.length" class="empty">
      <Icon name="heart" :size="34" />
      <p>{{ store.authed ? 'No sigues ningún manga aún.' : 'Inicia sesión en MangaDex para ver tus seguidos.' }}</p>
    </div>
    <div v-else class="grid">
      <MdCard v-for="m in store.followed" :key="m.id" :manga="m" :score="store.score(m)" @open="store.openDetail($event)" />
    </div>

    <MdDetailModal />
  </div>
</template>

<style scoped>
.fl { max-width: var(--content-max); margin: 0 auto; padding: 0 var(--s-6) var(--s-8); }
.fl__head { padding: var(--s-5) 0; }
.eyebrow { display: flex; align-items: center; gap: var(--s-2); font-family: var(--font-mono); font-size: var(--fs-2xs); letter-spacing: var(--tracking-caps); color: var(--azure); margin-bottom: var(--s-2); }
.tick { width: 14px; height: 1px; background: var(--azure); box-shadow: 0 0 8px var(--azure-glow); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: var(--s-5); }
.skeleton { aspect-ratio: 2/3; border-radius: var(--r-md); background: linear-gradient(100deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
.empty { display: flex; flex-direction: column; align-items: center; gap: var(--s-3); padding: var(--s-8) 0; color: var(--ink-faint); text-align: center; }
@media (max-width: 540px) { .fl { padding: 0 var(--s-4) var(--s-8); } .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); } }
</style>
