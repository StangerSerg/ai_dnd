// Local presentation adapter only: these types are not a server API contract.
export type Table = {
  id: string
  number: string
  title: string
  subtitle: string
  players: string
  level: string
  mood: string
  description: string
  scene: string
}

export type Stat = { id: string; label: string; short: string; min: number; max: number; initial: number }
export type Avatar = { id: string; name: string; image: string }
export type Setup = { stats: readonly Stat[]; points: number; avatars: readonly Avatar[] }
export type Character = { name: string; concept: string; avatar: string; stats: Record<string, number> }
export type Entry = { kind: 'action' | 'narration' | 'command'; text: string }
export type Command = 'check' | 'pause' | 'scene'

export interface PortalSource {
  tables: readonly Table[]
  loadSetup(tableId: string): Promise<Setup>
  initialCharacter(setup: Setup): Character
  changeStat(setup: Setup, character: Character, id: string, value: number): Character
  remaining(setup: Setup, character: Character): number
  submitAction(text: string): Entry
  submitNarration(text: string): Entry
  submitCommand(command: Command): Entry
}

const setup: Setup = {
  points: 12,
  stats: [
    { id: 'strength', label: 'Сила', short: 'СИЛ', min: 1, max: 6, initial: 1 },
    { id: 'agility', label: 'Ловкость', short: 'ЛОВ', min: 1, max: 6, initial: 1 },
    { id: 'wisdom', label: 'Мудрость', short: 'МДР', min: 1, max: 6, initial: 1 },
    { id: 'presence', label: 'Харизма', short: 'ХАР', min: 1, max: 6, initial: 1 },
  ],
  avatars: [
    { id: 'human-knight', name: 'Человек-рыцарь', image: '/images/avatars/human-knight.png' },
    { id: 'dwarf-cleric', name: 'Дворф-жрец', image: '/images/avatars/dwarf-cleric.png' },
    { id: 'elf-rogue', name: 'Эльф-плут', image: '/images/avatars/elf-rogue.png' },
    { id: 'elf-ranger', name: 'Эльф-следопыт', image: '/images/avatars/elf-ranger.png' },
    { id: 'human-mage', name: 'Человек-маг', image: '/images/avatars/human-mage.png' },
    { id: 'dwarf-warrior', name: 'Дворф-воин', image: '/images/avatars/dwarf-warrior.png' },
  ],
}

const remaining = (model: Setup, character: Character) => model.points - model.stats.reduce((sum, stat) => sum + (character.stats[stat.id] - stat.min), 0)

export const demoSource: PortalSource = {
  tables: [
    {
      id: 'mill', number: '01', title: 'Тайна старой мельницы', subtitle: 'Там, где шепчет туман',
      players: '3 из 5', level: '1–3 уровень', mood: 'Тайна · Приключение',
      description: 'На краю деревни остановилась мельница. А вместе с ней — время, кажется, и для её жителей.',
      scene: 'Дождь давно закончился, но с крыш деревни всё ещё падают редкие капли. Впереди, за покосившимся забором, чернеет старая мельница. Её крылья неподвижны, хотя ветер треплет ваши плащи. Изнутри доносится тихий, размеренный стук.',
    },
    {
      id: 'forest', number: '02', title: 'Тропами лунного леса', subtitle: 'Не всякий свет укажет путь',
      players: '2 из 4', level: '2–4 уровень', mood: 'Мистика · Исследование',
      description: 'Старые тропы меняются местами, а лунный свет приводит путников не туда, куда они шли.',
      scene: 'Лес встречает вас тишиной. Между стволами дрожит серебристый свет, а под ногами лежит тропа, которой не было на карте. Где-то впереди звонко смеётся невидимая птица.',
    },
    {
      id: 'crown', number: '03', title: 'Пепел и корона', subtitle: 'У каждой легенды есть цена',
      players: '4 из 6', level: '3–5 уровень', mood: 'Интрига · Опасность',
      description: 'В заброшенном замке ищут корону, способную изменить судьбу целого королевства.',
      scene: 'Ветер разносит пепел по пустому двору. Над вами возвышается замок, чьи окна давно лишились света. На ступенях главной лестницы кто-то недавно оставил следы.',
    },
  ],
  async loadSetup(tableId) {
    // Deliberately asynchronous; no network requests or invented endpoints.
    await new Promise(resolve => window.setTimeout(resolve, 350))
    if (!this.tables.some(table => table.id === tableId)) throw new Error('Стол не найден')
    return setup
  },
  initialCharacter(model) {
    return { name: '', concept: '', avatar: model.avatars[0].id, stats: Object.fromEntries(model.stats.map(stat => [stat.id, stat.initial])) }
  },
  remaining,
  changeStat(model, character, id, value) {
    const stat = model.stats.find(item => item.id === id)
    if (!stat || !Number.isInteger(value)) return character
    const current = character.stats[id]
    const next = Math.max(stat.min, Math.min(stat.max, current + remaining(model, character), value))
    return { ...character, stats: { ...character.stats, [id]: next } }
  },
  submitAction: text => ({ kind: 'action', text }),
  submitNarration: text => ({ kind: 'narration', text }),
  submitCommand: command => ({ kind: 'command', text: { check: 'Запрошена условная проверка. Бросок и результат не рассчитываются.', pause: 'Объявлена условная пауза.', scene: 'Предложена смена сцены. Состояние игры не меняется.' }[command] }),
}
