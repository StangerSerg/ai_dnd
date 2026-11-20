import type { ReactNode } from 'react'
import { AvatarImage } from './avatar'

// Character card extracted from the Codex demo (DemoApp.tsx): portrait art,
// name/concept and a stat grid. The real screen adds the hit-point bar and
// server-driven stats; the demo keeps its four local stats.
export interface CardStat {
  key: string
  short: string
  value: ReactNode
}

export function CharacterCard({
  portrait,
  artNote,
  kicker,
  name,
  concept,
  stats,
  hp,
  hpLabel,
  children,
}: {
  portrait: { name: string; image: string } | null
  artNote: string
  kicker: string
  name: string
  concept: string
  stats: CardStat[]
  hp?: { current: number; max: number } | null
  hpLabel?: string
  children?: ReactNode
}) {
  return (
    <section className="character-card">
      <div className="character-art">
        {portrait && <AvatarImage key={portrait.image} name={portrait.name} image={portrait.image} />}
        <span>{artNote}</span>
      </div>
      <div className="character-info">
        <p>{kicker}</p>
        <h2>{name}</h2>
        <span>{concept}</span>
        {hp && hpLabel && (
          <>
            <div className="health-label"><span>{hpLabel}</span><strong>{hp.current} / {hp.max}</strong></div>
            <div className="health-bar"><span style={{ width: `${hp.max > 0 ? Math.max(0, Math.min(100, (hp.current / hp.max) * 100)) : 0}%` }} /></div>
          </>
        )}
        <div className="stats">{stats.map(stat => <div key={stat.key}><strong>{stat.value}</strong><small>{stat.short}</small></div>)}</div>
        {children}
      </div>
    </section>
  )
}