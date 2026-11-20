// Stylized 2D dice for every die the combat journal can show (task #21).
// The protected Codex donor (src/demo/Dice.tsx) is d20-only and stays
// byte-identical: a single d20 still renders through it, everything else
// (d4/d6/d8/d10/d12/d100…, multi-dice rows) uses the simple per-sides
// polygon silhouettes below. Same visual language as the donor: demo.css
// palette variables (--dice-light/mid/dark/edge), .dice-illustration mount
// tumble (key={eventId}) and the .dice-result caption.
import { useTranslation } from 'react-i18next'
import { Dice, type D20Value, type DiceStyle } from '@/demo/Dice'

/** One visual die: the term's sides + the server value (only source of truth). */
export interface DieSpec {
  sides: number
  value: number
}

export function clampD20(value: number): D20Value {
  return Math.max(1, Math.min(20, Math.round(value))) as D20Value
}

// Polygon silhouettes in a 200×200 viewBox: outer hull, lighter inner face,
// facet edges and the value/caption baselines. Pragmatic beta illustrations,
// not exact orthographic projections (the d20 keeps the donor's exact one).
interface DieShape {
  hull: string
  face: string
  edges: string
  valueY: number
  sidesY: number
}

const SHAPES: Record<number, DieShape> = {
  4: {
    hull: '100,18 186,170 14,170',
    face: '100,62 150,150 50,150',
    edges: 'M100 18L100 62M14 170L50 150M186 170L150 150',
    valueY: 122, sidesY: 158,
  },
  6: {
    hull: '36,36 164,36 164,164 36,164',
    face: '62,62 138,62 138,138 62,138',
    edges: 'M36 36L62 62M164 36L138 62M36 164L62 138M164 164L138 138',
    valueY: 106, sidesY: 140,
  },
  8: {
    hull: '100,14 186,100 100,186 14,100',
    face: '100,52 148,100 100,148 52,100',
    edges: 'M100 14L100 52M186 100L148 100M100 186L100 148M14 100L52 100',
    valueY: 104, sidesY: 136,
  },
  10: {
    hull: '100,14 180,84 100,186 20,84',
    face: '100,48 146,86 100,150 54,86',
    edges: 'M100 14L100 48M180 84L146 86M100 186L100 150M20 84L54 86',
    valueY: 98, sidesY: 130,
  },
  12: {
    hull: '100,14 188,78 154,182 46,182 12,78',
    face: '100,52 150,88 131,148 69,148 50,88',
    edges: 'M100 14L100 52M188 78L150 88M154 182L131 148M46 182L69 148M12 78L50 88',
    valueY: 106, sidesY: 140,
  },
}

// d100 and any exotic sides degrade to a generic hexagon.
const GENERIC_SHAPE: DieShape = {
  hull: '100,14 178,57 178,143 100,186 22,143 22,57',
  face: '100,50 148,78 148,122 100,150 52,122 52,78',
  edges: 'M100 14L100 50M178 57L148 78M178 143L148 122M100 186L100 150M22 143L52 122M22 57L52 78',
  valueY: 106, sidesY: 140,
}

// The donor d20 idle silhouette (hexagonal hull + central triangle), kept
// pixel-identical to the #20 pending button.
const D20_IDLE = {
  hull: '100,8 179.67,54 179.67,146 100,192 20.33,146 20.33,54',
  face: '100,43.14 149.24,128.43 50.76,128.43',
  edges: 'M100 8L100 43.14M20.33 54L100 43.14L179.67 54M20.33 54L50.76 128.43L20.33 146M179.67 54L149.24 128.43L179.67 146M50.76 128.43L100 192L149.24 128.43M20.33 146L100 192L179.67 146',
  markY: 112,
}

function shapeFor(sides: number): DieShape {
  return SHAPES[sides] ?? GENERIC_SHAPE
}

/** One stylized non-d20 die; tumbles on mount via key={eventId} like the donor. */
function Die2D({ sides, value, eventId, appearance }: DieSpec & { eventId: number; appearance: DiceStyle }) {
  const shape = shapeFor(sides)
  return (
    <div className={`dice-stage dice-${appearance} dice-2d-stage`}>
      <div className="dice-shadow" aria-hidden="true" />
      <svg key={eventId} className="dice-illustration dice-2d-illustration" viewBox="0 0 200 200" aria-hidden="true">
        <polygon className="dice-facet dice-facet-left" points={shape.hull} />
        <polygon className="dice-facet dice-facet-front" points={shape.face} />
        <path className="dice-2d-edges" d={shape.edges} />
        <text className="dice-face-value dice-2d-value" x="100" y={shape.valueY} textAnchor="middle">{value}</text>
        <text className="dice-2d-sides" x="100" y={shape.sidesY} textAnchor="middle">d{sides}</text>
      </svg>
    </div>
  )
}

/**
 * The whole 2D side of the DiceRenderer seam: a single kept d20 goes through
 * the protected donor unchanged; any other shape renders N stylized dice
 * (multi-dice = clone windows, user decision) plus one result caption.
 */
export function Dice2D({ dice, eventId, appearance }: {
  dice: readonly DieSpec[]
  eventId: number
  appearance: DiceStyle
}) {
  const { t } = useTranslation()
  if (dice.length === 0) return null
  if (dice.length === 1 && dice[0].sides === 20) {
    return <Dice type="d20" value={clampD20(dice[0].value)} eventId={eventId} appearance={appearance} />
  }
  return (
    <div className="dice-2d-group" data-testid="dice-2d">
      <div className="dice-2d-row">
        {dice.map((die, index) => (
          <Die2D key={`${eventId}-${index}`} sides={die.sides} value={die.value} eventId={eventId} appearance={appearance} />
        ))}
      </div>
      <p className="dice-result" aria-live="polite" aria-atomic="true">
        {dice.length === 1
          ? <>{t('game:dice.resultDie', { sides: dice[0].sides })} <strong>{dice[0].value}</strong></>
          : <>{t('game:dice.resultMany')} <strong>{dice.map(die => die.value).join(', ')}</strong></>}
      </p>
    </div>
  )
}

/**
 * Idle "?" die for the click-to-roll gate: the silhouette matches the roll's
 * first die (d20 keeps the exact #20 shape), the server value stays hidden.
 */
export function IdleDie({ sides }: { sides: number }) {
  if (sides === 20) {
    return (
      <svg className="roll-card-idle-die" viewBox="0 0 200 200" aria-hidden="true">
        <polygon className="idle-die-hull" points={D20_IDLE.hull} />
        <polygon className="idle-die-face" points={D20_IDLE.face} />
        <path className="idle-die-edges" d={D20_IDLE.edges} />
        <text className="idle-die-mark" x="100" y={D20_IDLE.markY} textAnchor="middle">?</text>
      </svg>
    )
  }
  const shape = shapeFor(sides)
  return (
    <svg className="roll-card-idle-die" viewBox="0 0 200 200" aria-hidden="true">
      <polygon className="idle-die-hull" points={shape.hull} />
      <polygon className="idle-die-face" points={shape.face} />
      <path className="idle-die-edges" d={shape.edges} />
      <text className="idle-die-mark" x="100" y={shape.valueY + 14} textAnchor="middle">?</text>
    </svg>
  )
}