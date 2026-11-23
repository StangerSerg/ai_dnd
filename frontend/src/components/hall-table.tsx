import { useTranslation } from 'react-i18next'

/** Unified hall table: merged from "my sessions" and the open-tables feed. */
export interface HallTable {
  id: string
  title: string
  scenario: string
  language: string
  dmMode: string
  status: string
  /**
   * Роль места текущего пользователя за столом: 'dm' | 'player' | 'spectator' |
   * null. Только для отображения (бейдж). Лидерство больше НЕ следует из роли
   * 'dm': в ai-режиме лидер сидит игроком. Лидера определяет отдельный флаг
   * `isLeader` (из `is_host` ответа API).
   */
  role: string | null
  mine: boolean
  /** «Я лидер этого стола» — из `is_host` ответа API, а не из роли места. */
  isLeader: boolean
  seatsTaken: number | null
  seatsTotal: number | null
  hostName: string | null
  inviteToken: string | null
  recruiting: boolean
  /** Present in the public open-tables feed. */
  open: boolean
}

// Candle flame hues borrowed from the hall palette so every table reads as a
// distinct place: amber, ember, moss, teal, amethyst, rose-gold.
const flameHues = ['#f7a739', '#e2603f', '#8fbf6a', '#4fb3a5', '#a98bd0', '#d98a6a']

export function HallTableChoice({
  table,
  index,
  active,
  onSelect,
}: {
  table: HallTable
  index: number
  active: boolean
  onSelect: (id: string) => void
}) {
  const { t } = useTranslation()
  const number = index + 1
  const flame = flameHues[index % flameHues.length]
  const seats =
    table.seatsTaken != null
      ? table.seatsTotal != null
        ? `${table.seatsTaken}/${table.seatsTotal}`
        : `${table.seatsTaken}`
      : null
  const flag = table.isLeader
    ? t('games:hall.flagLeader')
    : table.mine
      ? t('games:hall.flagMine')
      : t('games:hall.flagOpen')
  const label = t('games:hall.tableAria', {
    number,
    title: table.title,
    seats: seats ?? t('games:hall.seatsUnknown'),
    flag,
  })

  return (
    <button
      type="button"
      id={`hall-table-${table.id}`}
      className={`table-choice table-${number} ${active ? 'is-active' : ''}`}
      style={{ ['--table-flame' as string]: flame, animationDelay: `${Math.min(index, 8) * 70}ms` }}
      onClick={() => onSelect(table.id)}
      aria-pressed={active}
      aria-label={label}
      data-testid="hall-table"
      data-table-id={table.id}
    >
      <span className="table-shadow" aria-hidden="true" />
      <span className="table-top" aria-hidden="true">
        <span className="table-rim" />
        <span className="table-map">⌁<br />✧</span>
        <span className="table-candle">✦</span>
        <span className="table-dice">⬡</span>
        <span className="table-cup">◉</span>
      </span>
      <span className="hall-table-flag" data-kind={table.isLeader ? 'leader' : table.mine ? 'mine' : 'open'}>
        {flag}
      </span>
      {seats && (
        <span className="hall-table-seats" aria-hidden="true">
          <span className="hall-seat-dot" /> {seats}
        </span>
      )}
      <span className="table-name">
        <small>{t('games:hall.tableNumber', { number })}</small>
        {table.title}
      </span>
    </button>
  )
}