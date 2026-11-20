import type { FormEvent, ReactNode } from 'react'

// Action/narration form extracted from the Codex demo (DemoApp.tsx). The real
// player screen adds quick-action buttons, a disabled state and a notice line.
export interface QuickAction {
  key: string
  label: string
  onClick: () => void
  disabled?: boolean
}

export function ActionForm({
  id,
  label,
  value,
  onChange,
  onSubmit,
  placeholder,
  rows = 3,
  maxLength = 1000,
  submitLabel,
  disabled = false,
  footerLeft,
  footerRight,
  notice,
  quickActions,
  children,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder: string
  rows?: number
  maxLength?: number
  submitLabel: string
  disabled?: boolean
  footerLeft: ReactNode
  footerRight: ReactNode
  notice?: ReactNode
  quickActions?: QuickAction[]
  children?: ReactNode
}) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!disabled && value.trim()) onSubmit()
  }
  return (
    <form className="action-form" onSubmit={handleSubmit}>
      <label htmlFor={id}>{label}</label>
      {quickActions && quickActions.length > 0 && (
        <div className="quick-actions">
          {quickActions.map(action => (
            <button key={action.key} type="button" className="secondary-button" disabled={disabled || action.disabled} onClick={action.onClick}>
              {action.label}
            </button>
          ))}
        </div>
      )}
      <div className="action-input">
        <textarea id={id} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} rows={rows} maxLength={maxLength} disabled={disabled} />
        <button type="submit" disabled={disabled || !value.trim()} aria-label={submitLabel}>➜</button>
      </div>
      <div className="action-footer"><span>{footerLeft}</span><span>{footerRight}</span></div>
      {notice && <p className="action-notice" role="status">{notice}</p>}
      {children}
    </form>
  )
}