// Story feed pieces extracted from the Codex demo (DemoApp.tsx): the DM story
// card, a feed entry and the live (streaming) narration card. Shared by /demo
// and the real player screen; markup/classes match demo.css.

export function Ornament() {
  return <span className="ornament" aria-hidden="true">✧ <i /> ✧</span>
}

export function StoryCard({ title, note, text, question }: { title: string; note: string; text: string; question?: string }) {
  return (
    <div className="story-card">
      <div className="story-card-header"><div className="dm-avatar">✧</div><div><strong>{title}</strong><small>{note}</small></div><span>✦</span></div>
      <div className="story-body"><p>{text}</p>{question && <p className="story-question">{question}</p>}</div>
      <div className="story-end"><Ornament /></div>
    </div>
  )
}

export function EntryItem({ label, text }: { label: string; text: string }) {
  return <div className="action-entry"><span>{label}</span><p>{text}</p></div>
}

/** Narration still streaming: chunks accumulate here; the final Narrated replaces it. */
export function LiveNarration({ title, note, text }: { title: string; note: string; text: string }) {
  return (
    <div className="story-card story-card-live" data-testid="live-narration">
      <div className="story-card-header"><div className="dm-avatar">✧</div><div><strong>{title}</strong><small>{note}</small></div><span>✦</span></div>
      <div className="story-body"><p>{text}<span className="typing-caret" aria-hidden="true" /></p></div>
    </div>
  )
}