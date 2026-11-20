import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import type { JournalEntry } from '@/api/events'
import { eventOf } from '@/api/events'
import { useDiceReveal } from '@/stores/dice-reveal'
import { EntryItem, LiveNarration, StoryCard } from './story'
import { hasVisualDice, RollCard } from './roll-card'

// Journal feed: every event type renders by its own shape (mirrors in
// src/api/events.ts). Narrated becomes a story card, DiceRolled a roll card,
// the rest are compact entries. Unknown future types degrade to a generic
// entry instead of breaking the screen.

type NameOf = (actorId: string | null | undefined) => string

function entryText(entry: JournalEntry, t: TFunction, nameOf: NameOf): { label: string; text: string } | null {
  const payload = entry.payload
  switch (entry.type) {
    case 'PlayerActed': {
      const event = eventOf(entry, 'PlayerActed')
      if (!event) break
      return { label: `${t('game:feed.action')} · ${nameOf(event.actor_id)}`, text: event.text }
    }
    case 'ItemGranted': {
      const event = eventOf(entry, 'ItemGranted')
      if (!event) break
      return { label: t('game:feed.item'), text: t('game:events.itemGranted', { name: event.name, actor: nameOf(event.actor_id) }) }
    }
    case 'SceneChanged': {
      const event = eventOf(entry, 'SceneChanged')
      if (!event) break
      return { label: t('game:feed.scene'), text: t('game:events.sceneChanged', { scene: event.scene_id }) }
    }
    case 'Revealed': {
      const event = eventOf(entry, 'Revealed')
      if (!event) break
      return { label: t('game:feed.revealed'), text: t(`game:events.revealed.${event.kind}`, { id: event.entity_id }) }
    }
    case 'NpcAttitudeChanged': {
      const event = eventOf(entry, 'NpcAttitudeChanged')
      if (!event) break
      return { label: t('game:feed.npc'), text: t('game:events.attitude', { npc: event.npc_id, attitude: t(`game:attitude.${event.attitude}`) }) }
    }
    case 'NpcMoved': {
      const event = eventOf(entry, 'NpcMoved')
      if (!event) break
      return { label: t('game:feed.npc'), text: t('game:events.npcMoved', { npc: event.npc_id, scene: event.scene_id }) }
    }
    case 'NpcDied': {
      const event = eventOf(entry, 'NpcDied')
      if (!event) break
      return { label: t('game:feed.npc'), text: t('game:events.npcDied', { npc: event.npc_id }) }
    }
    case 'ActorsJoined': {
      const event = eventOf(entry, 'ActorsJoined')
      if (!event) break
      return { label: t('game:feed.joined'), text: event.actors.map(actor => actor.name).join(', ') }
    }
    case 'EncounterStarted': {
      const event = eventOf(entry, 'EncounterStarted')
      if (!event) break
      return { label: t('game:feed.encounter'), text: t('game:events.encounterStarted', { id: event.encounter_id }) }
    }
    case 'CombatStarted':
      return { label: t('game:feed.combat'), text: t('game:events.combatStarted') }
    case 'PartyDefeated':
      return { label: t('game:defeat.title'), text: t('game:defeat.marker') }
    case 'CombatEnded': {
      const event = eventOf(entry, 'CombatEnded')
      if (!event) break
      return { label: t('game:feed.combat'), text: event.winner ? t('game:events.combatEndedWinner', { side: event.winner }) : t('game:events.combatEnded') }
    }
    case 'RoundStarted': {
      const event = eventOf(entry, 'RoundStarted')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.roundStarted', { round: event.round }) }
    }
    case 'TurnStarted': {
      const event = eventOf(entry, 'TurnStarted')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.turnStarted', { actor: nameOf(event.actor_id), round: event.round }) }
    }
    case 'TurnEnded': {
      const event = eventOf(entry, 'TurnEnded')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.turnEnded', { actor: nameOf(event.actor_id) }) }
    }
    case 'TurnSlotUsed': {
      const event = eventOf(entry, 'TurnSlotUsed')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.slotUsed', { actor: nameOf(event.actor_id), slot: t(`game:slot.${event.slot}`), label: event.label ?? '' }) }
    }
    case 'CheckResolved': {
      const event = eventOf(entry, 'CheckResolved')
      if (!event) break
      const target = event.target !== null ? String(event.target) : event.difficulty ? t(`game:difficulty.${event.difficulty}`) : '—'
      return {
        label: `${t('game:feed.check')} · ${nameOf(event.actor_id)}`,
        text: t('game:events.checkResolved', {
          check: event.check,
          total: event.total ?? '—',
          target,
          outcome: t(`game:outcome.${event.outcome}`),
        }) + (event.auto_failed_by ? ` (${event.auto_failed_by})` : ''),
      }
    }
    case 'ContestResolved': {
      const event = eventOf(entry, 'ContestResolved')
      if (!event) break
      return {
        label: t('game:feed.contest'),
        text: t('game:events.contestResolved', {
          actor: nameOf(event.actor_id), check: event.check, total: event.total ?? '—',
          opponent: nameOf(event.opponent_id), opponentCheck: event.opponent_check, opponentTotal: event.opponent_total ?? '—',
          winner: event.winner_id ? nameOf(event.winner_id) : t('game:events.contestDraw'),
        }),
      }
    }
    case 'AttackResolved': {
      const event = eventOf(entry, 'AttackResolved')
      if (!event) break
      return {
        label: `${t('game:feed.attack')} · ${nameOf(event.attacker_id)}`,
        text: t('game:events.attackResolved', {
          target: nameOf(event.target_id), attack: event.attack_name, total: event.total,
          ac: event.target_ac, outcome: t(`game:outcome.${event.outcome}`),
        }) + (event.redirected_to_id ? t('game:events.attackIntercepted', { protector: nameOf(event.redirected_to_id) }) : ''),
      }
    }
    case 'DamageDealt': {
      const event = eventOf(entry, 'DamageDealt')
      if (!event) break
      return { label: t('game:feed.damage'), text: t('game:events.damageDealt', { target: nameOf(event.target_id), amount: event.amount, hp: event.hp_after }) }
    }
    case 'Healed': {
      const event = eventOf(entry, 'Healed')
      if (!event) break
      return { label: t('game:feed.heal'), text: t('game:events.healed', { target: nameOf(event.target_id), amount: event.amount, hp: event.hp_after }) }
    }
    case 'ActorDied': {
      const event = eventOf(entry, 'ActorDied')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.actorDied', { actor: nameOf(event.actor_id) }) }
    }
    case 'ActorFled': {
      const event = eventOf(entry, 'ActorFled')
      if (!event) break
      return { label: t('game:feed.combat'), text: t('game:events.actorFled', { actor: nameOf(event.actor_id) }) }
    }
    case 'ProtectionStarted': {
      const event = eventOf(entry, 'ProtectionStarted')
      if (!event) break
      return { label: t('game:feed.protection'), text: t('game:events.protectionStarted', { protector: nameOf(event.protector_id), target: nameOf(event.target_id) }) }
    }
    case 'ProtectionEnded': {
      const event = eventOf(entry, 'ProtectionEnded')
      if (!event) break
      return { label: t('game:feed.protection'), text: t(`game:events.protectionEnded.${event.reason}`, { protector: nameOf(event.protector_id), target: nameOf(event.target_id) }) }
    }
    case 'ResourceChanged': {
      const event = eventOf(entry, 'ResourceChanged')
      if (!event) break
      return { label: t('game:feed.resource'), text: t('game:events.resourceChanged', { actor: nameOf(event.actor_id), resource: event.resource, delta: event.delta > 0 ? `+${event.delta}` : String(event.delta), current: event.current }) }
    }
    case 'ConditionApplied': {
      const event = eventOf(entry, 'ConditionApplied')
      if (!event) break
      return { label: t('game:feed.condition'), text: t('game:events.conditionApplied', { actor: nameOf(event.actor_id), condition: event.condition }) }
    }
    case 'ConditionRemoved': {
      const event = eventOf(entry, 'ConditionRemoved')
      if (!event) break
      return { label: t('game:feed.condition'), text: t('game:events.conditionRemoved', { actor: nameOf(event.actor_id), condition: event.condition }) }
    }
    case 'TimePassed': {
      const event = eventOf(entry, 'TimePassed')
      if (!event) break
      return { label: t('game:feed.time'), text: t('game:events.timePassed', { rounds: event.rounds }) }
    }
    case 'Rested': {
      const event = eventOf(entry, 'Rested')
      if (!event) break
      return { label: t('game:feed.rest'), text: t(`game:events.rested.${event.rest}`, { actors: event.actor_ids.map(nameOf).join(', ') }) }
    }
    case 'FactNoted':
    case 'SummaryUpdated':
      // dm-only events; players never receive them (server visibility filter).
      return { label: t('game:feed.dm'), text: String(payload.text ?? '') }
    default:
      break
  }
  return null
}

