import { useEffect, useRef, useState } from 'react'
import { GateMoonlight } from './GateMoonlight'
import './gate-ambience.css'

function VideoTorch({ side }: { side: 'left' | 'right' }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const cleanPlate = new Image()
    let disposed = false
    let failed = false
    let phaseInitialized = false
    let playAttempt = 0
    let waitingTimer: number | undefined

    const clearWaitingTimer = () => {
      window.clearTimeout(waitingTimer)
      waitingTimer = undefined
    }
    const showFallback = () => {
      clearWaitingTimer()
      setIsPlaying(false)
    }
    const pause = () => {
      // Invalidate a pending play rejection before pausing (including StrictMode).
      playAttempt += 1
      video.pause()
      showFallback()
    }
    const unload = () => {
      clearWaitingTimer()
      if (!video.hasAttribute('src')) return
      phaseInitialized = false
      video.removeAttribute('src')
      video.load()
    }
    const play = () => {
      if (disposed || failed || reducedMotion.matches || document.hidden) return
      if (!cleanPlate.hasAttribute('src')) cleanPlate.src = '/images/castle-gate-clear-moon-unlit.webp'
      if (!cleanPlate.complete || !cleanPlate.naturalWidth) return
      if (!video.hasAttribute('src')) video.src = '/images/torch-flame-loop.webm'
      video.muted = true
      const attempt = ++playAttempt
      void video.play().catch(() => {
        if (disposed || attempt !== playAttempt) return
        failed = true
        pause()
        unload()
      })
    }
    const onPlaying = () => {
      clearWaitingTimer()
      if (disposed || failed || reducedMotion.matches || document.hidden || video.paused) return
      setIsPlaying(true)
    }
    const onWaiting = () => {
      if (disposed || failed || reducedMotion.matches || document.hidden || video.paused || waitingTimer !== undefined) return
      // Keep the last ready frame visible through brief loop-boundary waits.
      waitingTimer = window.setTimeout(() => {
        if (waitingTimer === undefined) return
        waitingTimer = undefined
        if (disposed || failed || reducedMotion.matches || document.hidden) return
        if (video.readyState < 3 || video.paused) showFallback()
      }, 150)
    }
    const onError = () => {
      if (disposed) return
      showFallback()
      if (!video.hasAttribute('src')) return
      failed = true
      pause()
      unload()
    }
    const onLoadedMetadata = () => {
      if (disposed || failed || reducedMotion.matches || phaseInitialized || !video.hasAttribute('src')) return
      phaseInitialized = true
      // Seek once per load so visibility pauses preserve the phase.
      if (side === 'right') {
        try {
          video.currentTime = 2.6
        } catch {
          onError()
        }
      }
    }
    const onVisibilityChange = () => {
      if (document.hidden) pause()
      else play()
    }
    const onMotionChange = () => {
      if (reducedMotion.matches) {
        pause()
        unload()
        cleanPlate.removeAttribute('src')
      } else {
        failed = false
        onVisibilityChange()
      }
    }

    cleanPlate.onload = play
    cleanPlate.onerror = () => {
      if (disposed || reducedMotion.matches || !cleanPlate.hasAttribute('src')) return
      failed = true
      pause()
      unload()
    }
    video.addEventListener('loadedmetadata', onLoadedMetadata)
    video.addEventListener('playing', onPlaying)
    video.addEventListener('pause', showFallback)
    video.addEventListener('waiting', onWaiting)
    video.addEventListener('emptied', showFallback)
    video.addEventListener('error', onError)
    reducedMotion.addEventListener('change', onMotionChange)
    document.addEventListener('visibilitychange', onVisibilityChange)
    onMotionChange()

    return () => {
      disposed = true
      clearWaitingTimer()
      playAttempt += 1
      cleanPlate.onload = null
      cleanPlate.onerror = null
      cleanPlate.removeAttribute('src')
      video.removeEventListener('loadedmetadata', onLoadedMetadata)
      video.removeEventListener('playing', onPlaying)
      video.removeEventListener('pause', showFallback)
      video.removeEventListener('waiting', onWaiting)
      video.removeEventListener('emptied', showFallback)
      video.removeEventListener('error', onError)
      reducedMotion.removeEventListener('change', onMotionChange)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      video.pause()
      unload()
    }
  }, [side])

  return (
    <div className={`gate-torch gate-torch-${side}${isPlaying ? ' is-playing' : ''}`}>
      <span className="gate-torch-glow" />
      <video
        ref={videoRef}
        className="gate-torch-video"
        muted
        playsInline
        loop
        preload="none"
        controls={false}
        tabIndex={-1}
        aria-hidden="true"
        disablePictureInPicture
        disableRemotePlayback
      />
    </div>
  )
}

export function GateAmbience() {
  return <>
    <GateMoonlight />
    <div className="gate-ambience gate-ambience-video" aria-hidden="true">
      <div className="gate-ambience-frame">
        <VideoTorch side="left" />
        <VideoTorch side="right" />
      </div>
    </div>
  </>
}