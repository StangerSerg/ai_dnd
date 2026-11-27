import { useTranslation } from 'react-i18next'
import { Label } from '@/components/ui/label'

// Идентификаторы совпадают с backend game/tones.py. Подписи не называют чужих персонажей.
const tones = {
  default: ['Обычный', 'Default'],
  humor: ['Юмор', 'Humor'],
  sarcasm: ['Сарказм', 'Sarcasm'],
  cynical: ['Циничный', 'Cynical'],
  noir: ['Мрачный нуар', 'Dark noir'],
  heroic: ['Героический', 'Heroic'],
  poetic: ['Поэтический', 'Poetic'],
  mysterious: ['Таинственный', 'Mysterious'],
  horror: ['Хоррор', 'Horror'],
  clinical: ['Сухой протокол', 'Clinical'],
  philosophical: ['Философский', 'Philosophical'],
  brutal: ['Брутальный', 'Brutal'],
  caring: ['Заботливый', 'Caring'],
  chaotic: ['Хаотичный', 'Chaotic'],
  inverted_sage: ['Мудрый наставник', 'Wise mentor'],
  solemn_guide: ['Торжественный проводник', 'Solemn guide'],
  fireside_bard: ['Бард у костра', 'Fireside bard'],
  ancient_oracle: ['Древний оракул', 'Ancient oracle'],
  pirate_captain: ['Морской капитан', 'Sea captain'],
  absurd_host: ['Чудаковатый хозяин', 'Whimsical host'],
  drill_sergeant: ['Сержант-инструктор', 'Drill instructor'],
  gothic_narrator: ['Готический рассказчик', 'Gothic narrator'],
  trickster: ['Шут-трикстер', 'Trickster jester'],
  honor_bound: ['Воин чести', 'Honorable warrior'],
} as const

export type Tone = keyof typeof tones

export function CreateToneControl({ value, onChange, disabled }: {
  value: Tone
  onChange: (tone: Tone) => void
  disabled?: boolean
}) {
  const { i18n } = useTranslation()
  const ru = (i18n.resolvedLanguage ?? i18n.language).startsWith('ru')
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="create-tone">{ru ? 'Тон ведущего' : 'Narrator tone'}</Label>
      <select id="create-tone" value={value} onChange={event => onChange(event.target.value as Tone)}
        disabled={disabled} data-testid="create-tone"
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground">
        {(Object.keys(tones) as Tone[]).map(tone => (
          <option key={tone} value={tone}>{tones[tone][ru ? 0 : 1]}</option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">
        {ru ? 'Тон задаётся при создании стола.' : 'The tone is set when the table is created.'}
      </p>
    </div>
  )
}
