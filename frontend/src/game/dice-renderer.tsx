// DiceRenderer seam (stage 3.5, generalized in #21): the game screen renders
// rolls only through this wrapper. kind='2d' delegates to the stylized 2D
// dice (./dice-2d.tsx — a single d20 still goes through the preserved Codex
// donor src/demo/Dice.tsx, byte-identical); kind='3d' lazy-loads the dice-box
// adapter (./dice-3d.tsx, separate chunk with three.js inside) so the
// lobby/portal bundles stay light. The 2D dice is always the fallback:
// "less animation", prefers-reduced-motion, mobile/weak devices, chunk load
// errors, WebGL failures and dice the 3D library cannot force (exotic sides)
// all land on it.
import { Component, lazy, Suspense, useSyncExternalStore, type ReactNode } from 'react'
import { clampD20, Dice2D, type DieSpec } from './dice-2d'
import type { D20Value, DiceStyle } from '@/demo/Dice'
import { useSettings } from '@/stores/settings'

export type { D20Value, DiceStyle, DieSpec }
export { clampD20 }

export type DiceRendererKind = '2d' | '3d'

const Dice3D = lazy(() => import('./dice-3d'))

// Die types @3d-dice/dice-box-threejs can force deterministically (verified
// against its dist: d4/d6/d8/d10/d12/d20 geometries + swapDiceFace). d100 is
// tens-only in the library and percentile server values are 1–100, so exotic
// sides stay on the stylized 2D fallback instead of risking a wrong face.
const D3_SIDES: ReadonlySet<number> = new Set([4, 6, 8, 10, 12, 20])

/** Whether the whole roll can play on the 3D stage (every die supported). */
export function supports3D(dice: readonly DieSpec[]): boolean {
  return dice.length > 0 && dice.every(die => D3_SIDES.has(die.sides))
}

// Capability probes for the default kind: reduced motion, coarse pointer
// (phones/tablets) and small viewports get 2D; low-core devices too.
const MEDIA_QUERIES = [
  '(prefers-reduced-motion: reduce)',
  '(pointer: coarse)',
  '(max-width: 768px)',
]

function subscribeMedia(onChange: () => void) {
  const lists = MEDIA_QUERIES.map(query => window.matchMedia(query))
  for (const list of lists) list.addEventListener('change', onChange)
  return () => {
    for (const list of lists) list.removeEventListener('change', onChange)
  }
}

function mediaKind(): DiceRendererKind {
  return MEDIA_QUERIES.some(query => window.matchMedia(query).matches) ? '2d' : '3d'
}

function lowPowerDevice(): boolean {
  return (navigator.hardwareConcurrency ?? 8) < 4
}

/** Resolves the user setting + device capabilities into the effective kind. */
export function useEffectiveDiceKind(): DiceRendererKind {
  const lessAnimation = useSettings(state => state.lessAnimation)
  const media = useSyncExternalStore(subscribeMedia, mediaKind, () => '2d' as const)
  return lessAnimation || media === '2d' || lowPowerDevice() ? '2d' : '3d'
}

// A failed lazy chunk load must not crash the game screen: fall back to 2D.
class Dice3DBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

export function DiceRenderer({
  value,
  dice,
  eventId,
  appearance = 'amber',
  kind = '2d',
  onSettled,
}: {
  /** Legacy single-d20 prop (demo page); `dice` takes precedence. */
  value?: D20Value
  dice?: readonly DieSpec[]
  eventId: number
  appearance?: DiceStyle
  kind?: DiceRendererKind
  /** 3D only: fires once the throw lands (or fails) — drives the reveal gate. */
  onSettled?: () => void
}) {
  const specs: readonly DieSpec[] = dice ?? (value !== undefined ? [{ sides: 20, value }] : [])
  const dice2d = <Dice2D dice={specs} eventId={eventId} appearance={appearance} />
  if (kind === '2d' || !supports3D(specs)) {
    return <div data-renderer="2d">{dice2d}</div>
  }
  return (
    <div data-renderer="3d">
      <Dice3DBoundary fallback={dice2d}>
        {/* While the 3D chunk loads, show the value without the arrive animation. */}
        <Suspense fallback={<div className="dice-3d-loading">{dice2d}</div>}>
          <Dice3D dice={specs} eventId={eventId} appearance={appearance} onSettled={onSettled} />
        </Suspense>
      </Dice3DBoundary>
    </div>
  )
}