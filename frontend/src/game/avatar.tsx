import { useState } from 'react'

// Extracted from the Codex demo (DemoApp.tsx) unchanged: portrait with a
// graceful sigil fallback. Shared by /demo and the real player screen.
export function AvatarImage({ name, image }: { name: string; image: string }) {
  const [missing, setMissing] = useState(false)
  return <span className="avatar-image">{missing ? <span aria-hidden="true">✧</span> : <img src={image} alt={`Портрет: ${name}`} onError={() => setMissing(true)} />}</span>
}