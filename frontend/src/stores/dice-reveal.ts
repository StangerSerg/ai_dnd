import { create } from 'zustand'

// Click-to-roll reveal gate: purely client-side presentation state. The server
// already resolved everything; this store only delays showing the local
// player's own live roll results (d20 checks/attacks and damage/healing dice
// alike) until they click the idle die.
// History vs live: on the FIRST session snapshot load all DiceRolled seqs are
// seeded as revealed; later snapshot refetches must not re-seed (a live roll
// that got refetched stays gated until clicked). Switching sessions resets.
interface DiceRevealState {
  sessionId: string | null
  /** Seqs whose result (and downstream entries) may be shown. */
  revealed: ReadonlySet<number>
  /** Seq clicked and currently animating; downstream stays held until finish. */
  rolling: number | null
  seedHistory: (sessionId: string, seqs: Iterable<number>) => void
  startRoll: (seq: number) => void
  finishRoll: (seq: number) => void
}

export const useDiceReveal = create<DiceRevealState>()((set, get) => ({
  sessionId: null,
  revealed: new Set<number>(),
  rolling: null,
  seedHistory: (sessionId, seqs) => {
    if (get().sessionId === sessionId) return
    set({ sessionId, revealed: new Set(seqs), rolling: null })
  },
  startRoll: seq => {
    const { rolling, revealed } = get()
    if (rolling === null && !revealed.has(seq)) set({ rolling: seq })
  },
  finishRoll: seq => {
    const { rolling, revealed } = get()
    if (rolling !== seq) return
    set({ rolling: null, revealed: new Set(revealed).add(seq) })
  },
}))