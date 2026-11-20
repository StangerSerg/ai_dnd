import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { naturalRoll, type DiceRolled, type DiceTermResult } from '@/api/events'
import { DiceRenderer, supports3D, useEffectiveDiceKind, type DiceStyle, type DieSpec } from './dice-renderer'
import { IdleDie } from './dice-2d'
import { useSettings } from '@/stores/settings'

// Roll card for DiceRolled journal events. Every roll with dice terms gets
// animated dice (3D on capable desktops, stylized 2D otherwise — see
// useEffectiveDiceKind): d20 checks/attacks/initiative AND d6/d8 damage/healing,
// including multi-dice (2d6 → two dice, one per kept server value). Dropped
// dice (advantage/disadvantage) stay in the text terms breakdown only. The
// values come from the server event and are the only source of truth.
//
// Click-to-roll: the player's own live roll first renders in "pending" mode —
// an idle clickable die with no value. Both renderers animate on MOUNT, so the
// click simply defers mounting DiceRenderer until then; "rolling" keeps the
// totals/downstream hidden while the throw plays, then onRolled reveals them.
// In 3D the reveal is driven by the actual throw completion (onSettled), with
// a generous timer as a safety net; in 2D the fixed arrive-animation timer is
// the signal (multi-dice 2D dice all tumble in parallel).

/** Kept dice of every dice term, in notation order — the visual dice specs. */
export function visualDice(event: DiceRolled): DieSpec[] {
  const specs: DieSpec[] = []
  for (const term of event.roll.terms) {
    if (term.kind !== 'dice') continue
    const kept = term.rolls.filter(die => die.kept)
    // Degenerate all-dropped term: mirror naturalRoll's fallback to the first die.
    const shown = kept.length > 0 ? kept : term.rolls.slice(0, 1)
    for (const die of shown) specs.push({ sides: term.sides, value: die.value })
  }
  return specs
}

/** Any roll with at least one die gets the visual + the own-roll click gate. */
export function hasVisualDice(event: DiceRolled): boolean {
  return event.roll.terms.some((term): term is DiceTermResult => term.kind === 'dice' && term.rolls.length > 0)
}

// Reveal timing. 2D arrive is .8s. 3D reveals on actual throw completion
// (onSettled); these timers are only a safety net for lost callbacks — single
// d20 physics settles ~2.5s, multi-dice throws can take longer.
const ROLL_PLAY_MS = { '2d': 900, '3d': 6000, '3d-multi': 8000 } as const

const DICE_STYLES: readonly DiceStyle[] = ['amber', 'jade', 'amethyst', 'gold', 'malachite', 'ruby']

export function RollCard({ event, seq, actorName, pending = false, rolling = false, onReveal, onRolled, rollerStyle }: {
  event: DiceRolled
  seq: number
  actorName: string
  pending?: boolean
  rolling?: boolean
  onReveal?: (seq: number) => void
  onRolled?: (seq: number) => void
  rollerStyle?: string
}) {
  const { t } = useTranslation()
  const diceKind = useEffectiveDiceKind()
  const diceStyle = useSettings(state => state.diceStyle)
  // Roller's chosen skin wins for everyone; own style is the fallback for
  // NPC/self/unknown actors. lessAnimation stays the viewer's own choice.
  const appearance = DICE_STYLES.find(style => style === rollerStyle) ?? diceStyle
  const roll = event.roll
  const natural = naturalRoll(roll)
  const dice = useMemo(() => visualDice(event), [event])
  const hasVisual = dice.length > 0
  const plays3D = diceKind === '3d' && supports3D(dice)
  const playMs = plays3D ? (dice.length > 1 ? ROLL_PLAY_MS['3d-multi'] : ROLL_PLAY_MS['3d']) : ROLL_PLAY_MS['2d']
  const showResult = !pending && !rolling

  useEffect(() => {
    if (!rolling || !onRolled) return
    const timer = window.setTimeout(() => onRolled(seq), playMs)
    return () => window.clearTimeout(timer)
  }, [rolling, seq, playMs, onRolled])

  return (
    <div className={`roll-card${pending ? ' roll-card-pending' : ''}`} data-testid="roll-card"
      data-pending={pending ? 'true' : undefined}>
      {hasVisual && (
        pending ? (
          <button type="button" className="roll-card-idle" onClick={() => onReveal?.(seq)}
            aria-label={t('game:roll.click')} data-testid="roll-dice-button">
            {/* Static idle die: silhouette of the roll's first die, but a "?" —
                the server value must stay hidden until the click. */}
            <IdleDie sides={dice[0].sides} />
            <span className="roll-card-idle-label">{t('game:roll.click')}</span>
          </button>
        ) : (
          <div className="roll-card-dice">
            <DiceRenderer dice={dice} eventId={seq} kind={diceKind} appearance={appearance}
              onSettled={rolling ? () => onRolled?.(seq) : undefined} />
          </div>
        )
      )}
      <div className="roll-card-info">
        <span className="roll-card-kicker">{t('game:roll.title')} · {t(`game:roll.reason.${event.reason}`)}</span>
        <strong>{actorName}</strong>
        {pending ? (
          <p className="roll-card-pending-hint" data-testid="roll-pending-hint">{t('game:roll.pendingHint')}</p>
        ) : (
          <>
            <p className="roll-card-notation">
              {roll.notation}
              {roll.mode !== 'normal' && <em> · {t(`game:roll.mode.${roll.mode}`)}</em>}
            </p>
            {showResult && (
              <>
                <p className="roll-card-total">{t('game:roll.total')}: <strong>{roll.total}</strong>
                  {natural !== null && <> · {t('game:roll.natural')}: <strong>{natural}</strong></>}
                </p>
                <p className="roll-card-terms">
                  {roll.terms.map((term, index) => (
                    <span key={index}>
                      {term.kind === 'dice'
                        ? `${term.notation}: [${term.rolls.map(die => (die.kept ? String(die.value) : `(${die.value})`)).join(', ')}]`
                        : `${term.sign < 0 ? '−' : '+'}${Math.abs(term.value)}${term.label ? ` (${term.label})` : ''}`}
                    </span>
                  ))}
                </p>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}