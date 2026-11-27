// Item icons (подэтап 7): собственные SVG проекта из public/images/items.
// Id фиксирован каталогом GET /api/item-icons; предмет без icon (или с
// неизвестным id) получает нейтральный inline-силуэт мешочка.
export const ITEM_ICON_IDS = [
  'sword', 'axe', 'bow', 'armor', 'shield', 'potion',
  'scroll', 'ring', 'amulet', 'cloak', 'ammo', 'key',
] as const

export type ItemIconId = (typeof ITEM_ICON_IDS)[number]

export function itemIconUrl(icon: string | null | undefined): string | null {
  if (!icon) return null
  return (ITEM_ICON_IDS as readonly string[]).includes(icon) ? `/images/items/${icon}.svg` : null
}

export function ItemIcon({ icon, name, size = 28 }: {
  icon?: string | null
  /** Alt-текст; пустая строка делает иконку декоративной (имя рядом). */
  name?: string
  size?: number
}) {
  const url = itemIconUrl(icon)
  if (url) {
    return <img className="item-icon" src={url} alt={name ?? ''} width={size} height={size} />
  }
  // Fallback: нейтральный мешочек, палитра тёмного пергамента стола.
  return (
    <svg className="item-icon" viewBox="0 0 64 64" width={size} height={size}
      role={name ? 'img' : undefined} aria-label={name || undefined} aria-hidden={name ? undefined : true}>
      <path d="M24 14h16l-4 8c8 4 14 12 14 22 0 10-8 16-18 16s-18-6-18-16c0-10 6-18 14-22z" fill="#8c7959" />
      <path d="M32 22c8 4 14 12 14 22 0 10-8 16-14 16z" fill="#6d5f47" />
      <rect x="22" y="10" width="20" height="7" rx="3.5" fill="#c8a56a" />
      <path d="M26 38c2-3 5-5 6-5" stroke="#c8a56a" strokeWidth="3" strokeLinecap="round" fill="none" />
    </svg>
  )
}