// Кукла (paper doll) + рюкзак игрока (подэтап 7). Надетое — из
// state.equipment[actor_id] ({item_id: slot}), определения — из item-catalog,
// стеки — из state.inventory[actor_id]. Действия шлют POST item-action; успех
// инвалидирует снапшот сессии (существующий механизм рефетча), ошибки
// локализуются по машинному коду. В бою кнопки заблокированы (combat_active).
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ApiError, api, apiErrorMessage, type Item, type ItemActionRequest, type SessionStateView } from '@/api'
import type { ItemSlot } from '@/api/events'
import { ItemIcon } from './item-icon'

const DOLL_SLOTS: ItemSlot[] = ['weapon', 'armor', 'amulet', 'ring', 'cloak']

export interface RepairTarget {
  id: string
  name: string
}

interface InventoryEntry {
  item_id: string | null
  name: string
  quantity?: number
  enhancement?: number
}

/** Стек и заточка записи: ×N (quantity>1) и +N (enhancement>0). */
function EntryMarks({ entry }: { entry: InventoryEntry }) {
  const quantity = entry.quantity ?? 1
  const enhancement = entry.enhancement ?? 0
  return (
    <>
      {quantity > 1 && <span className="item-quantity">×{quantity}</span>}
      {enhancement > 0 && <span className="item-enhancement">+{enhancement}</span>}
    </>
  )
}

