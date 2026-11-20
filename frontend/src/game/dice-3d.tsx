// 3D dice adapter for stage 3.5 (lazy chunk: imported only by DiceRenderer
// when the effective kind is '3d' on the game screen).
//
// Determinism contract: the server values are the ONLY source of truth. The
// library (@3d-dice/dice-box-threejs) supports predetermined outcomes via the
// "NdS@v1,v2,…" notation (verified against dist: the parser collects every
// number after "@" in order and swapDiceFace forces diceList[i] to result[i],
// with special handling for d4/d10): physics runs honestly, and once the dice
// settle the library swaps the top faces to the server values (resultReason
// "forced"). Every player thus sees the same final faces; trajectories may
// differ. We additionally verify EVERY reported die (sides + value, in order)
// and fall back to the stylized 2D dice on any mismatch or WebGL failure — a
// wrong face is never shown. Multi-dice throws use the ONE singleton stage
// (native NdS notation), never extra WebGL contexts.
//
// One shared WebGL stage for the whole journal: browsers cap live WebGL
// contexts (~8–16), so a DiceBox per roll card would break long sessions.
// The newest roll claims the canvas and animates; older cards keep the static
// 2D illustration with the correct value. The render loop runs only while a
// throw is in flight (no idle rAF), keeping the game screen cheap.
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import DiceBox, { type DiceBoxColorset } from '@3d-dice/dice-box-threejs'
import { Dice2D, type DieSpec } from './dice-2d'
import type { DiceStyle } from '@/demo/Dice'

// Skins mirror the 2D palettes in demo.css.
// texture:'none' → all materials are generated on canvas, no external assets.
const COLORSETS: Record<DiceStyle, DiceBoxColorset> = {
  amber: {
    name: 'aidnd-amber',
    foreground: '#1a1a16',
    background: ['#d4ae71', '#946c3f', '#c49a5f'],
    outline: '#f3e4c4',
    edge: '#e6c991',
    texture: 'none',
    material: 'plastic',
  },
  jade: {
    name: 'aidnd-jade',
    foreground: '#101f18',
    background: ['#95b9a4', '#497d69', '#5f9480'],
    outline: '#b6d6bd',
    edge: '#b6d6bd',
    texture: 'none',
    material: 'plastic',
  },
  amethyst: {
    name: 'aidnd-amethyst',
    foreground: '#1c1424',
    background: ['#b6a3cf', '#745b91', '#8f74ad'],
    outline: '#d6c6e7',
    edge: '#d6c6e7',
    texture: 'none',
    material: 'plastic',
  },
  gold: {
    name: 'aidnd-gold',
    foreground: '#000000',
    background: ['#d4af37', '#b8860b', '#f2d16b'],
    outline: 'none',
    edge: '#f6d978',
    texture: 'none',
    material: 'metal',
  },
  malachite: {
    name: 'aidnd-malachite',
    foreground: '#efffec',
    background: ['#0b6849', '#12805a', '#064b37'],
    outline: '#043d2c',
    edge: '#3cba83',
    texture: 'none',
    material: 'plastic',
  },
  ruby: {
    name: 'aidnd-ruby',
    foreground: '#fff4ed',
    // Alpha belongs to the canvas background, not to the opaque numerals.
    background: ['rgba(170, 14, 45, 0.72)', 'rgba(218, 28, 61, 0.65)', 'rgba(126, 8, 34, 0.82)'],
    outline: '#650c23',
    edge: '#d93355',
    texture: 'none',
    material: 'glass',
  },
}

// --- module-level singleton stage -----------------------------------------
let stage: HTMLDivElement | null = null
let box: DiceBox | null = null
let initPromise: Promise<DiceBox> | null = null
let initFailed = false
let activeSlot: HTMLDivElement | null = null
let activeEventId = Number.NEGATIVE_INFINITY
// Event whose roll was already scheduled/played: re-renders and remounts of
// the same eventId must not replay the throw (mirrors 2D key={eventId}).
let rolledEventId: number | null = null
let appliedAppearance: DiceStyle | null = null
let queue: Promise<void> = Promise.resolve()
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function ensureStage(): HTMLDivElement {
  if (!stage) {
    stage = document.createElement('div')
    stage.id = 'aidnd-dice-3d-stage'
    stage.className = 'dice-3d-canvas'
  }
  return stage
}

/** Newest event wins the canvas; older rolls stay on the 2D fallback. */
function claim(slot: HTMLDivElement, eventId: number): boolean {
  if (initFailed || eventId < activeEventId) return false
  activeEventId = eventId
  activeSlot = slot
  slot.appendChild(ensureStage())
  emit()
  return true
}

function release(slot: HTMLDivElement) {
  if (activeSlot === slot) {
    activeSlot = null
    emit()
  }
}

function ensureBox(appearance: DiceStyle): Promise<DiceBox> {
  if (box) return Promise.resolve(box)
  initPromise ??= (async () => {
    // The stage is already attached to a sized slot (claim ran first).
    const instance = new DiceBox('#aidnd-dice-3d-stage', {
      assetPath: '/',
      sounds: false,
      shadows: true,
      theme_surface: 'green-felt',
      theme_customColorset: COLORSETS[appearance],
      theme_material: COLORSETS[appearance].material,
      strength: 0.7,
    })
    await instance.initialize()
    box = instance
    appliedAppearance = appearance
    return instance
  })().catch((error: unknown) => {
    // No WebGL / context loss: permanent 2D fallback for this page load.
    initFailed = true
    initPromise = null
    emit()
    throw error
  })
  return initPromise
}

