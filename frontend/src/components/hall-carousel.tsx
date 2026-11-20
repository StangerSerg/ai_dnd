import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'

const EDGE_EPSILON = 1

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Single-row hall carousel: wheel scrolling, arrow buttons, native swipe. */
export function HallCarousel({ count, children }: { count: number; children: ReactNode }) {
  const { t } = useTranslation()
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [canLeft, setCanLeft] = useState(false)
  const [canRight, setCanRight] = useState(false)

  const updateArrows = useCallback(() => {
    const scroller = scrollerRef.current
    if (!scroller) return
    const max = scroller.scrollWidth - scroller.clientWidth
    setCanLeft(scroller.scrollLeft > EDGE_EPSILON)
    setCanRight(scroller.scrollLeft < max - EDGE_EPSILON)
  }, [])

  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller) return

    function onWheel(event: WheelEvent) {
      if (!scroller) return
      if (event.deltaY === 0 || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return
      const max = scroller.scrollWidth - scroller.clientWidth
      if (max <= EDGE_EPSILON) return
      const delta =
        event.deltaMode === 1 ? event.deltaY * 16
        : event.deltaMode === 2 ? event.deltaY * scroller.clientWidth
        : event.deltaY
      const scrollable = delta > 0 ? scroller.scrollLeft < max - EDGE_EPSILON : scroller.scrollLeft > EDGE_EPSILON
      if (!scrollable) return
      event.preventDefault()
      scroller.scrollLeft = Math.max(0, Math.min(max, scroller.scrollLeft + delta))
    }

    updateArrows()
    scroller.addEventListener('scroll', updateArrows, { passive: true })
    scroller.addEventListener('wheel', onWheel, { passive: false })
    const observer = new ResizeObserver(updateArrows)
    observer.observe(scroller)
    return () => {
      scroller.removeEventListener('scroll', updateArrows)
      scroller.removeEventListener('wheel', onWheel)
      observer.disconnect()
    }
  }, [updateArrows, count])

  function scrollByCard(direction: -1 | 1) {
    const scroller = scrollerRef.current
    if (!scroller) return
    const card = scroller.querySelector<HTMLElement>('.table-choice')
    const gap = Number.parseFloat(getComputedStyle(scroller).columnGap) || 0
    const step = card ? card.offsetWidth + gap : scroller.clientWidth
    scroller.scrollBy({ left: direction * step, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
  }

  return (
    <div className="hall-carousel">
      <div className="hall-carousel-nav">
        <button
          type="button"
          className="hall-arrow"
          aria-label={t('games:hall.scrollLeft')}
          disabled={!canLeft}
          onClick={() => scrollByCard(-1)}
          data-testid="hall-arrow-left"
        >
          <ChevronLeftIcon aria-hidden="true" />
        </button>
        <button
          type="button"
          className="hall-arrow"
          aria-label={t('games:hall.scrollRight')}
          disabled={!canRight}
          onClick={() => scrollByCard(1)}
          data-testid="hall-arrow-right"
        >
          <ChevronRightIcon aria-hidden="true" />
        </button>
      </div>
      <div className="table-row" ref={scrollerRef} data-testid="hall-carousel">
        {children}
      </div>
    </div>
  )
}