export function InventoryPanel({ sessionId, actorId, state, inCombat, disabled, targets }: {
  sessionId: string
  actorId: string
  state: SessionStateView
  /** Бой: предметные действия недоступны (409 combat_active). */
  inCombat: boolean
  /** Кик/остановленный стол/нокаут: кнопки заблокированы. */
  disabled: boolean
  /** Живые члены партии для spend-ремонта (target_id). */
  targets: RepairTarget[]
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const catalogQuery = useQuery({
    queryKey: ['item-catalog', sessionId],
    queryFn: () => api.getItemCatalog(sessionId),
    refetchOnWindowFocus: false,
  })
  const itemsById = useMemo(
    () => new Map<string, Item>((catalogQuery.data?.items ?? []).map(item => [item.id, item])),
    [catalogQuery.data],
  )
  const equipped = state.equipment?.[actorId] ?? {}
  const inventory = state.inventory[actorId] ?? []
  const [error, setError] = useState<string | null>(null)
  const [spendForm, setSpendForm] = useState<{ itemId: string; quantity: number; targetId: string } | null>(null)

  const mutation = useMutation({
    mutationFn: (body: ItemActionRequest) => api.itemAction(sessionId, body),
    onSuccess: () => {
      setError(null)
      setSpendForm(null)
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['combat-options', sessionId] })
      // Каталог фильтруется по инвентарю: новые предметы (в т.ч. visibility="dm")
      // должны появиться в определениях после действия.
      void queryClient.invalidateQueries({ queryKey: ['item-catalog', sessionId] })
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        const code = err.code ?? err.detail
        setError(t(`game:items.errors.${code}`, { defaultValue: apiErrorMessage(err) }))
      } else {
        setError(t('game:items.errors.network'))
      }
    },
  })

  const busy = mutation.isPending
  const blocked = inCombat || disabled || busy
  const blockedTitle = inCombat ? t('game:items.combatHint') : undefined

  function act(body: ItemActionRequest) {
    setError(null)
    mutation.mutate(body)
  }

  function openSpend(entry: InventoryEntry) {
    if (!entry.item_id) return
    setError(null)
    setSpendForm({ itemId: entry.item_id, quantity: 1, targetId: targets[0]?.id ?? '' })
  }

  // Тумблер механики: items_enabled=false — кукла и рюкзак полностью скрыты.
  if (catalogQuery.data && !catalogQuery.data.items_enabled) return null

  const equippedInSlot = (slot: ItemSlot): string | undefined =>
    Object.entries(equipped).find(([, value]) => value === slot)?.[0]
  const otherEquipped = Object.entries(equipped).filter(([, slot]) => slot === 'other')

  function unequipButton(itemId: string) {
    return (
      <button type="button" className="item-action-button" disabled={blocked} title={blockedTitle}
        data-testid={`item-unequip-${itemId}`} onClick={() => act({ action: 'unequip', item_id: itemId, quantity: 1 })}>
        {t('game:items.unequip')}
      </button>
    )
  }

  return (
    <section className="inventory-panel" aria-label={t('game:items.title')} data-testid="inventory-panel">
      <div className="aside-heading"><span>{t('game:items.title')}</span><span>✧</span></div>
      {inCombat && <p className="items-combat-hint" role="status" data-testid="items-combat-hint">{t('game:items.combatHint')}</p>}

      <h3 className="inventory-subheading">{t('game:items.equipment')}</h3>
      <div className="paper-doll">
        {DOLL_SLOTS.map(slot => {
          const itemId = equippedInSlot(slot)
          const def = itemId ? itemsById.get(itemId) : undefined
          // Надетый предмет остаётся в инвентаре: актуальная заточка (ItemEnhanced)
          // — в записи инвентаря, базовая из каталога — только фолбэк.
          const entry = itemId ? inventory.find(candidate => candidate.item_id === itemId) : undefined
          const enhancement = entry?.enhancement ?? def?.enhancement ?? 0
          return (
            <div key={slot} className={`doll-slot${itemId ? ' doll-slot-filled' : ''}`} data-testid={`doll-slot-${slot}`}>
              <span className="doll-slot-label">{t(`game:events.itemSlot.${slot}`)}</span>
              {itemId ? (
                <div className="doll-slot-item">
                  <ItemIcon icon={def?.icon} size={26} />
                  <span className="item-name">
                    {def?.name ?? itemId}
                    {enhancement > 0 && <span className="item-enhancement">+{enhancement}</span>}
                  </span>
                  {unequipButton(itemId)}
                </div>
              ) : (
                <span className="doll-slot-empty">{t('game:items.emptySlot')}</span>
              )}
            </div>
          )
        })}
        <div className={`doll-slot doll-slot-other${otherEquipped.length ? ' doll-slot-filled' : ''}`} data-testid="doll-slot-other">
          <span className="doll-slot-label">{t('game:events.itemSlot.other')}</span>
          {otherEquipped.length === 0 ? (
            <span className="doll-slot-empty">{t('game:items.emptySlot')}</span>
          ) : otherEquipped.map(([itemId]) => {
            const def = itemsById.get(itemId)
            const entry = inventory.find(candidate => candidate.item_id === itemId)
            const enhancement = entry?.enhancement ?? def?.enhancement ?? 0
            return (
              <div key={itemId} className="doll-slot-item">
                <ItemIcon icon={def?.icon} size={26} />
                <span className="item-name">
                  {def?.name ?? itemId}
                  {enhancement > 0 && <span className="item-enhancement">+{enhancement}</span>}
                </span>
                {unequipButton(itemId)}
              </div>
            )
          })}
        </div>
      </div>

      <h3 className="inventory-subheading">{t('game:items.inventory')}</h3>
      {catalogQuery.isPending ? (
        <p className="items-note" role="status">{t('game:items.catalogLoading')}</p>
      ) : catalogQuery.isError ? (
        <p className="items-note" role="alert">
          {t('game:items.catalogError')}{' '}
          <button type="button" className="item-action-button" onClick={() => void catalogQuery.refetch()}>
            {t('retry')}
          </button>
        </p>
      ) : inventory.length === 0 ? (
        <p className="items-note">{t('game:items.emptyInventory')}</p>
      ) : (
        <ul className="inventory-list">
          {inventory.map((entry, index) => {
            const def = entry.item_id ? itemsById.get(entry.item_id) : undefined
            const itemId = entry.item_id
            const isEquipped = itemId !== null && equipped[itemId] !== undefined
            const form = spendForm && itemId !== null && spendForm.itemId === itemId ? spendForm : null
            const maxQuantity = entry.quantity ?? 1
            return (
              <li key={itemId ?? `${entry.name}-${index}`} className="inventory-row" data-testid={`inventory-item-${itemId ?? entry.name}`}>
                <div className="inventory-row-main">
                  <ItemIcon icon={def?.icon} size={26} />
                  <span className="item-name">{entry.name}</span>
                  <EntryMarks entry={entry} />
                  <span className="inventory-row-actions">
                    {itemId && def?.slot && !isEquipped && (
                      <button type="button" className="item-action-button" disabled={blocked} title={blockedTitle}
                        data-testid={`item-equip-${itemId}`} onClick={() => act({ action: 'equip', item_id: itemId, quantity: 1 })}>
                        {t('game:items.equip')}
                      </button>
                    )}
                    {itemId && isEquipped && unequipButton(itemId)}
                    {itemId && def?.consumable_effect && (
                      <button type="button" className="item-action-button" disabled={blocked} title={blockedTitle}
                        data-testid={`item-use-${itemId}`} onClick={() => act({ action: 'use', item_id: itemId, quantity: 1 })}>
                        {t('game:items.use')}
                      </button>
                    )}
                    {itemId && def?.consume_on === 'spend' && (
                      <button type="button" className="item-action-button" disabled={blocked} title={blockedTitle}
                        data-testid={`item-spend-${itemId}`} onClick={() => openSpend(entry)}>
                        {t('game:items.spend')}
                      </button>
                    )}
                  </span>
                </div>
                {form && itemId && (
                  <div className="spend-form" data-testid={`spend-form-${itemId}`}>
                    <label className="spend-field">
                      <span>{t('game:items.quantity')}</span>
                      <input type="number" min={1} max={maxQuantity} value={form.quantity}
                        data-testid="spend-quantity"
                        onChange={event => setSpendForm({ ...form, quantity: Math.max(1, Math.min(maxQuantity, Number(event.target.value) || 1)) })} />
                    </label>
                    {def?.repair_hp != null && (
                      <label className="spend-field">
                        <span>{t('game:items.target')}</span>
                        <select value={form.targetId} data-testid="spend-target"
                          onChange={event => setSpendForm({ ...form, targetId: event.target.value })}>
                          {targets.length === 0 && <option value="">{t('game:items.noTargets')}</option>}
                          {targets.map(target => <option key={target.id} value={target.id}>{target.name}</option>)}
                        </select>
                      </label>
                    )}
                    <span className="spend-actions">
                      <button type="button" className="item-action-button item-action-primary"
                        disabled={blocked || (def?.repair_hp != null && !form.targetId)}
                        data-testid="spend-confirm"
                        onClick={() => act({
                          action: 'spend',
                          item_id: itemId,
                          quantity: form.quantity,
                          target_id: def?.repair_hp != null ? form.targetId : null,
                        })}>
                        {t('game:items.confirm')}
                      </button>
                      <button type="button" className="item-action-button" data-testid="spend-cancel"
                        onClick={() => setSpendForm(null)}>
                        {t('game:items.cancel')}
                      </button>
                    </span>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {error && <p className="moderation-error" role="alert" data-testid="items-error">{error}</p>}
    </section>
  )
}