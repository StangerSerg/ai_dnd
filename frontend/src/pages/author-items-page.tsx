// Страница автора «Предметы сценария» (подэтап 7): выбор своего сценария
// (GET /api/author/scenarios), список предметов с механикой, правка через
// ItemForm (PUT публикует новую версию), отчёт issues и сеты монстров.
// Доступ — любой авторизованный: REST сам отдаёт только свои сценарии.
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { QueryError, selectClass, tableClass, tdClass, thClass } from '@/components/admin/common'
import { ItemForm } from '@/components/author/item-form'
import { api, apiErrorMessage, author, type Item, type ItemUpdateResponse } from '@/api'
import { ItemIcon } from '@/game/item-icon'

export function AuthorItemsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const scenariosQuery = useQuery({ queryKey: ['author-scenarios'], queryFn: author.listScenarios })
  const [scenarioId, setScenarioId] = useState('')
  const scenarios = useMemo(() => scenariosQuery.data ?? [], [scenariosQuery.data])
  const activeId = scenarioId || scenarios[0]?.id || ''
  const itemsQuery = useQuery({
    queryKey: ['author-items', activeId],
    queryFn: () => author.getItems(activeId),
    enabled: Boolean(activeId),
  })
  const iconsQuery = useQuery({ queryKey: ['item-icons'], queryFn: api.getItemIcons })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saved, setSaved] = useState<ItemUpdateResponse | null>(null)

  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data])
  const itemsById = useMemo(() => new Map(items.map(item => [item.id, item])), [items])
  const editing = editingId ? itemsById.get(editingId) ?? null : null

  function handleSaved(result: ItemUpdateResponse) {
    setSaved(result)
    setEditingId(null)
    void queryClient.invalidateQueries({ queryKey: ['author-items', activeId] })
    void queryClient.invalidateQueries({ queryKey: ['author-scenarios'] })
  }

  function mechanicsSummary(item: Item): string {
    const parts: string[] = []
    if (item.ac_bonus !== 0) parts.push(t('characters:items.mechanics.ac', { value: item.ac_bonus }))
    if (item.damage_bonus !== 0) parts.push(t('characters:items.mechanics.damage', { value: item.damage_bonus }))
    const effect = item.consumable_effect
    if (effect?.type === 'heal') parts.push(t('characters:items.mechanics.heal', { amount: effect.amount }))
    if (effect?.type === 'buff') parts.push(t('characters:items.mechanics.buff'))
    if (effect?.type === 'spell') parts.push(t('characters:items.mechanics.spell', { spell: effect.spell }))
    if (item.attack) parts.push(t('characters:items.mechanics.attack', { name: item.attack.name }))
    if (item.repair_hp != null) parts.push(t('characters:items.mechanics.repair', { value: item.repair_hp }))
    if (item.ammo) parts.push(t('characters:items.mechanics.ammo', { name: itemsById.get(item.ammo)?.name ?? item.ammo }))
    if (parts.length === 0) parts.push(t('characters:items.mechanics.narrative'))
    return parts.join(' · ')
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10" data-testid="author-items-page">
      <div className="flex flex-col gap-1">
        <h1 className="font-serif text-4xl font-medium">{t('characters:items.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('characters:items.hint')}</p>
      </div>

      {scenariosQuery.isPending ? (
        <p role="status" className="text-muted-foreground">{t('loading')}</p>
      ) : scenariosQuery.isError ? (
        <QueryError error={scenariosQuery.error} onRetry={() => void scenariosQuery.refetch()} testId="author-scenarios-error" />
      ) : scenarios.length === 0 ? (
        <p className="text-muted-foreground" data-testid="author-no-scenarios">{t('characters:items.noScenarios')}</p>
      ) : (
        <>
          <label className="flex max-w-md flex-col gap-1 text-xs font-medium text-muted-foreground">
            {t('characters:items.scenario')}
            <select className={selectClass} value={activeId} data-testid="author-scenario-select"
              onChange={event => { setScenarioId(event.target.value); setEditingId(null); setSaved(null) }}>
              {scenarios.map(scenario => (
                <option key={scenario.id} value={scenario.id}>{scenario.title} ({scenario.language.toUpperCase()})</option>
              ))}
            </select>
          </label>

          {saved && (
            <div role="status" className="rounded-md border border-primary/50 bg-accent px-4 py-3 text-sm" data-testid="author-saved-hint">
              <p>{t('characters:items.savedHint')}</p>
              {saved.issues.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1">
                  {saved.issues.map((issue, index) => (
                    <li key={index}>
                      <span className="font-medium">{t(`characters:items.issueCodes.${issue.code}`, { defaultValue: issue.code })}</span>
                      {' '}<code className="text-xs">{issue.path}</code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {itemsQuery.isPending ? (
            <p role="status" className="text-muted-foreground">{t('loading')}</p>
          ) : itemsQuery.isError ? (
            <QueryError error={itemsQuery.error} onRetry={() => void itemsQuery.refetch()} testId="author-items-error" />
          ) : (
            <>
              <section className="flex flex-col gap-2" aria-label={t('characters:items.listTitle')}>
                <h2 className="font-serif text-2xl">{t('characters:items.listTitle')}</h2>
                {items.length === 0 ? (
                  <p className="text-muted-foreground">{t('characters:items.empty')}</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className={tableClass} data-testid="author-items-table">
                      <thead>
                        <tr>
                          <th className={thClass}>{t('characters:items.fields.icon')}</th>
                          <th className={thClass}>{t('characters:items.fields.name')}</th>
                          <th className={thClass}>{t('characters:items.fields.kind')}</th>
                          <th className={thClass}>{t('characters:items.fields.slot')}</th>
                          <th className={thClass}>{t('characters:items.fields.quantity')}</th>
                          <th className={thClass}>{t('characters:items.fields.enhancement')}</th>
                          <th className={thClass}>{t('characters:items.mechanicsTitle')}</th>
                          <th className={thClass}></th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map(item => (
                          <tr key={item.id} data-testid={`author-item-${item.id}`}>
                            <td className={tdClass}><ItemIcon icon={item.icon} size={28} /></td>
                            <td className={tdClass}>
                              {item.name}
                              {item.visibility === 'dm' && (
                                <span className="ml-2 rounded-full border px-2 py-0.5 text-[10px] uppercase text-muted-foreground">
                                  {t('characters:items.visibility.dm')}
                                </span>
                              )}
                            </td>
                            <td className={tdClass}>{item.kind ? t(`characters:items.kind.${item.kind}`) : '—'}</td>
                            <td className={tdClass}>{item.slot ? t(`game:events.itemSlot.${item.slot}`) : '—'}</td>
                            <td className={tdClass}>×{item.quantity}</td>
                            <td className={tdClass}>{item.enhancement > 0 ? `+${item.enhancement}` : '—'}</td>
                            <td className={`${tdClass} text-xs text-muted-foreground`}>{mechanicsSummary(item)}</td>
                            <td className={tdClass}>
                              <Button variant="outline" size="sm" data-testid={`author-item-edit-${item.id}`}
                                onClick={() => { setEditingId(item.id); setSaved(null) }}>
                                {t('characters:items.edit')}
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {editing && (
                <ItemForm
                  key={editing.id}
                  scenarioId={activeId}
                  item={editing}
                  icons={iconsQuery.data?.icons ?? []}
                  onSaved={handleSaved}
                  onCancel={() => setEditingId(null)}
                />
              )}

              <section className="flex flex-col gap-2" aria-label={t('characters:items.issuesTitle')} data-testid="author-issues">
                <h2 className="font-serif text-2xl">{t('characters:items.issuesTitle')}</h2>
                {(itemsQuery.data?.issues.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground">{t('characters:items.issuesEmpty')}</p>
                ) : (
                  <ul className="flex flex-col gap-1 text-sm">
                    {itemsQuery.data?.issues.map((issue, index) => (
                      <li key={index} className="rounded-md border px-3 py-2">
                        <span className="font-medium">{t(`characters:items.issueCodes.${issue.code}`, { defaultValue: issue.code })}</span>
                        {' '}<code className="text-xs text-muted-foreground">{issue.path}</code>
                        {issue.params && Object.keys(issue.params).length > 0 && (
                          <span className="block text-xs text-muted-foreground">{JSON.stringify(issue.params)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="flex flex-col gap-2" aria-label={t('characters:items.encountersTitle')} data-testid="author-encounters">
                <h2 className="font-serif text-2xl">{t('characters:items.encountersTitle')}</h2>
                {(itemsQuery.data?.encounters.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground">{t('characters:items.encountersEmpty')}</p>
                ) : (
                  <div className="grid gap-3 md:grid-cols-2">
                    {itemsQuery.data?.encounters.map(encounter => (
                      <article key={encounter.id} className="flex flex-col gap-2 rounded-md border p-3">
                        <h3 className="font-serif text-lg">{encounter.title}</h3>
                        <div className="text-sm">
                          <span className="text-xs font-medium uppercase text-muted-foreground">{t('characters:items.creatures')}</span>
                          <ul className="mt-1 flex flex-col gap-0.5">
                            {encounter.creatures.map((creature, index) => (
                              <li key={index}>{creature.name ?? creature.statblock} ×{creature.count}</li>
                            ))}
                          </ul>
                        </div>
                        <div className="text-sm">
                          <span className="text-xs font-medium uppercase text-muted-foreground">{t('characters:items.loot')}</span>
                          {encounter.loot.length === 0 ? (
                            <p className="mt-1 text-muted-foreground">—</p>
                          ) : (
                            <ul className="mt-1 flex flex-col gap-0.5">
                              {encounter.loot.map((entry, index) => (
                                <li key={index} className="flex items-center gap-2">
                                  <ItemIcon icon={itemsById.get(entry.item_id)?.icon} size={18} />
                                  {itemsById.get(entry.item_id)?.name ?? entry.item_id} ×{entry.quantity}
                                  <span className="text-xs text-muted-foreground">
                                    {t('characters:items.chance', { percent: Math.round(entry.chance * 100) })}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </>
      )}
      {iconsQuery.isError && (
        <p role="alert" className="text-sm text-destructive-foreground">{apiErrorMessage(iconsQuery.error)}</p>
      )}
    </div>
  )
}