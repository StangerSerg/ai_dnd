// Форма правки предмета сценария (подэтап 7): поля PUT /api/author/scenarios/
// {id}/items/{item_id}. Отправляются все поля формы: пустой выбор = явный null
// (сброс), заполненный = новое значение. modifiers/buff effects — JSON-textarea
// с валидацией. 422 invalid_item/invalid_scenario показывает issues автора.
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { selectClass } from '@/components/admin/common'
import {
  AuthorItemValidationError,
  apiErrorMessage,
  author,
  type Item,
  type ItemConsumeOn,
  type ItemConsumableEffect,
  type ItemEffect,
  type ItemIconInfo,
  type ItemKind,
  type ItemSlotId,
  type ItemUpdateRequest,
  type ItemUpdateResponse,
  type IssueInfo,
} from '@/api'
import { itemIconUrl } from '@/game/item-icon'

const inputClass =
  'h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none'
const textareaClass =
  'w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none font-mono'
const labelClass = 'flex flex-col gap-1 text-xs font-medium text-muted-foreground'

const KINDS: ItemKind[] = ['weapon', 'armor', 'wearable', 'consumable', 'other']
const SLOTS: ItemSlotId[] = ['weapon', 'armor', 'amulet', 'ring', 'cloak', 'other']
const CONSUME_ON: ItemConsumeOn[] = ['use', 'attack', 'throw', 'spend']

function parseIntOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isInteger(parsed) ? parsed : null
}

/** JSON-массив эффектов; бросает Error с локализуемым ключом при невалидности. */
function parseEffects(value: string): ItemEffect[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error('invalidJson')
  }
  if (!Array.isArray(parsed) || parsed.some(entry => typeof entry !== 'object' || entry === null || Array.isArray(entry))) {
    throw new Error('invalidJson')
  }
  return parsed as ItemEffect[]
}

