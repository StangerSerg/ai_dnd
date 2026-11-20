import { useTranslation } from 'react-i18next'
import { localized, type CharacterSheet, type SheetField } from '@/api/portal'
import type { ResourceState } from '@/api/events'

// Character sheet rendered from the server template (content/sheets/*.json):
// layout sections -> fields by id, labels localized by the template itself.
// Read-only view (stage 3.4). Computed fields show the server-provided value
// or a dash — formulas are evaluated on the backend, not here.
// `liveResources` overlays current resource values from state.rules.actors.

function fieldValue(field: SheetField, values: Record<string, unknown>): unknown {
  const value = values[field.id]
  return value === undefined ? field.default : value
}

function FieldView({ field, values, language, liveResources }: {
  field: SheetField
  values: Record<string, unknown>
  language: string
  liveResources: Record<string, ResourceState> | undefined
}) {
  const { t } = useTranslation()
  const value = fieldValue(field, values)
  const label = localized(field.label, language)

  if (field.type === 'image') {
    return typeof value === 'string' && value
      ? <img className="sheet-portrait" src={value} alt={label} />
      : <span className="sheet-portrait sheet-portrait-empty" aria-hidden="true">✧</span>
  }
  if (field.type === 'resource') {
    const live = liveResources?.[field.id]
    const stored = (value ?? {}) as Partial<ResourceState>
    const current = live?.current ?? stored.current
    const max = live?.max ?? stored.max
    return <span className="sheet-value">{current ?? '—'}{max !== undefined ? ` / ${max}` : ''}</span>
  }
  if (field.type === 'checkbox') {
    return <span className="sheet-value">{value ? '✓' : '—'}</span>
  }
  if (field.type === 'select') {
    const option = field.options?.find(item => item.value === value)
    return <span className="sheet-value">{option ? localized(option.label, language) : '—'}</span>
  }
  if (field.type === 'list') {
    const items = Array.isArray(value) ? (value as Record<string, unknown>[]) : []
    if (items.length === 0) return <span className="sheet-value">—</span>
    // item — запись подполей по id; сам id в объекте подполя не хранится.
    const subfields = Object.entries(field.item ?? {}).map(([id, sub]) => ({ ...sub, id }))
    return (
      <table className="sheet-list">
        <thead><tr>{subfields.map(sub => <th key={sub.id}>{localized(sub.label, language)}</th>)}</tr></thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={index}>{subfields.map(sub => <td key={sub.id}>{String(item[sub.id] ?? '—')}</td>)}</tr>
          ))}
        </tbody>
      </table>
    )
  }
  if (field.type === 'longtext') {
    return <span className="sheet-value sheet-longtext">{typeof value === 'string' && value ? value : '—'}</span>
  }
  // text / number / computed
  if (value === undefined || value === null || value === '') return <span className="sheet-value">—</span>
  return <span className="sheet-value">{String(value)}{field.type === 'computed' && <em className="sheet-computed-mark" title={t('game:sheet.computed')}> *</em>}</span>
}

export function SheetView({ sheet, liveResources }: { sheet: CharacterSheet; liveResources?: Record<string, ResourceState> }) {
  const { i18n } = useTranslation()
  const language = i18n.language
  const fields = new Map(sheet.template.fields.map(field => [field.id, field]))
  // Avatar preset is stored in sheet.portrait, not in template values.
  const values = { ...sheet.values }
  if (sheet.portrait) {
    for (const field of sheet.template.fields) {
      const value = fieldValue(field, values)
      if (field.type === 'image' && !(typeof value === 'string' && value)) values[field.id] = sheet.portrait
    }
  }
  return (
    <div className="sheet-view" data-testid="sheet-view">
      {sheet.template.layout.map((section, index) => (
        <section key={index} className="sheet-section">
          <h3>{localized(section.section, language)}</h3>
          <div className="sheet-grid" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(section.cols, 6))}, minmax(0, 1fr))` }}>
            {section.items.map(id => {
              const field = fields.get(id)
              if (!field) return null
              const wide = field.type === 'list' || field.type === 'longtext' || field.type === 'image'
              return (
                <div key={id} className={`sheet-field sheet-field-${field.type}`} style={wide ? { gridColumn: '1 / -1' } : undefined}>
                  <span className="sheet-label">{localized(field.label, language)}</span>
                  <FieldView field={field} values={values} language={language} liveResources={liveResources} />
                </div>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}