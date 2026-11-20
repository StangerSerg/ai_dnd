// Minimal hand-written typings for @3d-dice/dice-box-threejs@0.0.12 (the
// package ships no .d.ts). Only the surface used by src/game/dice-3d.tsx is
// declared; shapes verified against dist/dice-box-threejs.es.js.
declare module '@3d-dice/dice-box-threejs' {
  export interface DiceBoxColorset {
    name: string
    foreground: string | string[]
    background: string | string[]
    outline?: string
    edge?: string | string[]
    texture?: string
    material?: string
  }

  export interface DiceBoxConfig {
    assetPath?: string
    framerate?: number
    sounds?: boolean
    volume?: number
    shadows?: boolean
    theme_surface?: string
    theme_colorset?: string
    theme_customColorset?: DiceBoxColorset | null
    theme_texture?: string
    theme_material?: string
    gravity_multiplier?: number
    light_intensity?: number
    baseScale?: number
    strength?: number
    iterationLimit?: number
    onRollComplete?: (results: DiceBoxResults) => void
  }

  export interface DiceBoxDieResult {
    type: string
    sides: number
    id: number
    value: number
    label: string
    reason: string
  }

  export interface DiceBoxSetResult {
    num: number
    type: string
    sides: number
    rolls: DiceBoxDieResult[]
    total: number
  }

  export interface DiceBoxResults {
    notation: string
    sets: DiceBoxSetResult[]
    modifier: number
    total: number
  }

  export default class DiceBox {
    constructor(selector: string, config?: DiceBoxConfig)
    theme_colorset: string
    theme_customColorset: DiceBoxColorset | null
    theme_texture: string
    theme_material: string
    DiceFactory: { dice_material: string }
    initialized: boolean
    initialize(): Promise<void>
    /** Notation with predetermined outcomes, e.g. "1d20@17" or "2d6@3,5"
     * (server values, applied to the dice list in notation order). */
    roll(notation: string): Promise<DiceBoxResults>
    clearDice(): void
    updateConfig(config: Partial<DiceBoxConfig>): void
    loadTheme(theme: { colorset?: string; texture?: string; material?: string }): Promise<void>
  }
}