export function ItemForm({ scenarioId, item, icons, onSaved, onCancel }: {
  scenarioId: string
  item: Item
  icons: ItemIconInfo[]
  onSaved: (result: ItemUpdateResponse) => void
  onCancel: () => void
}) {
  const { t, i18n } = useTranslation()
  const effect = item.consumable_effect ?? null
  const [name, setName] = useState(item.name)
  const [description, setDescription] = useState(item.description ?? '')
  const [visibility, setVisibility] = useState<'public' | 'dm'>(item.visibility)
  const [kind, setKind] = useState<'' | ItemKind>(item.kind ?? '')
  const [slot, setSlot] = useState<'' | ItemSlotId>(item.slot ?? '')
  const [quantity, setQuantity] = useState(String(item.quantity))
  const [acBonus, setAcBonus] = useState(String(item.ac_bonus))
  const [damageBonus, setDamageBonus] = useState(String(item.damage_bonus))
  const [damageType, setDamageType] = useState(item.damage_type ?? '')
  const [enhancement, setEnhancement] = useState(String(item.enhancement))
  const [consumeOn, setConsumeOn] = useState<'' | ItemConsumeOn>(item.consume_on ?? '')
  const [effectType, setEffectType] = useState<'' | ItemConsumableEffect['type']>(effect?.type ?? '')
  const [healAmount, setHealAmount] = useState(effect?.type === 'heal' ? effect.amount : '')
  const [buffEffects, setBuffEffects] = useState(effect?.type === 'buff' ? JSON.stringify(effect.effects, null, 2) : '[]')
  const [buffRounds, setBuffRounds] = useState(effect?.type === 'buff' && effect.rounds != null ? String(effect.rounds) : '')
  const [spell, setSpell] = useState(effect?.type === 'spell' ? effect.spell : '')
  const [ammo, setAmmo] = useState(item.ammo ?? '')
  const [attackName, setAttackName] = useState(item.attack?.name ?? '')
  const [attackBonus, setAttackBonus] = useState(String(item.attack?.bonus ?? 0))
  const [attackDamage, setAttackDamage] = useState(item.attack?.damage ?? '')
  const [attackDamageType, setAttackDamageType] = useState(item.attack?.damage_type ?? '')
  const [repairHp, setRepairHp] = useState(item.repair_hp != null ? String(item.repair_hp) : '')
  const [icon, setIcon] = useState(item.icon ?? '')
  const [modifiers, setModifiers] = useState(JSON.stringify(item.modifiers, null, 2))
  const [validation, setValidation] = useState<string | null>(null)
  const [issues, setIssues] = useState<IssueInfo[]>([])

  const mutation = useMutation({
    mutationFn: (body: ItemUpdateRequest) => author.updateItem(scenarioId, item.id, body),
    onSuccess: result => {
      setValidation(null)
      setIssues([])
      onSaved(result)
    },
    onError: (error: unknown) => {
      if (error instanceof AuthorItemValidationError) {
        setValidation(t(`characters:items.errors.${error.code}`, { defaultValue: error.detail }))
        setIssues(error.issues)
      } else {
        setValidation(apiErrorMessage(error))
        setIssues([])
      }
    },
  })

  function buildBody(): ItemUpdateRequest | null {
    if (!name.trim()) {
      setValidation(t('characters:items.validation.nameRequired'))
      return null
    }
    const quantityValue = parseIntOrNull(quantity)
    if (quantityValue === null || quantityValue < 1) {
      setValidation(t('characters:items.validation.quantity'))
      return null
    }
    const enhancementValue = parseIntOrNull(enhancement)
    if (enhancementValue === null || enhancementValue < 0 || enhancementValue > 3) {
      setValidation(t('characters:items.validation.enhancement'))
      return null
    }
    const acValue = parseIntOrNull(acBonus)
    const damageValue = parseIntOrNull(damageBonus)
    if (acValue === null || damageValue === null) {
      setValidation(t('characters:items.validation.integer'))
      return null
    }
    const repairValue = repairHp.trim() ? parseIntOrNull(repairHp) : null
    if (repairHp.trim() && repairValue === null) {
      setValidation(t('characters:items.validation.integer'))
      return null
    }
    let modifiersValue: ItemEffect[]
    let buffEffectsValue: ItemEffect[] = []
    try {
      modifiersValue = parseEffects(modifiers)
      if (effectType === 'buff') buffEffectsValue = parseEffects(buffEffects)
    } catch {
      setValidation(t('characters:items.validation.invalidJson'))
      return null
    }
    let consumable: ItemUpdateRequest['consumable_effect'] = null
    if (effectType === 'heal') {
      if (!healAmount.trim()) {
        setValidation(t('characters:items.validation.healAmount'))
        return null
      }
      consumable = { type: 'heal', amount: healAmount.trim() }
    } else if (effectType === 'buff') {
      const roundsValue = buffRounds.trim() ? parseIntOrNull(buffRounds) : null
      if (buffRounds.trim() && (roundsValue === null || roundsValue < 1)) {
        setValidation(t('characters:items.validation.integer'))
        return null
      }
      consumable = { type: 'buff', effects: buffEffectsValue, rounds: roundsValue }
    } else if (effectType === 'spell') {
      if (!spell.trim()) {
        setValidation(t('characters:items.validation.spell'))
        return null
      }
      consumable = { type: 'spell', spell: spell.trim() }
    }
    const attackBonusValue = parseIntOrNull(attackBonus) ?? 0
    return {
      name: name.trim(),
      description: description.trim() || null,
      visibility,
      kind: kind || null,
      slot: slot || null,
      quantity: quantityValue,
      modifiers: modifiersValue,
      ac_bonus: acValue,
      damage_bonus: damageValue,
      damage_type: damageType.trim() || null,
      enhancement: enhancementValue,
      consumable_effect: consumable,
      consume_on: consumeOn || null,
      ammo: ammo.trim() || null,
      attack: attackName.trim()
        ? {
            name: attackName.trim(),
            bonus: attackBonusValue,
            damage: attackDamage.trim() || null,
            damage_type: attackDamageType.trim() || null,
          }
        : null,
      repair_hp: repairValue,
      icon: icon || null,
    }
  }

  function save() {
    setIssues([])
    const body = buildBody()
    if (!body) return
    setValidation(null)
    mutation.mutate(body)
  }

  const iconLanguage = i18n.language === 'ru' ? 'ru' : 'en'

  return (
    <div className="flex flex-col gap-4 rounded-md border p-4" data-testid="item-form">
      <h3 className="font-serif text-xl">{t('characters:items.editTitle', { name: item.name })}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className={labelClass}>
          {t('characters:items.fields.name')}
          <input className={inputClass} value={name} onChange={event => setName(event.target.value)} data-testid="item-field-name" />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.visibility')}
          <select className={selectClass} value={visibility} onChange={event => setVisibility(event.target.value as 'public' | 'dm')}>
            <option value="public">{t('characters:items.visibility.public')}</option>
            <option value="dm">{t('characters:items.visibility.dm')}</option>
          </select>
        </label>
        <label className={`${labelClass} sm:col-span-2`}>
          {t('characters:items.fields.description')}
          <textarea className={textareaClass} rows={2} value={description} onChange={event => setDescription(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.kind')}
          <select className={selectClass} value={kind} onChange={event => setKind(event.target.value as '' | ItemKind)} data-testid="item-field-kind">
            <option value="">{t('characters:items.none')}</option>
            {KINDS.map(value => <option key={value} value={value}>{t(`characters:items.kind.${value}`)}</option>)}
          </select>
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.slot')}
          <select className={selectClass} value={slot} onChange={event => setSlot(event.target.value as '' | ItemSlotId)} data-testid="item-field-slot">
            <option value="">{t('characters:items.none')}</option>
            {SLOTS.map(value => <option key={value} value={value}>{t(`game:events.itemSlot.${value}`)}</option>)}
          </select>
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.quantity')}
          <input className={inputClass} type="number" min={1} value={quantity} onChange={event => setQuantity(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.enhancement')}
          <input className={inputClass} type="number" min={0} max={3} value={enhancement} onChange={event => setEnhancement(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.acBonus')}
          <input className={inputClass} type="number" value={acBonus} onChange={event => setAcBonus(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.damageBonus')}
          <input className={inputClass} type="number" value={damageBonus} onChange={event => setDamageBonus(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.damageType')}
          <input className={inputClass} value={damageType} onChange={event => setDamageType(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.repairHp')}
          <input className={inputClass} type="number" min={1} value={repairHp} onChange={event => setRepairHp(event.target.value)} data-testid="item-field-repair" />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.consumeOn')}
          <select className={selectClass} value={consumeOn} onChange={event => setConsumeOn(event.target.value as '' | ItemConsumeOn)}>
            <option value="">{t('characters:items.none')}</option>
            {CONSUME_ON.map(value => <option key={value} value={value}>{t(`characters:items.consumeOn.${value}`)}</option>)}
          </select>
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.ammo')}
          <input className={inputClass} value={ammo} onChange={event => setAmmo(event.target.value)} />
        </label>
        <label className={labelClass}>
          {t('characters:items.fields.consumableEffect')}
          <select className={selectClass} value={effectType} data-testid="item-field-effect"
            onChange={event => setEffectType(event.target.value as '' | ItemConsumableEffect['type'])}>
            <option value="">{t('characters:items.none')}</option>
            <option value="heal">{t('characters:items.effectType.heal')}</option>
            <option value="buff">{t('characters:items.effectType.buff')}</option>
            <option value="spell">{t('characters:items.effectType.spell')}</option>
          </select>
        </label>
        {effectType === 'heal' && (
          <label className={labelClass}>
            {t('characters:items.fields.healAmount')}
            <input className={inputClass} value={healAmount} placeholder="2d4+2" onChange={event => setHealAmount(event.target.value)} />
          </label>
        )}
        {effectType === 'buff' && (
          <>
            <label className={labelClass}>
              {t('characters:items.fields.buffRounds')}
              <input className={inputClass} type="number" min={1} value={buffRounds} onChange={event => setBuffRounds(event.target.value)} />
            </label>
            <label className={`${labelClass} sm:col-span-2`}>
              {t('characters:items.fields.buffEffects')}
              <textarea className={textareaClass} rows={4} value={buffEffects} onChange={event => setBuffEffects(event.target.value)} />
            </label>
          </>
        )}
        {effectType === 'spell' && (
          <label className={labelClass}>
            {t('characters:items.fields.spell')}
            <input className={inputClass} value={spell} onChange={event => setSpell(event.target.value)} />
          </label>
        )}
      </div>

      <fieldset className="flex flex-col gap-2 rounded-md border p-3">
        <legend className="px-1 text-xs font-medium text-muted-foreground">{t('characters:items.fields.attack')}</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={labelClass}>
            {t('characters:items.fields.attackName')}
            <input className={inputClass} value={attackName} onChange={event => setAttackName(event.target.value)} data-testid="item-field-attack-name" />
          </label>
          <label className={labelClass}>
            {t('characters:items.fields.attackBonus')}
            <input className={inputClass} type="number" value={attackBonus} onChange={event => setAttackBonus(event.target.value)} />
          </label>
          <label className={labelClass}>
            {t('characters:items.fields.attackDamage')}
            <input className={inputClass} value={attackDamage} placeholder="1d8" onChange={event => setAttackDamage(event.target.value)} />
          </label>
          <label className={labelClass}>
            {t('characters:items.fields.attackDamageType')}
            <input className={inputClass} value={attackDamageType} onChange={event => setAttackDamageType(event.target.value)} />
          </label>
        </div>
        <p className="text-xs text-muted-foreground">{t('characters:items.attackHint')}</p>
      </fieldset>

      <label className={labelClass}>
        {t('characters:items.fields.modifiers')}
        <textarea className={textareaClass} rows={4} value={modifiers} onChange={event => setModifiers(event.target.value)} data-testid="item-field-modifiers" />
        <span className="text-xs font-normal">{t('characters:items.modifiersHint')}</span>
      </label>

      <fieldset className="flex flex-col gap-2 rounded-md border p-3" data-testid="icon-picker">
        <legend className="px-1 text-xs font-medium text-muted-foreground">{t('characters:items.fields.icon')}</legend>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
          <button type="button" aria-pressed={icon === ''}
            className={`flex flex-col items-center gap-1 rounded-md border p-2 text-xs ${icon === '' ? 'border-primary bg-accent' : 'hover:border-ring'}`}
            onClick={() => setIcon('')}>
            <span className="grid h-8 w-8 place-items-center text-muted-foreground">—</span>
            {t('characters:items.iconNone')}
          </button>
          {icons.map(info => {
            const url = itemIconUrl(info.id)
            return (
              <button key={info.id} type="button" aria-pressed={icon === info.id} data-testid={`icon-option-${info.id}`}
                className={`flex flex-col items-center gap-1 rounded-md border p-2 text-xs ${icon === info.id ? 'border-primary bg-accent' : 'hover:border-ring'}`}
                onClick={() => setIcon(info.id)}>
                {url && <img src={url} alt="" width={32} height={32} />}
                {info.name[iconLanguage]}
              </button>
            )
          })}
        </div>
      </fieldset>

      <p className="text-xs text-muted-foreground">{t('characters:items.newVersionHint')}</p>
      {validation && <p role="alert" className="text-sm text-destructive-foreground" data-testid="item-form-error">{validation}</p>}
      {issues.length > 0 && (
        <ul className="flex flex-col gap-1 text-sm text-destructive-foreground" data-testid="item-form-issues">
          {issues.map((issue, index) => (
            <li key={index}>
              <span className="font-medium">{t(`characters:items.issueCodes.${issue.code}`, { defaultValue: issue.code })}</span>
              {' '}<code className="text-xs">{issue.path}</code>
              {issue.params && Object.keys(issue.params).length > 0 && (
                <span className="text-xs text-muted-foreground"> {JSON.stringify(issue.params)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <Button onClick={save} disabled={mutation.isPending} data-testid="item-save">
          {mutation.isPending ? t('characters:items.saving') : t('characters:items.save')}
        </Button>
        <Button variant="outline" onClick={onCancel} disabled={mutation.isPending} data-testid="item-cancel">
          {t('characters:items.cancel')}
        </Button>
      </div>
    </div>
  )
}