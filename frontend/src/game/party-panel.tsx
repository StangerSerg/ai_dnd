import type { ReactNode } from 'react'

// Party list extracted from the Codex demo (DemoApp.tsx). Members carry their
// own portrait node (demo sigils or real portraits) and an optional right slot
// (the real screen shows hit points there). badge/controls — модерация лидера
// (#26): бейдж мута виден всем, контролы получает только лидер.
export interface PartyMember {
  key: string
  name: string
  note: string
  portrait: ReactNode
  right?: ReactNode
  muted?: boolean
  badge?: ReactNode
  controls?: ReactNode
  /** testid строки-заметки (локализованные состояния, #37). */
  noteTestId?: string
}

export function PartyPanel({ heading, count, members, note }: { heading: string; count?: string; members: PartyMember[]; note?: string }) {
  return (
    <>
      <div className="aside-heading party-title"><span>{heading}</span>{count && <span>{count}</span>}</div>
      <div className="party-list">
        {members.map(member => (
          <div key={member.key} className={member.muted ? 'party-seat-muted' : undefined}>
            {member.portrait}
            <p><strong>{member.name}</strong><small data-testid={member.noteTestId}>{member.note}</small>{member.badge}</p>
            {member.right && <span className="party-right">{member.right}</span>}
            {member.controls}
          </div>
        ))}
      </div>
      {note && <p className="party-note">{note}</p>}
    </>
  )
}