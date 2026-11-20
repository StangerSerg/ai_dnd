import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import type { Locale } from '@/api/types'
import ru from './locales/ru.json'
import en from './locales/en.json'

const STORAGE_KEY = 'aidnd.locale'
const namespaces = ['common', 'auth', 'portal', 'profile', 'games', 'game', 'characters', 'demo', 'admin'] as const

function resources(bundle: Record<(typeof namespaces)[number], Record<string, unknown>>) {
  return Object.fromEntries(namespaces.map(ns => [ns, bundle[ns]]))
}

export function isLocale(value: unknown): value is Locale {
  return value === 'ru' || value === 'en'
}

export function storedLocale(): Locale | null {
  const value = localStorage.getItem(STORAGE_KEY)
  return isLocale(value) ? value : null
}

function initialLocale(): Locale {
  return storedLocale() ?? (navigator.language.toLowerCase().startsWith('ru') ? 'ru' : 'en')
}

void i18n.use(initReactI18next).init({
  resources: { ru: resources(ru), en: resources(en) },
  lng: initialLocale(),
  fallbackLng: 'en',
  supportedLngs: ['ru', 'en'],
  ns: [...namespaces],
  defaultNS: 'common',
  interpolation: { escapeValue: false },
})

function syncDocumentLang(locale: string) {
  document.documentElement.lang = locale
}
syncDocumentLang(i18n.language)
i18n.on('languageChanged', syncDocumentLang)

export function setLocale(locale: Locale) {
  localStorage.setItem(STORAGE_KEY, locale)
  void i18n.changeLanguage(locale)
}

// Seam for GET /api/me: the server locale applies only while the user
// has not made an explicit local choice.
export function applyUserLocale(locale: Locale) {
  if (!storedLocale() && i18n.language !== locale) void i18n.changeLanguage(locale)
}

export default i18n