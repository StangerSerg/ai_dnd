import { useEffect, useRef } from 'react'
import './gate-moonlight.css'

const SCENE_WIDTH = 1536
const SCENE_HEIGHT = 1024
const CANVAS_SCALE = 0.5
const CLOUD_WIDTH = 768
const CLOUD_HEIGHT = 384
const CLOUD_SPEED = 22
const FRAME_INTERVAL = 1000 / 30
const FADE_DURATION = 1400
const LIGHT_RESPONSE = 600
const MAX_LIGHT = 0.28
const CLOUD_URL = '/images/gate-cloud-field.webp'
const MASK_URL = '/images/castle-sky-mask.png'
const MOON = { x: 156, y: 100, radius: 29 }
const MOON_SAMPLES: readonly (readonly [number, number])[] = [
  [MOON.x, MOON.y],
  ...Array.from({ length: 20 }, (_, index): [number, number] => {
    const radius = MOON.radius * Math.sqrt((index + 0.5) / 20)
    const angle = index * Math.PI * (3 - Math.sqrt(5))
    return [MOON.x + radius * Math.cos(angle), MOON.y + radius * Math.sin(angle)]
  }),
]

function moonCoverage(alpha: Uint8ClampedArray, phase: number) {
  let coverage = 0
  for (const [x, y] of MOON_SAMPLES) {
    const sampleX = (x + phase - 0.5) % CLOUD_WIDTH
    const sampleY = y - 0.5
    const x0 = Math.floor(sampleX)
    const x1 = (x0 + 1) % CLOUD_WIDTH
    const y0 = Math.floor(sampleY)
    const dx = sampleX - x0
    const dy = sampleY - y0
    const top = alpha[y0 * CLOUD_WIDTH + x0] * (1 - dx) + alpha[y0 * CLOUD_WIDTH + x1] * dx
    const bottom = alpha[(y0 + 1) * CLOUD_WIDTH + x0] * (1 - dx) + alpha[(y0 + 1) * CLOUD_WIDTH + x1] * dx
    coverage += top * (1 - dy) + bottom * dy
  }
  return coverage / (255 * MOON_SAMPLES.length)
}

function startingPhase(alpha: Uint8ClampedArray) {
  const candidates = Array.from({ length: CLOUD_WIDTH / 8 }, (_, index) => {
    const phase = index * 8
    return { phase, coverage: moonCoverage(alpha, phase) }
  })
  const clearest = Math.min(...candidates.map(candidate => candidate.coverage))
  let bestPhase = 0
  let bestScore = -Infinity
  for (const { phase, coverage } of candidates) {
    if (coverage > clearest + 0.06) continue
    const approaching = Math.max(...[4, 6, 8].map(seconds => moonCoverage(alpha, phase + CLOUD_SPEED * seconds)))
    const score = approaching - coverage - 0.3 * moonCoverage(alpha, phase + CLOUD_SPEED * 2)
    if (score > bestScore) {
      bestScore = score
      bestPhase = phase
    }
  }
  return bestPhase
}

