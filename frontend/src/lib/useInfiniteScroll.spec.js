// @vitest-environment happy-dom
import { defineComponent, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useInfiniteScroll } from './useInfiniteScroll'

afterEach(() => vi.unstubAllGlobals())

describe('useInfiniteScroll', () => {
  it('solicita más solo al intersectar y cuando la lista aún admite otra página', async () => {
    let observer
    class FakeObserver {
      constructor(callback, options) { this.callback = callback; this.options = options; observer = this }
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
    }
    vi.stubGlobal('IntersectionObserver', FakeObserver)

    const loadMore = vi.fn()
    const canLoad = ref(false)
    const Host = defineComponent({
      setup() { return { sentinel: useInfiniteScroll(loadMore, () => canLoad.value, '600px') } },
      template: '<div ref="sentinel"></div>',
    })
    const wrapper = mount(Host)
    await nextTick()

    expect(observer.options.rootMargin).toBe('600px')
    observer.callback([{ isIntersecting: true }])
    expect(loadMore).not.toHaveBeenCalled()

    canLoad.value = true
    observer.callback([{ isIntersecting: true }])
    expect(loadMore).toHaveBeenCalledOnce()

    wrapper.unmount()
    expect(observer.disconnect).toHaveBeenCalledOnce()
  })
})
