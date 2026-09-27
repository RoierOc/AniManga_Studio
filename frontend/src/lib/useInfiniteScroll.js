import { onBeforeUnmount, ref, watch } from 'vue'

export function useInfiniteScroll(loadMore, canLoad = () => true, rootMargin = '600px') {
  const sentinel = ref(null)
  let observer = null

  watch(sentinel, (el, previous) => {
    if (previous) observer?.unobserve(previous)
    if (!el || typeof IntersectionObserver === 'undefined') return
    observer ||= new IntersectionObserver((entries) => {
      if (entries.some(entry => entry.isIntersecting) && canLoad()) loadMore()
    }, { rootMargin })
    observer.observe(el)
  })

  onBeforeUnmount(() => observer?.disconnect())
  return sentinel
}