/**
 * Forced-outcome notation for the whole roll: consecutive same-sides dice
 * group into one term ("2d6+1d8@3,5,7"). The library applies the values after
 * "@" to its dice list in notation order — the same order we verify below.
 */
function notationFor(dice: readonly DieSpec[]): string {
  const parts: string[] = []
  let i = 0
  while (i < dice.length) {
    let j = i
    while (j < dice.length && dice[j].sides === dice[i].sides) j++
    parts.push(`${j - i}d${dice[i].sides}`)
    i = j
  }
  return `${parts.join('+')}@${dice.map(die => die.value).join(',')}`
}

function scheduleRoll(
  dice: readonly DieSpec[],
  eventId: number,
  appearance: DiceStyle,
  onFail: () => void,
  onSettled: () => void,
) {
  queue = queue.then(async () => {
    if (initFailed || activeEventId !== eventId) {
      // Superseded by a newer roll (or no WebGL): the card shows static 2D.
      onSettled()
      return
    }
    try {
      const instance = await ensureBox(appearance)
      if (activeEventId !== eventId) {
        onSettled()
        return
      }
      if (appliedAppearance !== appearance) {
        // updateConfig is broken in 0.0.12 (Object.apply); load the theme directly.
        instance.theme_customColorset = COLORSETS[appearance]
        instance.theme_material = COLORSETS[appearance].material ?? 'plastic'
        await instance.loadTheme({
          colorset: instance.theme_colorset,
          texture: instance.theme_texture,
          material: instance.theme_material,
        })
        // Cached colorsets share a mutable texture; restore the selected material.
        instance.DiceFactory.dice_material = COLORSETS[appearance].material ?? 'plastic'
        appliedAppearance = appearance
      }
      if (rolledEventId === eventId) {
        // Remount/re-render of an already played throw: never replay it.
        onSettled()
        return
      }
      rolledEventId = eventId
      // Predetermined outcome: every die MUST stop on its server value.
      const results = await instance.roll(notationFor(dice))
      const expected = dice.map(die => `${die.sides}:${die.value}`)
      const rolled = results.sets.flatMap(set => set.rolls.map(roll => `${set.sides}:${roll.value}`))
      if (rolled.length !== expected.length || rolled.some((value, index) => value !== expected[index])) {
        console.warn('[dice-3d] forced result mismatch, falling back to 2D', { expected, rolled })
        onFail()
      }
      onSettled()
    } catch (error) {
      console.warn('[dice-3d] roll failed, falling back to 2D', error)
      onFail()
      onSettled()
    }
  })
}

// --- component -------------------------------------------------------------
export default function Dice3D({ dice, eventId, appearance, onSettled }: {
  dice: readonly DieSpec[]
  eventId: number
  appearance: DiceStyle
  onSettled?: () => void
}) {
  const { t } = useTranslation()
  const slotRef = useRef<HTMLDivElement>(null)
  const [claimed, setClaimed] = useState(false)
  const [failed, setFailed] = useState(false)
  // Stable effect key + latest-value refs: the throw is scheduled once per
  // eventId even though the dice array/callback identities change per render.
  const diceKey = dice.map(die => `${die.sides}:${die.value}`).join(',')
  const diceRef = useRef(dice)
  diceRef.current = dice
  const settleRef = useRef(onSettled)
  settleRef.current = onSettled

  useEffect(() => {
    const slot = slotRef.current
    if (!slot) return
    const sync = () => setClaimed(activeSlot === slot && !initFailed)
    listeners.add(sync)
    let idleTimer: number | undefined
    if (claim(slot, eventId)) {
      scheduleRoll(diceRef.current, eventId, appearance, () => setFailed(true), () => settleRef.current?.())
    } else {
      // Older card / failed init: static 2D illustration. If this card is the
      // rolling gate, let it finish after the 2D arrive animation (~.8s).
      idleTimer = window.setTimeout(() => settleRef.current?.(), 900)
    }
    sync()
    return () => {
      listeners.delete(sync)
      release(slot)
      if (idleTimer !== undefined) window.clearTimeout(idleTimer)
    }
  }, [eventId, diceKey, appearance])

  const active = claimed && !failed
  const single = dice.length === 1 ? dice[0] : null
  const resultLabel = single
    ? (single.sides === 20 ? t('game:dice.result') : t('game:dice.resultDie', { sides: single.sides }))
    : t('game:dice.resultMany')
  return (
    <div className={`dice-stage dice-3d-stage dice-${appearance}`} data-testid="dice-3d">
      {/* Canvas host: keeps fixed dimensions even while hidden so DiceBox can
          measure it on first claim; the canvas itself moves between cards. */}
      <div ref={slotRef} className="dice-3d-host" data-active={active ? 'true' : 'false'} aria-hidden="true" />
      {active ? (
        <p className="dice-result" aria-live="polite" aria-atomic="true">
          {resultLabel} <strong>{dice.map(die => die.value).join(', ')}</strong>
        </p>
      ) : (
        <Dice2D dice={dice} eventId={eventId} appearance={appearance} />
      )}
    </div>
  )
}