export function EventFeed({ entries, liveText, actorName, sessionId, myActorId = null, diceStyleOf }: {
  entries: JournalEntry[]
  liveText: string
  actorName: NameOf
  sessionId: string
  myActorId?: string | null
  diceStyleOf?: (actorId: string) => string | undefined
}) {
  const { t } = useTranslation()
  const revealSessionId = useDiceReveal(state => state.sessionId)
  const revealed = useDiceReveal(state => state.revealed)
  const rolling = useDiceReveal(state => state.rolling)
  const startRoll = useDiceReveal(state => state.startRoll)
  const finishRoll = useDiceReveal(state => state.finishRoll)

  // Click-to-roll gate (local player only): stop at the first unrevealed own
  // live roll with dice (d20 checks/attacks AND damage/healing, multi-dice
  // included) — everything after it (and the streaming narration) stays held
  // until the click and the throw finish. Other actors' rolls auto-reveal.
  // Until the session's history is seeded (first snapshot), render everything
  // revealed to avoid a false gate.
  const gateActive = Boolean(myActorId) && revealSessionId === sessionId
  let gateSeq: number | null = null
  const visible: JournalEntry[] = []
  for (const entry of entries) {
    visible.push(entry)
    if (gateActive && entry.type === 'DiceRolled' && !revealed.has(entry.seq)) {
      const event = eventOf(entry, 'DiceRolled')
      if (event && event.actor_id === myActorId && hasVisualDice(event)) {
        gateSeq = entry.seq
        break
      }
    }
  }
  // Newest-first: reverse the gated slice so the latest event (and the
  // pending own roll) sits at the top, right under the controls.
  const ordered = [...visible].reverse()
  return (
    <div className="event-feed" data-testid="event-feed">
      {gateSeq === null && liveText && <LiveNarration title={t('game:feed.dm')} note={t('game:feed.streaming')} text={liveText} />}
      {ordered.map(entry => {
        if (entry.type === 'Narrated') {
          const event = eventOf(entry, 'Narrated')
          if (event) return <StoryCard key={entry.seq} title={t('game:feed.dm')} note={t('game:feed.narration')} text={event.text} />
        }
        if (entry.type === 'DiceRolled') {
          const event = eventOf(entry, 'DiceRolled')
          if (event) return <RollCard key={entry.seq} event={event} seq={entry.seq} actorName={actorName(event.actor_id)}
            rollerStyle={event.actor_id ? diceStyleOf?.(event.actor_id) : undefined}
            pending={entry.seq === gateSeq && rolling !== gateSeq}
            rolling={entry.seq === gateSeq && rolling === gateSeq}
            onReveal={startRoll} onRolled={finishRoll} />
        }
        const rendered = entryText(entry, t, actorName)
        if (rendered) return <EntryItem key={entry.seq} label={rendered.label} text={rendered.text} />
        // Unknown/future event type: keep it visible instead of dropping.
        return <EntryItem key={entry.seq} label={`${t('game:feed.event')} · ${entry.type}`} text={JSON.stringify(entry.payload)} />
      })}
    </div>
  )
}