export function GateMoonlight() {
  const frameRef = useRef<HTMLDivElement>(null)
  const lightFrameRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const frame = frameRef.current
    const lightFrame = lightFrameRef.current
    const canvas = canvasRef.current
    if (!frame || !lightFrame || !canvas) return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let disposed = false
    let failed = false
    let animationFrame = 0
    let previousFrame: number | null = null
    let context: CanvasRenderingContext2D | null = null
    let cloud: HTMLImageElement | null = null
    let mask: HTMLImageElement | null = null
    let alpha: Uint8ClampedArray | null = null
    let phase = 0
    let fadeTime = 0
    let moonVisibility = 1

    const pause = () => {
      cancelAnimationFrame(animationFrame)
      animationFrame = 0
      previousFrame = null
    }
    const hide = () => {
      pause()
      frame.classList.remove('is-moonlight-ready')
      lightFrame.classList.remove('is-moonlight-ready')
      frame.style.setProperty('--gate-moonlight', '0')
      lightFrame.style.setProperty('--gate-moonlight', '0')
      canvas.style.removeProperty('mask-image')
      canvas.style.removeProperty('-webkit-mask-image')
      fadeTime = 0
      moonVisibility = 1
    }
    const fail = () => {
      failed = true
      hide()
    }
    const draw = (elapsed: number) => {
      if (!context || !cloud || !alpha) return
      phase = (phase + elapsed * CLOUD_SPEED / 1000) % CLOUD_WIDTH
      fadeTime = Math.min(FADE_DURATION, fadeTime + elapsed)
      const fade = fadeTime / FADE_DURATION
      context.clearRect(0, 0, canvas.width, canvas.height)
      context.globalAlpha = fade
      for (let x = -phase; x < SCENE_WIDTH; x += CLOUD_WIDTH) {
        context.drawImage(cloud, x * CANVAS_SCALE, 0, CLOUD_WIDTH * CANVAS_SCALE, CLOUD_HEIGHT * CANVAS_SCALE)
      }
      const visibility = 1 - moonCoverage(alpha, phase) * fade
      moonVisibility += (visibility - moonVisibility) * (1 - Math.exp(-elapsed / LIGHT_RESPONSE))
      const value = (MAX_LIGHT * moonVisibility * fade).toFixed(4)
      frame.style.setProperty('--gate-moonlight', value)
      lightFrame.style.setProperty('--gate-moonlight', value)
    }
    const tick = (now: number) => {
      animationFrame = 0
      if (disposed || failed) return
      if (reducedMotion.matches) {
        hide()
        return
      }
      if (document.hidden) return
      const elapsed = previousFrame === null ? 0 : now - previousFrame
      if (previousFrame === null || elapsed >= FRAME_INTERVAL - 0.5) {
        previousFrame = now
        try {
          draw(elapsed)
        } catch {
          fail()
          return
        }
      }
      animationFrame = requestAnimationFrame(tick)
    }
    const resume = () => {
      if (disposed || failed) return
      if (reducedMotion.matches) {
        hide()
        return
      }
      if (document.hidden) {
        pause()
        return
      }
      if (!CSS.supports('mask-image', `url("${MASK_URL}")`) && !CSS.supports('-webkit-mask-image', `url("${MASK_URL}")`)) {
        fail()
        return
      }
      try {
        context ??= canvas.getContext('2d')
        if (!context) {
          fail()
          return
        }
        if (!cloud || !mask) {
          cloud = new Image()
          mask = new Image()
          cloud.decoding = 'async'
          mask.decoding = 'async'
          cloud.onload = resume
          mask.onload = resume
          cloud.onerror = fail
          mask.onerror = fail
          cloud.src = CLOUD_URL
          mask.src = MASK_URL
          return
        }
        if (!cloud.complete || !mask.complete) return
        if (cloud.naturalWidth !== CLOUD_WIDTH || cloud.naturalHeight !== CLOUD_HEIGHT
          || mask.naturalWidth !== SCENE_WIDTH || mask.naturalHeight !== SCENE_HEIGHT) {
          fail()
          return
        }
        if (!alpha) {
          const sampler = document.createElement('canvas')
          sampler.width = CLOUD_WIDTH
          sampler.height = CLOUD_HEIGHT
          const samplerContext = sampler.getContext('2d', { willReadFrequently: true })
          if (!samplerContext) {
            fail()
            return
          }
          samplerContext.drawImage(cloud, 0, 0)
          const pixels = samplerContext.getImageData(0, 0, CLOUD_WIDTH, CLOUD_HEIGHT).data
          alpha = new Uint8ClampedArray(CLOUD_WIDTH * CLOUD_HEIGHT)
          for (let index = 0; index < alpha.length; index++) alpha[index] = pixels[index * 4 + 3]
          phase = startingPhase(alpha)
        }
        canvas.style.setProperty('mask-image', `url("${MASK_URL}")`)
        canvas.style.setProperty('-webkit-mask-image', `url("${MASK_URL}")`)
        if (!animationFrame) {
          draw(0)
          frame.classList.add('is-moonlight-ready')
          lightFrame.classList.add('is-moonlight-ready')
          animationFrame = requestAnimationFrame(tick)
        }
      } catch {
        fail()
      }
    }

    canvas.addEventListener('contextlost', fail)
    reducedMotion.addEventListener('change', resume)
    document.addEventListener('visibilitychange', resume)
    resume()

    return () => {
      disposed = true
      hide()
      canvas.removeEventListener('contextlost', fail)
      reducedMotion.removeEventListener('change', resume)
      document.removeEventListener('visibilitychange', resume)
      for (const image of [cloud, mask]) {
        if (!image) continue
        image.onload = null
        image.onerror = null
        image.removeAttribute('src')
      }
    }
  }, [])

  return (
    <>
      <div className="gate-ambience gate-moonlight-illumination-layer" aria-hidden="true">
        <div ref={lightFrameRef} className="gate-ambience-frame">
          <div className="gate-moonlight-illumination" />
        </div>
      </div>
      <div className="gate-ambience gate-moonlight-ambience" aria-hidden="true">
        <div ref={frameRef} className="gate-ambience-frame">
          <canvas
            ref={canvasRef}
            className="gate-moonlight-clouds"
            width={SCENE_WIDTH * CANVAS_SCALE}
            height={SCENE_HEIGHT * CANVAS_SCALE}
          />
        </div>
      </div>
    </>
  )
}
