import { useTranslation } from 'react-i18next'
import { UserIcon } from 'lucide-react'
import { localized } from '@/api'
import { AvatarImage } from '@/game/avatar'
import { AVATARS } from '@/game/avatars'
import { cn } from '@/lib/utils'
import '@/game/game.css'

// Переиспользуемый пикер аватара (создание персонажа и смена аватара):
// сетка пресетов из game/avatars.ts + опция «без аватара» (value = null).
export function AvatarPicker({ value, onChange }: { value: string | null; onChange: (value: string | null) => void }) {
  const { t, i18n } = useTranslation()
  const optionClass = 'flex flex-col items-center gap-1 rounded-md border border-input bg-transparent p-2 text-center transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
  const selectedClass = 'border-ring ring-[3px] ring-ring/50'
  return (
    <fieldset className="flex min-w-0 flex-col gap-2" data-testid="avatar-picker">
      <legend className="mb-2 text-sm font-medium">{t('characters:chooseAvatar')}</legend>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4" role="radiogroup" aria-label={t('characters:chooseAvatar')}>
        <button type="button" role="radio" aria-checked={value === null} onClick={() => onChange(null)}
          className={cn(optionClass, value === null && selectedClass)} data-testid="avatar-option-none">
          <span className="avatar-tile grid size-16 place-items-center overflow-hidden rounded-md bg-muted text-muted-foreground">
            <UserIcon className="size-8" aria-hidden="true" />
          </span>
          <span className="text-xs text-muted-foreground">{t('characters:noAvatar')}</span>
        </button>
        {AVATARS.map(avatar => {
          const label = localized(avatar.name, i18n.language)
          return (
            <button key={avatar.id} type="button" role="radio" aria-checked={value === avatar.image}
              onClick={() => onChange(avatar.image)} className={cn(optionClass, value === avatar.image && selectedClass)}
              data-testid={`avatar-option-${avatar.id}`}>
              <span className="avatar-tile block size-16 overflow-hidden rounded-md">
                <AvatarImage name={label} image={avatar.image} />
              </span>
              <span className="text-xs">{label}</span>
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}