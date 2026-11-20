import { useTranslation } from 'react-i18next'
import { LanguagesIcon, CheckIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { setLocale } from '@/i18n'
import type { Locale } from '@/api/types'

const locales: Locale[] = ['ru', 'en']

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={t('language.label')} data-testid="language-switcher">
          <LanguagesIcon />
          <span className="uppercase">{i18n.language}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {locales.map(locale => (
          <DropdownMenuItem key={locale} onClick={() => setLocale(locale)} data-testid={`locale-${locale}`}>
            {t(`language.${locale}`)}
            {i18n.language === locale && <CheckIcon className="ml-auto" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}