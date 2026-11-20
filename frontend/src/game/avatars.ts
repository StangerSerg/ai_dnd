// Пресеты аватаров персонажей: файлы в public/images/avatars, allowlist на
// бэкенде — characters/avatars.py (AVATAR_PRESETS). Демо (demo.ts) не трогаем.
export interface AvatarPreset {
  id: string
  name: { ru: string; en: string }
  image: string
}

export const AVATARS: AvatarPreset[] = [
  { id: 'dwarf-cleric', name: { ru: 'Дворф-жрец', en: 'Dwarf cleric' }, image: '/images/avatars/dwarf-cleric.png' },
  { id: 'dwarf-warrior', name: { ru: 'Дворф-воин', en: 'Dwarf warrior' }, image: '/images/avatars/dwarf-warrior.png' },
  { id: 'elf-ranger', name: { ru: 'Эльф-следопыт', en: 'Elf ranger' }, image: '/images/avatars/elf-ranger.png' },
  { id: 'elf-rogue', name: { ru: 'Эльф-плут', en: 'Elf rogue' }, image: '/images/avatars/elf-rogue.png' },
  { id: 'human-knight', name: { ru: 'Человек-рыцарь', en: 'Human knight' }, image: '/images/avatars/human-knight.png' },
  { id: 'human-mage', name: { ru: 'Человек-маг', en: 'Human mage' }, image: '/images/avatars/human-mage.png' },
]