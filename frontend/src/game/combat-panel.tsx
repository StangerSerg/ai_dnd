import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { EyeOffIcon, ShieldIcon, SwordsIcon } from 'lucide-react'
import { ApiError, portal } from '@/api'
import type { RulesState } from '@/api/events'

// Combat panel (3.4): GET combat-options drives the UI; your_turn shows the
// option buttons, otherwise whose turn it is. POST combat-actions; 409 codes
// get a human-readable text. Refetch happens on combat journal events and
// after our own action (parent invalidates ['combat-options', sessionId]).
export function CombatPanel({ sessionId, rules, myActorId, actorName, downed = false, kicked = false }: {
  sessionId: string
  rules: RulesState | null
  myActorId: string | null
  actorName: (actorId: string | null | undefined) => string
  /** Мой актёр без сознания (#37): на своём ходу доступен только конец хода. */
  downed?: boolean
  /** Кикнутому боевая панель скрыта (долг #26). */
  kicked?: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const combat = rules?.combat ?? null
  const fingerprint = combat ? `${combat.round}:${combat.current}:${combat.order.length}` : 'none'

  const optionsQuery = useQuery({
    queryKey: ['combat-options', sessionId, fingerprint],
    queryFn: () => portal.combatOptions(sessionId),
    enabled: !kicked,
  })

  const mutation = useMutation({
    mutationFn: (optionIndex: number) => portal.combatAction(sessionId, optionIndex),
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['combat-options', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        // 403 может прийти строкой detail ("muted") без машинного code.
        const code = err.code ?? err.detail
        setError(t(`game:combat.errors.${code}`, { defaultValue: err.detail }))
      } else {
        setError(String(err))
      }
    },
  })

  const data = optionsQuery.data
  const inCombat = data?.in_combat ?? Boolean(combat)
  if (!inCombat || kicked) return null

  const currentActor = data?.current_actor_id ?? combat?.current ?? null
  const yourTurn = data?.your_turn ?? (currentActor !== null && currentActor === myActorId)

  const options = data?.options ?? []
  const protectOptions = options.filter(
    (option): option is typeof option & { target_id: string } => option.kind === 'protect' && option.target_id != null,
  )
  // Нет ресурса hp — считаем 0 хитов, такой союзник приоритетнее всех.
  const hpOf = (actorId: string) => rules?.actors[actorId]?.resources?.hp?.current ?? 0
  const quickProtect = protectOptions.reduce<(typeof protectOptions)[number] | null>(
    (best, option) => (best === null || hpOf(option.target_id) < hpOf(best.target_id) ? option : best),
    null,
  )
  const coveredBy = myActorId ? rules?.actors[myActorId]?.protected_by ?? null : null

  return (
    <section className="combat-panel" aria-label={t('game:combat.title')} data-testid="combat-panel">
      <div className="combat-panel-heading">
        <SwordsIcon aria-hidden="true" />
        <h2>{t('game:combat.title')}</h2>
        {combat && <span className="combat-round">{t('game:combat.round', { round: combat.round })}</span>}
      </div>
      {coveredBy && (
        <p className="combat-covered-hint" data-testid="combat-covered-hint">
          <ShieldIcon size={12} aria-hidden="true" /> {t('game:combat.coveredHint', { name: actorName(coveredBy) })}
        </p>
      )}
      {optionsQuery.isPending && <p role="status">{t('loading')}</p>}
      {yourTurn ? (
        <>
          <p className="combat-your-turn">{t('game:combat.yourTurn')}</p>
          {downed && <p className="combat-downed-hint" data-testid="combat-downed-hint">{t('game:downed.combatHint')}</p>}
          {quickProtect && (
            <button
              type="button"
              className="primary-button combat-quick-protect"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(quickProtect.index)}
              data-testid="combat-quick-protect"
            >
              <ShieldIcon size={13} aria-hidden="true" />
              {t('game:combat.quickProtect')} → {actorName(quickProtect.target_id)}
            </button>
          )}
          <div className="combat-options">
            {options.map(option => (
              <button
                key={option.index}
                type="button"
                className={`secondary-button${option.kind === 'hide' || option.kind === 'protect' ? ' combat-option-skill' : ''}`}
                disabled={mutation.isPending}
                onClick={() => mutation.mutate(option.index)}
                data-testid={`combat-option-${option.index}`}
              >
                {option.kind === 'hide' && <EyeOffIcon size={13} aria-hidden="true" />}
                {option.kind === 'protect' && <ShieldIcon size={13} aria-hidden="true" />}
                {option.label}
                {option.target_id ? ` → ${actorName(option.target_id)}` : ''}
              </button>
            ))}
            {options.length === 0 && <p className="combat-empty">{t('game:combat.noOptions')}</p>}
          </div>
        </>
      ) : (
        <p className="combat-waiting" data-testid="combat-waiting">{t('game:combat.waiting', { actor: actorName(currentActor) })}</p>
      )}
      {error && <p className="action-notice" role="alert" data-testid="combat-error">{error}</p>}
    </section>
  )
}