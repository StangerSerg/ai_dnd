import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { DicesIcon, PencilIcon, PlusIcon, ScrollTextIcon, UserIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, apiErrorMessage, portal, type CharacterSummary } from '@/api'
import { AvatarImage } from '@/game/avatar'
import { AvatarPicker } from '@/game/avatar-picker'
import { SheetView } from '@/game/sheet-view'
import { generateName } from '@/lib/namegen'
import '@/game/game.css'

const selectClass = 'h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none'

// Cosmetic SRD races; the picked label overrides the archetype's race text.
const CHARACTER_RACES = ['human', 'elf', 'dwarf', 'halfling', 'half-elf', 'half-orc', 'gnome', 'dragonborn', 'tiefling'] as const

function useErrorText() {
  const { t } = useTranslation()
  return (error: unknown): string => {
    if (error instanceof ApiError && error.code) {
      return t(`characters:errors.${error.code}`, { defaultValue: error.detail })
    }
    return apiErrorMessage(error)
  }
}

// Table context (task #31): the invite pins the scenario version; after the
// character is created the player returns to the table and is seated automatically.
interface TableContext {
  scenarioVersionId: string
  invite: string
}

function CreateCharacterDialog({ tableContext }: { tableContext?: TableContext }) {
  const { t, i18n } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(Boolean(tableContext))
  const [name, setName] = useState('')
  const [versionId, setVersionId] = useState(tableContext?.scenarioVersionId ?? '')
  const [charClass, setCharClass] = useState('')
  const [race, setRace] = useState('')
  const [portrait, setPortrait] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: portal.listScenarios, enabled: open })
  const scenarios = scenariosQuery.data ?? []
  const hasCatalog = scenarios.length > 0
  const contextScenario = tableContext ? scenarios.find(item => item.version_id === tableContext.scenarioVersionId) : undefined
  // The catalog lists only the latest scenario version, while the table may pin
  // an older one — take the title from the public table preview in that case.
  const contextInvite = tableContext?.invite
  const previewQuery = useQuery({
    queryKey: ['invite-preview', contextInvite],
    queryFn: () => portal.sessionPreview(contextInvite ?? ''),
    enabled: Boolean(contextInvite),
    retry: false,
  })
  const contextTitle = contextScenario?.title ?? previewQuery.data?.scenario_title ?? tableContext?.scenarioVersionId

  const classesQuery = useQuery({
    queryKey: ['scenario-classes', versionId],
    queryFn: () => portal.scenarioClasses(versionId),
    enabled: open && Boolean(versionId) && !tableContext,
  })
  const allowedClasses = tableContext ? (previewQuery.data?.available_classes ?? []) : (classesQuery.data ?? [])
  const selectedClass = allowedClasses.includes(charClass) ? charClass : (allowedClasses[0] ?? '')

  const mutation = useMutation({
    mutationFn: portal.createCharacter,
    onSuccess: data => {
      setOpen(false)
      setName('')
      setVersionId(tableContext?.scenarioVersionId ?? '')
      setCharClass('')
      setRace('')
      setPortrait(null)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['characters'] })
      if (tableContext) {
        // Auto-return to the table; GamesPage seats the new character (?characterId).
        void navigate({ to: '/games', search: { invite: tableContext.invite, characterId: data.id } })
      }
    },
    onError: err => setError(errorText(err)),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!name.trim()) {
      setError(t('characters:validation.name'))
      return
    }
    if (!tableContext && hasCatalog && !versionId) {
      setError(t('characters:validation.scenario'))
      return
    }
    if (!selectedClass) return
    mutation.mutate({
      name: name.trim(),
      scenario_version_id: versionId,
      char_class: selectedClass,
      race: race ? t(`characters:race.${race}`) : null,
      portrait,
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button data-testid="create-character"><PlusIcon />{t('characters:create')}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('characters:dialogTitle')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {tableContext ? t('characters:tableContextHint') : t('characters:createHint')}
        </p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="character-name">{t('characters:name')}</Label>
            <div className="flex gap-2">
              <Input id="character-name" className="flex-1" value={name} maxLength={120} placeholder={t('characters:namePlaceholder')}
                onChange={event => setName(event.target.value)} data-testid="character-name" />
              <Button type="button" variant="outline" onClick={() => setName(generateName(i18n.language))}
                data-testid="generate-name">
                <DicesIcon />{t('characters:generate')}
              </Button>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            {tableContext
              ? <p className="text-sm font-medium">{t('characters:scenario')}</p>
              : <Label htmlFor="character-scenario">{t('characters:scenario')}</Label>}
            {tableContext ? (
              <p className="text-sm" data-testid="table-scenario">
                {contextTitle}
              </p>
            ) : (
              <>
                {scenariosQuery.isPending && <p role="status" className="text-sm text-muted-foreground">{t('loading')}</p>}
                {hasCatalog ? (
                  <select id="character-scenario" className={selectClass} value={versionId}
                    onChange={event => setVersionId(event.target.value)} data-testid="character-scenario-select">
                    <option value="">{t('characters:scenarioSelect')}</option>
                    {scenarios.map(scenario => (
                      <option key={scenario.version_id} value={scenario.version_id}>
                        {scenario.title} · {scenario.language.toUpperCase()}
                      </option>
                    ))}
                  </select>
                ) : (
                  !scenariosQuery.isPending && (
                    <p className="text-xs text-muted-foreground">{t('characters:validation.scenario')}</p>
                  )
                )}
              </>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="character-class">{t('characters:class.label')}</Label>
            <select id="character-class" className={selectClass} value={selectedClass}
              onChange={event => setCharClass(event.target.value)} data-testid="character-class-select">
              {allowedClasses.map(value => (
                <option key={value} value={value}>{t(`characters:class.${value}`, { defaultValue: value })}</option>
              ))}
            </select>
            {allowedClasses.length > 0 && (
              <p className="text-xs text-muted-foreground" data-testid="class-scenario-hint">{t('characters:class.scenarioHint')}</p>
            )}
            {classesQuery.isError && !tableContext && <p role="alert" className="text-sm text-destructive-foreground">{errorText(classesQuery.error)}</p>}
          </div>
          {!selectedClass.startsWith('ship_') && <div className="flex flex-col gap-2">
            <Label htmlFor="character-race">{t('characters:race.label')}</Label>
            <select id="character-race" className={selectClass} value={race}
              onChange={event => setRace(event.target.value)} data-testid="character-race-select">
              <option value="">{t('characters:race.archetypeDefault')}</option>
              {CHARACTER_RACES.map(value => (
                <option key={value} value={value}>{t(`characters:race.${value}`)}</option>
              ))}
            </select>
          </div>}
          <AvatarPicker value={portrait} onChange={setPortrait} />
          {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="create-character-error">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !selectedClass || (!tableContext && !hasCatalog && !scenariosQuery.isPending)}
              data-testid="create-character-submit">{t('create')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// Смена аватара существующего персонажа: тот же пикер, что и при создании;
// PATCH /api/characters/{id} + инвалидация списка и листа.
function ChangeAvatarDialog({ character, onClose }: { character: CharacterSummary; onClose: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [portrait, setPortrait] = useState<string | null>(character.portrait)
  const [error, setError] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (value: string | null) => portal.updateCharacter(character.id, { portrait: value }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['characters'] })
      void queryClient.invalidateQueries({ queryKey: ['character-sheet', character.id] })
      onClose()
    },
    onError: err => setError(errorText(err)),
  })
  return (
    <Dialog open onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('characters:changeAvatarTitle')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">{character.name}</p>
        <AvatarPicker value={portrait} onChange={setPortrait} />
        {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="change-avatar-error">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>{t('cancel')}</Button>
          <Button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate(portrait)} data-testid="change-avatar-save">
            {t('save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SheetDialog({ character, onClose }: { character: CharacterSummary | null; onClose: () => void }) {
  const { t } = useTranslation()
  const id = character?.id ?? null
  const query = useQuery({
    queryKey: ['character-sheet', id],
    queryFn: () => {
      if (!id) throw new Error('no_character')
      return portal.getCharacter(id)
    },
    enabled: Boolean(id),
  })
  return (
    <Dialog open={Boolean(character)} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{character?.name ?? t('characters:openSheet')}</DialogTitle>
        </DialogHeader>
        {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
        {query.isError && (
          <div role="alert" className="flex items-center gap-3 text-destructive-foreground">
            <span>{apiErrorMessage(query.error)}</span>
            <Button variant="outline" size="sm" onClick={() => query.refetch()}>{t('retry')}</Button>
          </div>
        )}
        {query.data && <SheetView sheet={query.data} />}
      </DialogContent>
    </Dialog>
  )
}

export function CharactersPage() {
  const { t, i18n } = useTranslation()
  const { scenarioVersionId, invite } = useSearch({ from: '/characters' })
  const tableContext = scenarioVersionId && invite ? { scenarioVersionId, invite } : undefined
  const [selected, setSelected] = useState<CharacterSummary | null>(null)
  const [avatarTarget, setAvatarTarget] = useState<CharacterSummary | null>(null)
  const query = useQuery({ queryKey: ['characters'], queryFn: portal.listCharacters })

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="font-serif text-4xl font-medium">{t('characters:title')}</h1>
          <Badge variant="secondary">{t('games:liveBadge')}</Badge>
        </div>
        <CreateCharacterDialog tableContext={tableContext} />
      </div>
      {tableContext && (
        <p className="text-sm text-muted-foreground" role="status" data-testid="table-context-banner">
          {t('characters:tableContextBanner')}
        </p>
      )}
      <p className="text-sm text-muted-foreground">{t('characters:liveHint')}</p>
      {query.isPending && <p role="status" className="text-muted-foreground">{t('loading')}</p>}
      {query.isError && (
        <div role="alert" className="flex items-center gap-3 text-destructive-foreground">
          <span>{apiErrorMessage(query.error)}</span>
          <Button variant="outline" size="sm" onClick={() => query.refetch()}>{t('retry')}</Button>
        </div>
      )}
      {query.data?.length === 0 && <p className="text-muted-foreground" data-testid="characters-empty">{t('characters:empty')}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {query.data?.map(character => (
          <Card key={character.id} data-testid="character-card">
            <CardHeader>
              <div className="flex items-center gap-3">
                {character.portrait ? (
                  <span className="avatar-tile block size-16 shrink-0 overflow-hidden rounded-md" data-testid="character-avatar">
                    <AvatarImage name={character.name} image={character.portrait} />
                  </span>
                ) : (
                  <span className="grid size-16 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground" data-testid="character-avatar-placeholder">
                    <UserIcon className="size-8" aria-hidden="true" />
                  </span>
                )}
                <div className="min-w-0">
                  <CardTitle className="font-serif text-2xl">{character.name}</CardTitle>
                  <p className="break-all text-xs text-muted-foreground">{t('characters:template')}: {character.template_id}</p>
                </div>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <p className="text-muted-foreground">
                {t('characters:updatedAt')}: {new Date(character.updated_at).toLocaleDateString(i18n.language)}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => setSelected(character)} data-testid="open-sheet">
                  <ScrollTextIcon />{t('characters:openSheet')}
                </Button>
                <Button variant="outline" size="sm" onClick={() => setAvatarTarget(character)} data-testid="change-avatar">
                  <PencilIcon />{t('characters:changeAvatar')}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <SheetDialog character={selected} onClose={() => setSelected(null)} />
      {avatarTarget && (
        <ChangeAvatarDialog key={avatarTarget.id} character={avatarTarget} onClose={() => setAvatarTarget(null)} />
      )}
    </div>
  )
}
