import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '@/api'
import type { DiceStyle } from '@/demo/Dice'

// User-facing UI preferences (stage 3.5). Persisted in localStorage so the
// "less animation" choice survives reloads and applies to every session.
// lessAnimation=true forces the lightweight 2D dice renderer (no WebGL).
// diceStyle skins both renderers; persist's default shallow merge keeps the
// 'amber' default for stored states predating the field (no migrate needed).
// Server (users.ui_settings JSONB) is the source of truth on load; every local
// change is mirrored optimistically via PATCH /api/me (fire-and-forget).
const DICE_STYLES: readonly DiceStyle[] = ['amber', 'jade', 'amethyst', 'gold', 'malachite', 'ruby']

interface SettingsState {
  lessAnimation: boolean
  setLessAnimation: (value: boolean) => void
  toggleLessAnimation: () => void
  diceStyle: DiceStyle
  setDiceStyle: (style: DiceStyle) => void
  hydrateFromServer: (ui: { dice_style?: string; less_animation?: boolean } | undefined) => void
}

export const useSettings = create<SettingsState>()(
  persist(
    (set, get) => {
      const syncToServer = () => {
        const { diceStyle, lessAnimation } = get()
        void api
          .patchMe({ ui_settings: { dice_style: diceStyle, less_animation: lessAnimation } })
          .catch(console.warn)
      }
      return {
        lessAnimation: false,
        setLessAnimation: value => {
          set({ lessAnimation: value })
          syncToServer()
        },
        toggleLessAnimation: () => {
          set({ lessAnimation: !get().lessAnimation })
          syncToServer()
        },
        diceStyle: 'amber',
        setDiceStyle: style => {
          set({ diceStyle: style })
          syncToServer()
        },
        hydrateFromServer: ui => {
          if (!ui) return
          set({
            diceStyle: DICE_STYLES.find(style => style === ui.dice_style) ?? 'amber',
            lessAnimation: ui.less_animation ?? false,
          })
        },
      }
    },
    { name: 'aidnd.settings' },
  ),
)