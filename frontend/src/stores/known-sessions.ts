import { create } from 'zustand'
import type { KnownSession } from '@/api'

// No GET /api/sessions list endpoint yet: remember sessions created/joined
// from this browser in localStorage. IDs and invite tokens are real.
const STORAGE_KEY = 'aidnd.knownSessions'

function load(): KnownSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as KnownSession[]) : []
  } catch {
    return []
  }
}

function persist(sessions: KnownSession[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
}

interface KnownSessionsState {
  sessions: KnownSession[]
  add: (session: KnownSession) => void
}

export const useKnownSessions = create<KnownSessionsState>((set, get) => ({
  sessions: load(),
  add: session => {
    const existing = get().sessions.find(item => item.id === session.id)
    // Merge: keep known details (invite, scenario, language) when the new
    // entry does not carry them (e.g. joining a session we created as DM).
    const merged: KnownSession = {
      ...existing,
      ...session,
      invite_token: session.invite_token ?? existing?.invite_token,
      scenario_version_id: session.scenario_version_id ?? existing?.scenario_version_id,
      scenario: session.scenario ?? existing?.scenario,
      language: session.language || existing?.language || '',
      dm_mode: session.dm_mode || existing?.dm_mode || '',
    }
    const next = [merged, ...get().sessions.filter(item => item.id !== session.id)]
    persist(next)
    set({ sessions: next })
  },
}))