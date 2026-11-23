import { useEffect, useMemo, useRef, useState, type FormEvent, type Ref } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { CopyIcon, EyeIcon, LogInIcon, PlusIcon, UserIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  api,
  ApiError,
  apiErrorMessage,
  portal,
  type CharacterSummary,
  type DmMode,
  type Improvisation,
  type Locale,
  type SessionPreview,
  type SessionSummary,
} from '@/api'
import { openTables, setRecruiting, type OpenTableItem, type RecruitingRequest } from '@/api/client'
import { useKnownSessions } from '@/stores/known-sessions'
import { HallTableChoice, type HallTable } from '@/components/hall-table'
import { HallCarousel } from '@/components/hall-carousel'
import { HallRecruitingControl } from '@/components/hall-recruiting'
import {
  CreateVisibilityControl,
  defaultCreateVisibility,
  type CreateVisibilityValue,
} from '@/components/create-visibility'
import { AvatarImage } from '@/game/avatar'
import '@/demo/demo.css'
import '@/hall.css'

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const selectClass = 'h-9 w-full rounded-md border border-input bg-background text-foreground px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none'

function useErrorText() {
  const { t } = useTranslation()
  return (error: unknown): string => {
    if (error instanceof ApiError) {
      // Часть эндпоинтов шлёт код строкой detail (без {"code": ...}).
      const code = error.code ?? error.detail
      return t(`games:errors.${code}`, { defaultValue: error.detail })
    }
    return apiErrorMessage(error)
  }
}

function InviteCopy({ token }: { token: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  async function copy() {
    const link = `${window.location.origin}/games?invite=${encodeURIComponent(token)}`
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      window.prompt(t('game:inviteCopyPrompt'), link)
    }
  }
  return (
    <Button type="button" variant="outline" size="sm" onClick={copy} data-testid="copy-invite-link">
      <CopyIcon />{copied ? t('games:inviteCopied') : t('games:copyInvite')}
    </Button>
  )
}

function CreateGameDialog() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const addKnown = useKnownSessions(state => state.add)
  const [open, setOpen] = useState(false)
  const [versionId, setVersionId] = useState('')
  const [language, setLanguage] = useState<Locale>('ru')
  const [dmMode, setDmMode] = useState<DmMode>('ai')
  const [improvisation, setImprovisation] = useState<Improvisation>('moderate')
  const [characterId, setCharacterId] = useState('')
  const [visibility, setVisibility] = useState<CreateVisibilityValue>(defaultCreateVisibility)
  const [error, setError] = useState<string | null>(null)

  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: portal.listScenarios, enabled: open })
  const scenarios = scenariosQuery.data ?? []
  const hasCatalog = scenarios.length > 0
  // Кнопки RU/EN работают как фильтр каталога: язык задаётся кнопками, не сценарием.
  const visibleScenarios = scenarios.filter(item => item.language === language)
  const selectedScenario = visibleScenarios.find(item => item.version_id === versionId)

  // В ai-режиме создатель садится за стол лидером И игроком, поэтому выбирает
  // персонажа прямо здесь (без лишних шагов после создания). Совпадение шаблона
  // персонажа и сценария проверяет бэкенд (template_mismatch). В human-режиме
  // персонаж не нужен — создатель остаётся ведущим.
  const charactersQuery = useQuery({ queryKey: ['characters'], queryFn: portal.listCharacters, enabled: open })
  const characters: CharacterSummary[] = charactersQuery.data ?? []
  const needCharacter = dmMode === 'ai'
  const hasCharacters = characters.length > 0
  const charactersEmpty = charactersQuery.isSuccess && !hasCharacters

  const mutation = useMutation({
    mutationFn: api.createSession,
    onSuccess: data => {
      addKnown({
        id: data.id,
        invite_token: data.invite_token,
        scenario_version_id: versionId.trim(),
        scenario: selectedScenario?.title,
        language,
        dm_mode: dmMode,
        role: data.role === 'dm' ? 'dm' : 'player',
        recruiting: data.recruiting,
        seats_total: data.seats_total,
        created_at: new Date().toISOString(),
      })
      setOpen(false)
      setVersionId('')
      setCharacterId('')
      setVisibility(defaultCreateVisibility)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['characters'] })
      void navigate({ to: '/games/$sessionId', params: { sessionId: data.id } })
    },
    onError: err => setError(errorText(err)),
  })

  function pickLanguage(value: Locale) {
    setLanguage(value)
    // Выбранный сценарий другого языка не должен переживать смену фильтра; ручной UUID не трогаем.
    if (hasCatalog && versionId && !scenarios.some(item => item.version_id === versionId && item.language === value)) {
      setVersionId('')
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (hasCatalog) {
      if (!versionId) {
        setError(t('games:validation.scenario'))
        return
      }
    } else if (!uuidPattern.test(versionId.trim())) {
      setError(t('games:validation.versionId'))
      return
    }
    if (needCharacter) {
      if (charactersEmpty) {
        setError(t('games:characterEmptyHint'))
        return
      }
      if (!characterId) {
        setError(t('games:validation.character'))
        return
      }
    }
    mutation.mutate({
      scenario_version_id: versionId.trim(),
      language,
      dm_mode: dmMode,
      improvisation,
      ...(needCharacter ? { character_id: characterId } : {}),
      recruiting: visibility.recruiting,
      // Ёмкость задаётся только для публичного стола: у приватного она не
      // показывается, и скрытый лимит не должен резать вход по приглашению.
      seats_total: visibility.recruiting ? visibility.seats_total : null,
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button data-testid="create-game"><PlusIcon />{t('games:create')}</Button>
      </DialogTrigger>
      <DialogContent className="hall-create-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('games:dialogTitle')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">{t('games:createHint')}</p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="scenario-version-id">{t('games:scenario')}</Label>
            {scenariosQuery.isPending && <p role="status" className="text-sm text-muted-foreground">{t('loading')}</p>}
            {hasCatalog ? (
              <>
                <select id="scenario-version-id" className={selectClass} value={versionId}
                  onChange={event => setVersionId(event.target.value)} data-testid="scenario-select">
                  <option value="">{t('games:scenarioSelect')}</option>
                  {visibleScenarios.map(scenario => (
                    <option key={scenario.version_id} value={scenario.version_id}>
                      {scenario.title} · {scenario.language.toUpperCase()}
                    </option>
                  ))}
                </select>
                {visibleScenarios.length === 0 && (
                  <p className="text-xs text-muted-foreground" data-testid="scenario-language-empty">{t('games:scenarioLanguageEmpty')}</p>
                )}
              </>
            ) : (
              <>
                <Input id="scenario-version-id" value={versionId} placeholder="00000000-0000-0000-0000-000000000000"
                  onChange={event => setVersionId(event.target.value)} data-testid="scenario-version-id" />
                {!scenariosQuery.isPending && (
                  <p className="text-xs text-muted-foreground">{t('games:scenarioFallbackHint')}</p>
                )}
              </>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <Label>{t('games:language')}</Label>
            <div className="flex gap-2">
              {(['ru', 'en'] as const).map(value => (
                <Button key={value} type="button" size="sm" variant={language === value ? 'default' : 'outline'}
                  onClick={() => pickLanguage(value)} data-testid={`create-language-${value}`}>
                  {t(`language.${value}`)}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label>{t('games:dmMode')}</Label>
            <div className="flex flex-wrap gap-2">
              {(['ai', 'human'] as const).map(value => (
                <Button key={value} type="button" size="sm" variant={dmMode === value ? 'default' : 'outline'}
                  onClick={() => setDmMode(value)} data-testid={`create-dm-${value}`}>
                  {t(`games:dm.${value}`)}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground" data-testid="create-dm-hint">
              {needCharacter ? t('games:createDmHintAi') : t('games:createDmHintHuman')}
            </p>
          </div>
          {needCharacter && (
            <div className="flex flex-col gap-2">
              <Label>{t('games:character')}</Label>
              {charactersQuery.isPending && <p role="status" className="text-sm text-muted-foreground">{t('loading')}</p>}
              {charactersEmpty ? (
                <p className="text-sm text-muted-foreground" data-testid="create-character-empty">{t('games:characterEmptyHint')}</p>
              ) : hasCharacters ? (
                <div className="create-character-picker" role="radiogroup" aria-label={t('games:character')}
                  data-testid="create-character-picker">
                  {characters.map(character => (
                    <button
                      key={character.id}
                      type="button"
                      role="radio"
                      aria-checked={characterId === character.id}
                      className={`create-character-tile ${characterId === character.id ? 'is-selected' : ''}`}
                      onClick={() => setCharacterId(character.id)}
                      data-testid="create-character-tile"
                      data-character-id={character.id}
                    >
                      {character.portrait ? (
                        <span className="avatar-tile block size-12 shrink-0 overflow-hidden rounded-md">
                          <AvatarImage name={character.name} image={character.portrait} />
                        </span>
                      ) : (
                        <span className="grid size-12 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
                          <UserIcon className="size-6" aria-hidden="true" />
                        </span>
                      )}
                      <span className="create-character-name">{character.name}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              <Button asChild variant="link" size="sm" className="self-start px-0">
                <Link to="/characters"><PlusIcon />{t('games:goCharacters')}</Link>
              </Button>
            </div>
          )}
          <div className="flex flex-col gap-2">
            <Label>{t('games:improvisation')}</Label>
            <div className="flex flex-wrap gap-2">
              {(['strict', 'moderate', 'free'] as const).map(value => (
                <Button key={value} type="button" size="sm" variant={improvisation === value ? 'default' : 'outline'}
                  onClick={() => setImprovisation(value)}>
                  {t(`games:improv.${value}`)}
                </Button>
              ))}
            </div>
          </div>
          <CreateVisibilityControl value={visibility} onChange={setVisibility} disabled={mutation.isPending} />
          {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="create-game-error">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || (needCharacter && !characterId)}
              data-testid="create-game-submit">{t('create')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function JoinGameDialog() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const addKnown = useKnownSessions(state => state.add)
  const [open, setOpen] = useState(false)
  const [invite, setInvite] = useState(() => new URLSearchParams(window.location.search).get('invite') ?? '')
  const [characterId, setCharacterId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const charactersQuery = useQuery({ queryKey: ['characters'], queryFn: portal.listCharacters, enabled: open })
  const characters = charactersQuery.data ?? []
  const hasList = characters.length > 0
  // D2: an empty 200 [] means "no characters yet" (create path), not "API down".
  const listEmpty = charactersQuery.isSuccess && !hasList
  const listFailed = charactersQuery.isError

  const mutation = useMutation({
    mutationFn: api.joinSession,
    onSuccess: data => {
      addKnown({
        id: data.session_id,
        invite_token: invite.trim(),
        language: '',
        dm_mode: '',
        role: data.seat.role === 'dm' ? 'dm' : 'player',
        created_at: new Date().toISOString(),
      })
      setOpen(false)
      setInvite('')
      setCharacterId('')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void navigate({ to: '/games/$sessionId', params: { sessionId: data.session_id } })
    },
    onError: err => setError(errorText(err)),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!invite.trim()) {
      setError(t('games:validation.invite'))
      return
    }
    const id = characterId.trim()
    if (listEmpty) {
      setError(t('games:characterEmptyHint'))
      return
    }
    if (hasList && !id) {
      setError(t('games:validation.character'))
      return
    }
    if (!uuidPattern.test(id)) {
      setError(t('games:validation.characterId'))
      return
    }
    mutation.mutate({ invite_token: invite.trim(), character_id: id })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" data-testid="join-game"><LogInIcon />{t('games:join')}</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">{t('games:joinTitle')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">{t('games:joinHint')}</p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="invite-token">{t('games:inviteToken')}</Label>
            <Input id="invite-token" value={invite} maxLength={64}
              onChange={event => setInvite(event.target.value)} data-testid="invite-token" />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="character-id">{t('games:character')}</Label>
            {charactersQuery.isPending && <p role="status" className="text-sm text-muted-foreground">{t('loading')}</p>}
            {hasList ? (
              <select id="character-id" className={selectClass} value={characterId}
                onChange={event => setCharacterId(event.target.value)} data-testid="character-select">
                <option value="">{t('games:characterSelect')}</option>
                {characters.map(character => (
                  <option key={character.id} value={character.id}>{character.name}</option>
                ))}
              </select>
            ) : listEmpty ? (
              <p className="text-sm text-muted-foreground" data-testid="character-empty-hint">{t('games:characterEmptyHint')}</p>
            ) : listFailed ? (
              <>
                <p className="text-sm text-destructive-foreground" role="alert" data-testid="character-list-error">{t('games:characterUnavailable')}</p>
                <Input id="character-id" value={characterId} placeholder="00000000-0000-0000-0000-000000000000"
                  onChange={event => setCharacterId(event.target.value)} data-testid="character-id" />
                <p className="text-xs text-muted-foreground">{t('games:characterFallbackHint')}</p>
              </>
            ) : null}
            <Button asChild variant="link" size="sm" className="self-start px-0">
              <Link to="/characters">{t('games:goCharacters')}</Link>
            </Button>
          </div>
          {error && <p className="text-sm text-destructive-foreground" role="alert" data-testid="join-game-error">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending} data-testid="join-game-submit">{t('games:join')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// Invite landing / hall seating (task #31): the table card and a one-click
// "sit down". Suitable characters are filtered by the same rule as
// realtime service.join_session: the character template must match the table's
// sheet template, and the character must not already be seated. `embedded`
// renders the body without the outer Card so it can live inside the hall
// seating dialog (the dialog supplies the title).
function InviteTableCard({ invite, autoCharacterId, embedded = false }: { invite: string; autoCharacterId?: string; embedded?: boolean }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const addKnown = useKnownSessions(state => state.add)
  const [selectedId, setSelectedId] = useState('')
  const [joinError, setJoinError] = useState<string | null>(null)
  const autoJoinStarted = useRef(false)

  const previewQuery = useQuery({
    queryKey: ['invite-preview', invite],
    queryFn: () => portal.sessionPreview(invite),
    retry: false,
  })
  const preview = previewQuery.data
  const charactersQuery = useQuery({
    queryKey: ['characters'],
    queryFn: portal.listCharacters,
    enabled: Boolean(preview),
  })
  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: portal.listSessions,
    enabled: Boolean(preview),
  })
  const alreadySeated = Boolean(preview && sessionsQuery.data?.some(item => item.id === preview.id))

  const characters = charactersQuery.data ?? []
  const suitable = characters.filter(character =>
    preview?.sheet_template_id != null
    && character.template_id === preview.sheet_template_id
    && !(preview.taken_character_ids ?? []).includes(character.id))
  const chosenId = selectedId || suitable[0]?.id || ''

  const joinMutation = useMutation({
    mutationFn: (characterId: string) => api.joinSession({ invite_token: invite, character_id: characterId }),
    onSuccess: data => {
      addKnown({
        id: data.session_id,
        invite_token: invite,
        scenario_version_id: preview?.scenario_version_id ?? undefined,
        scenario: preview?.scenario_title ?? preview?.scenario,
        language: preview?.language ?? '',
        dm_mode: preview?.dm_mode ?? '',
        role: data.seat.role === 'dm' ? 'dm' : 'player',
        created_at: new Date().toISOString(),
      })
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void navigate({ to: '/games/$sessionId', params: { sessionId: data.session_id } })
    },
    onError: err => setJoinError(errorText(err)),
  })

  // Наблюдатель (3.8): вход без персонажа, read-only экран игры.
  const observeMutation = useMutation({
    mutationFn: (sessionId: string) => portal.observeSession(sessionId),
    onSuccess: data => {
      addKnown({
        id: data.session_id,
        invite_token: invite,
        scenario_version_id: preview?.scenario_version_id ?? undefined,
        scenario: preview?.scenario_title ?? preview?.scenario,
        language: preview?.language ?? '',
        dm_mode: preview?.dm_mode ?? '',
        role: 'spectator',
        created_at: new Date().toISOString(),
      })
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
      void navigate({ to: '/games/$sessionId', params: { sessionId: data.session_id } })
    },
    onError: err => setJoinError(errorText(err)),
  })

  // Auto-seat after returning from character creation (?characterId=<new id>).
  useEffect(() => {
    if (!autoCharacterId || !preview || alreadySeated || autoJoinStarted.current) return
    autoJoinStarted.current = true
    joinMutation.mutate(autoCharacterId)
  }, [autoCharacterId, preview, alreadySeated, joinMutation])

  if (previewQuery.isPending) {
    return <p role="status" className="text-muted-foreground" data-testid="invite-preview-loading">{t('games:preview.loading')}</p>
  }
  if (previewQuery.isError || !preview) {
    return (
      <Card data-testid="invite-preview-error">
        <CardContent className="flex flex-col gap-3 pt-6 text-sm">
          <p className="text-destructive-foreground" role="alert">{errorText(previewQuery.error)}</p>
          <Button asChild variant="outline" size="sm" className="self-start">
            <Link to="/games" search={{}}>{t('games:preview.backToGames')}</Link>
          </Button>
        </CardContent>
      </Card>
    )
  }

  const isActive = preview.status === 'active'
  const body = (
    <>
      <div className="flex flex-wrap gap-2">
        <Badge>{t(`games:statusValues.${preview.status}`, { defaultValue: preview.status })}</Badge>
        <Badge variant="secondary">{t(`games:dm.${preview.dm_mode}`, { defaultValue: preview.dm_mode })}</Badge>
        <Badge variant="outline" className="uppercase">{preview.language}</Badge>
      </div>
      {preview.host_name && <p className="text-muted-foreground">{t('games:preview.host', { name: preview.host_name })}</p>}
      <p className="text-muted-foreground" data-testid="invite-seats">
        {preview.seats_total != null
          ? t('games:preview.seatsOf', { taken: preview.seats_taken, total: preview.seats_total })
          : t('games:preview.seats', { taken: preview.seats_taken })}
      </p>
      {alreadySeated ? (
        <div className="flex flex-col gap-2">
          <p data-testid="already-seated">{t('games:preview.alreadySeated')}</p>
          <Button asChild size="sm" className="self-start" data-testid="enter-seated-game">
            <Link to="/games/$sessionId" params={{ sessionId: preview.id }}>{t('games:enter')}</Link>
          </Button>
        </div>
      ) : !isActive ? (
        <p className="text-destructive-foreground" role="alert" data-testid="invite-stopped">{t('games:preview.stopped')}</p>
      ) : charactersQuery.isPending ? (
        <p role="status" className="text-muted-foreground">{t('loading')}</p>
      ) : charactersQuery.isError ? (
        <div role="alert" className="flex items-center gap-3 text-destructive-foreground">
          <span>{t('games:preview.charactersUnavailable')}</span>
          <Button variant="outline" size="sm" onClick={() => charactersQuery.refetch()}>{t('retry')}</Button>
        </div>
      ) : suitable.length > 0 ? (
        <div className="flex flex-col gap-2">
          {suitable.length > 1 && (
            <select className={selectClass} value={chosenId} aria-label={t('games:character')}
              onChange={event => setSelectedId(event.target.value)} data-testid="invite-character-select">
              {suitable.map(character => (
                <option key={character.id} value={character.id}>{character.name}</option>
              ))}
            </select>
          )}
          <Button size="sm" className="self-start" disabled={joinMutation.isPending || !chosenId}
            onClick={() => joinMutation.mutate(chosenId)} data-testid="sit-at-table">
            <LogInIcon />{t('games:preview.sit')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-muted-foreground" data-testid="invite-no-character">
            {characters.length === 0 ? t('games:preview.noCharacters') : t('games:preview.noMatching')}
          </p>
          <Button asChild size="sm" className="self-start" data-testid="create-for-table">
            <Link to="/characters" search={{ scenarioVersionId: preview.scenario_version_id ?? undefined, invite }}>
              <PlusIcon />{t('games:preview.createForTable')}
            </Link>
          </Button>
        </div>
      )}
      {/* Смотреть за столом: персонаж не нужен, доступ только к public-ленте. */}
      {isActive && !alreadySeated && (
        <Button variant="outline" size="sm" className="self-start"
          disabled={observeMutation.isPending || joinMutation.isPending}
          onClick={() => observeMutation.mutate(preview.id)} data-testid="watch-table">
          <EyeIcon />{t('games:preview.watch')}
        </Button>
      )}
      {joinError && <p className="text-destructive-foreground" role="alert" data-testid="invite-join-error">{joinError}</p>}
    </>
  )

  if (embedded) {
    return <div className="hall-seat-body flex flex-col gap-3 text-sm" data-testid="invite-table-card">{body}</div>
  }
  return (
    <Card data-testid="invite-table-card">
      <CardHeader>
        <p className="text-xs uppercase tracking-wide text-muted-foreground">{t('games:preview.title')}</p>
        <CardTitle className="font-serif text-2xl">{preview.scenario_title ?? preview.scenario}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">{body}</CardContent>
    </Card>
  )
}

// Deterministic atmospheric mood per table, drawn from the hall palette of
// story tones. Purely presentational flavour keyed by the table id.
function moodKey(id: string): string {
  let hash = 0
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return `games:hall.mood${hash % 6}`
}

// Лидерские действия над столом в зале: закрыть активный стол (stop) либо
// удалить уже закрытый (archive). Оба — только лидер, оба требуют подтверждения
// (второй клик). stop переводит стол в "stopped"; archive скрывает стол из
// списков (идемпотентно). Бэкенд: archive работает только для stopped-стола,
// иначе 409 session_not_stopped (локализуется через onErrorText выше).
function LeaderTableActions({
  table,
  onClose,
  onDelete,
  pending,
  error,
}: {
  table: HallTable
  onClose: (table: HallTable) => void
  onDelete: (table: HallTable) => void
  pending: boolean
  error: string | null
}) {
  const { t } = useTranslation()
  const [confirm, setConfirm] = useState<'close' | 'delete' | null>(null)
  const isActive = table.status === 'active'
  const isStopped = table.status === 'stopped'

  // После успешного закрытия стол становится stopped: сбрасываем подтверждение,
  // чтобы показать кнопку удаления вместо «закрыть».
  useEffect(() => {
    if (confirm === 'close' && !isActive) setConfirm(null)
  }, [confirm, isActive])

  if (confirm === 'close') {
    return (
      <div className="hall-leader-action" data-testid="leader-close-confirm">
        <p className="hall-leader-confirm-text">{t('games:hall.closeConfirm')}</p>
        <div className="hall-leader-buttons">
          <button type="button" className="text-button danger" disabled={pending}
            onClick={() => onClose(table)} data-testid="leader-close-yes">{t('games:hall.closeYes')}</button>
          <button type="button" className="text-button" disabled={pending}
            onClick={() => setConfirm(null)} data-testid="leader-close-cancel">{t('games:hall.actionCancel')}</button>
        </div>
        {error && <p className="hall-recruit-error" role="alert" data-testid="leader-action-error">{error}</p>}
      </div>
    )
  }
  if (confirm === 'delete') {
    return (
      <div className="hall-leader-action" data-testid="leader-delete-confirm">
        <p className="hall-leader-confirm-text">{t('games:hall.deleteConfirm')}</p>
        <div className="hall-leader-buttons">
          <button type="button" className="text-button danger" disabled={pending}
            onClick={() => onDelete(table)} data-testid="leader-delete-yes">{t('games:hall.deleteYes')}</button>
          <button type="button" className="text-button" disabled={pending}
            onClick={() => setConfirm(null)} data-testid="leader-delete-cancel">{t('games:hall.actionCancel')}</button>
        </div>
        {error && <p className="hall-recruit-error" role="alert" data-testid="leader-action-error">{error}</p>}
      </div>
    )
  }

  if (!isActive && !isStopped) return null
  return (
    <div className="hall-leader-action">
      {isActive && (
        <button type="button" className="text-button" disabled={pending}
          onClick={() => setConfirm('close')} data-testid="leader-close">{t('games:hall.closeTable')}</button>
      )}
      {isStopped && (
        <button type="button" className="text-button danger" disabled={pending}
          onClick={() => setConfirm('delete')} data-testid="leader-delete">{t('games:hall.deleteTable')}</button>
      )}
      {error && <p className="hall-recruit-error" role="alert" data-testid="leader-action-error">{error}</p>}
    </div>
  )
}

function HallDetails({
  table,
  onEnter,
  onApplyRecruiting,
  recruitPending,
  recruitError,
  onCloseTable,
  onDeleteTable,
  leaderPending,
  leaderError,
  ref,
}: {
  table: HallTable
  onEnter: () => void
  onApplyRecruiting: (table: HallTable, next: RecruitingRequest) => void
  recruitPending: boolean
  recruitError: string | null
  onCloseTable: (table: HallTable) => void
  onDeleteTable: (table: HallTable) => void
  leaderPending: boolean
  leaderError: string | null
  ref?: Ref<HTMLElement>
}) {
  const { t } = useTranslation()
  const seats =
    table.seatsTaken != null
      ? table.seatsTotal != null
        ? t('games:hall.seatsOf', { taken: table.seatsTaken, total: table.seatsTotal })
        : t('games:hall.seatsTaken', { taken: table.seatsTaken })
      : t('games:hall.seatsUnknown')
  const free =
    table.seatsTotal != null && table.seatsTaken != null
      ? Math.max(0, table.seatsTotal - table.seatsTaken)
      : null

  return (
    <aside className="table-details" aria-live="polite" data-testid="hall-details" ref={ref}>
      <div className="detail-top">
        <span>{t('games:hall.detailTop')}</span>
        <span>{table.mine ? t('games:hall.mineTag') : t('games:hall.openTag')}</span>
      </div>
      <div className="detail-symbol" aria-hidden="true">✧</div>
      <p className="detail-kicker">
        {table.hostName ? t('games:preview.host', { name: table.hostName }) : t('games:hall.kicker')}
      </p>
      <h2 data-testid="hall-details-title">{table.title}</h2>

      <div className="hall-badges">
        <span className="hall-badge">{t(`games:statusValues.${table.status}`, { defaultValue: table.status })}</span>
        <span className="hall-badge hall-badge-alt">{t(`games:dm.${table.dmMode}`, { defaultValue: table.dmMode })}</span>
        {table.language && <span className="hall-badge hall-badge-line uppercase">{table.language}</span>}
        {table.mine && <span className="hall-badge hall-badge-mine">{t(`games:role.${table.role}`, { defaultValue: table.role ?? '' })}</span>}
      </div>

      <div className="detail-meta">
        <div>
          <span>{t('games:hall.metaTravelers')}</span>
          <strong data-testid="hall-seats">{seats}</strong>
        </div>
        <div>
          <span>{t('games:hall.metaFree')}</span>
          <strong>{free != null ? free : table.seatsTotal == null ? '∞' : '—'}</strong>
        </div>
      </div>

      {table.mine ? (
        <button
          type="button"
          className="primary-button"
          onClick={onEnter}
          data-testid="approach-table"
        >
          {t('games:enter')} <span>→</span>
        </button>
      ) : table.inviteToken ? (
        <div className="hall-seat-embed">
          <InviteTableCard invite={table.inviteToken} embedded />
        </div>
      ) : (
        <p className="hall-recruit-error" role="alert" data-testid="hall-invite-missing">{t('games:hall.inviteMissing')}</p>
      )}

      <div className="detail-mood">✦ &nbsp; {t(moodKey(table.id))}</div>

      {table.isLeader && (
        <>
          <HallRecruitingControl table={table} pending={recruitPending} onApply={next => onApplyRecruiting(table, next)} />
          {recruitError && <p className="hall-recruit-error" role="alert" data-testid="recruiting-error">{recruitError}</p>}
          {table.inviteToken && (
            <div className="hall-invite-row">
              <InviteCopy token={table.inviteToken} />
            </div>
          )}
          <LeaderTableActions
            table={table}
            onClose={onCloseTable}
            onDelete={onDeleteTable}
            pending={leaderPending}
            error={leaderError}
          />
        </>
      )}

      <p className="detail-description">{t('games:hall.description', { title: table.title })}</p>
      <p className="detail-foot">
        {table.mine ? t('games:hall.footMine') : t('games:hall.footOpen')}
      </p>
    </aside>
  )
}

export function GamesPage() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const { invite, characterId } = useSearch({ from: '/games' })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const known = useKnownSessions(state => state.sessions)

  const sessionsQuery = useQuery({ queryKey: ['sessions'], queryFn: portal.listSessions })
  const openQuery = useQuery({ queryKey: ['open-tables'], queryFn: openTables })
  const sessions: SessionSummary[] = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data])
  const open: OpenTableItem[] = useMemo(() => openQuery.data ?? [], [openQuery.data])
  const openMap = useMemo(() => new Map(open.map(item => [item.id, item])), [open])

  // «Мои» столы несут название/места/хозяина прямо в списке сессий; превью нужно
  // только строкам открытого набора без scenario_title, чтобы карточка показывала
  // локализованное название вместо сырого slug.
  const previewTokens = useMemo(() => {
    const tokens = new Set<string>()
    for (const item of open) {
      if (!item.scenario_title && item.invite_token) tokens.add(item.invite_token)
    }
    return [...tokens].sort()
  }, [open])

  const previewsQuery = useQuery({
    queryKey: ['hall-previews', previewTokens],
    queryFn: async () => {
      const list = await Promise.all(previewTokens.map(token => portal.sessionPreview(token).catch(() => null)))
      const map: Record<string, SessionPreview> = {}
      for (const preview of list) if (preview) map[preview.id] = preview
      return map
    },
    enabled: previewTokens.length > 0,
  })
  const previews = useMemo(() => previewsQuery.data ?? {}, [previewsQuery.data])

  // Leader recruiting edits land optimistically here, then reconcile on refetch.
  const [overrides, setOverrides] = useState<Record<string, RecruitingRequest>>({})
  const [recruitError, setRecruitError] = useState<string | null>(null)
  const recruitMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: RecruitingRequest }) => setRecruiting(id, body),
    onSuccess: (data, variables) => {
      setOverrides(previous => ({ ...previous, [variables.id]: { recruiting: data.recruiting, seats_total: data.seats_total ?? null } }))
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
      void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
    },
    onError: error => setRecruitError(errorText(error)),
  })

  // Лидерские операции над столом: закрыть активный стол (stop → stopped) и
  // удалить уже закрытый (archive → скрывается из списков). Обе host-only;
  // archive для не-stopped стола вернёт 409 session_not_stopped.
  const [leaderError, setLeaderError] = useState<string | null>(null)
  const invalidateHall = () => {
    void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    void queryClient.invalidateQueries({ queryKey: ['open-tables'] })
    void queryClient.invalidateQueries({ queryKey: ['hall-previews'] })
  }
  const stopMutation = useMutation({
    mutationFn: (id: string) => portal.stopSession(id),
    onSuccess: () => { setLeaderError(null); invalidateHall() },
    onError: error => setLeaderError(errorText(error)),
  })
  const archiveMutation = useMutation({
    mutationFn: (id: string) => portal.archiveSession(id),
    onSuccess: () => { setLeaderError(null); invalidateHall() },
    onError: error => setLeaderError(errorText(error)),
  })
  const leaderPending = stopMutation.isPending || archiveMutation.isPending

  function closeTable(table: HallTable) {
    setLeaderError(null)
    stopMutation.mutate(table.id)
  }
  function deleteTable(table: HallTable) {
    setLeaderError(null)
    archiveMutation.mutate(table.id)
  }

  const tables = useMemo<HallTable[]>(() => {
    const result: HallTable[] = []
    const seen = new Set<string>()
    for (const session of sessions) {
      const openItem = openMap.get(session.id)
      const preview = previews[session.id]
      const remembered = known.find(item => item.id === session.id)
      const override = overrides[session.id]
      result.push({
        id: session.id,
        title: session.scenario_title ?? openItem?.scenario_title ?? preview?.scenario_title ?? remembered?.scenario ?? session.scenario,
        scenario: session.scenario,
        language: session.language || openItem?.language || preview?.language || '',
        dmMode: session.dm_mode || openItem?.dm_mode || preview?.dm_mode || '',
        status: session.status,
        role: session.role,
        mine: true,
        isLeader: Boolean(session.is_host),
        seatsTaken: session.seats_taken ?? openItem?.seats_taken ?? preview?.seats_taken ?? null,
        seatsTotal: override?.seats_total ?? session.seats_total ?? openItem?.seats_total ?? preview?.seats_total ?? null,
        hostName: session.host_name ?? openItem?.host_name ?? preview?.host_name ?? null,
        inviteToken: session.invite_token ?? openItem?.invite_token ?? remembered?.invite_token ?? null,
        recruiting: override?.recruiting ?? Boolean(openItem),
        open: Boolean(openItem),
      })
      seen.add(session.id)
    }
    for (const openItem of open) {
      if (seen.has(openItem.id)) continue
      const override = overrides[openItem.id]
      result.push({
        id: openItem.id,
        title: openItem.scenario_title ?? previews[openItem.id]?.scenario_title ?? openItem.scenario,
        scenario: openItem.scenario,
        language: openItem.language,
        dmMode: openItem.dm_mode,
        status: 'active',
        role: null,
        mine: false,
        isLeader: false,
        seatsTaken: openItem.seats_taken,
        seatsTotal: override?.seats_total ?? openItem.seats_total ?? null,
        hostName: openItem.host_name ?? previews[openItem.id]?.host_name ?? null,
        inviteToken: openItem.invite_token,
        recruiting: override?.recruiting ?? true,
        open: true,
      })
      seen.add(openItem.id)
    }
    return result
  }, [sessions, open, openMap, previews, known, overrides])

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = tables.find(table => table.id === selectedId) ?? null
  const detailsRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!selectedId || !detailsRef.current) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    detailsRef.current.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' })
  }, [selectedId])

  function toggleTable(id: string) {
    setSelectedId(previous => (previous === id ? null : id))
  }

  function applyRecruiting(table: HallTable, next: RecruitingRequest) {
    setRecruitError(null)
    setOverrides(previous => ({ ...previous, [table.id]: next }))
    recruitMutation.mutate({ id: table.id, body: next })
  }

  function enterTable(table: HallTable) {
    void navigate({ to: '/games/$sessionId', params: { sessionId: table.id } })
  }

  function closeSeating() {
    void navigate({ to: '/games', search: {} })
  }

  const loading = sessionsQuery.isPending || openQuery.isPending
  const queryError = sessionsQuery.error ?? openQuery.error
  const isEmpty = !loading && tables.length === 0

  return (
    <div className="app codex-demo hall-page">
      <main className="hall scene">
        <div className="hall-heading">
          <div className="eyebrow"><span /> {t('games:hall.eyebrow')}</div>
          <h1 tabIndex={-1}>{t('games:hall.titleLine1')}<br /><em>{t('games:hall.titleLine2')}</em></h1>
          <p>{t('games:hall.lead')}</p>
          <div className="hall-actions">
            <JoinGameDialog />
            <CreateGameDialog />
          </div>
        </div>

        {queryError && (
          <div className="hall-alert" role="alert" data-testid="hall-error">
            <span>{apiErrorMessage(queryError)}</span>
            <Button variant="outline" size="sm" onClick={() => { void sessionsQuery.refetch(); void openQuery.refetch() }}>
              {t('retry')}
            </Button>
          </div>
        )}

        {loading ? (
          <div className="hall-loading" role="status" data-testid="hall-loading">
            <div className="floor-label"><span>✧</span> {t('games:hall.floorLabel')} <span>✧</span></div>
            <div className="hall-loading-row" aria-hidden="true">
              <span className="hall-loading-table" /><span className="hall-loading-table" /><span className="hall-loading-table" />
            </div>
            <p className="floor-hint">{t('loading')}</p>
          </div>
        ) : isEmpty ? (
          <div className="hall-empty" data-testid="sessions-empty">
            <div className="detail-symbol" aria-hidden="true">✧</div>
            <h2>{t('games:hall.emptyTitle')}</h2>
            <p>{t('games:empty')}</p>
            <div className="hall-actions">
              <JoinGameDialog />
              <CreateGameDialog />
            </div>
          </div>
        ) : (
          <div className="hall-layout">
            <div className="hall-floor" aria-label={t('games:hall.floorAria')} data-testid="hall-floor">
              <div className="floor-label"><span>✧</span> {t('games:hall.floorLabel')} <span>✧</span></div>
              <HallCarousel count={tables.length}>
                {tables.map((table, index) => (
                  <HallTableChoice
                    key={table.id}
                    table={table}
                    index={index}
                    active={selected?.id === table.id}
                    onSelect={toggleTable}
                  />
                ))}
              </HallCarousel>
              {selected && (
                <HallDetails
                  key={selected.id}
                  ref={detailsRef}
                  table={selected}
                  onEnter={() => enterTable(selected)}
                  onApplyRecruiting={applyRecruiting}
                  recruitPending={recruitMutation.isPending}
                  recruitError={recruitError}
                  onCloseTable={closeTable}
                  onDeleteTable={deleteTable}
                  leaderPending={leaderPending}
                  leaderError={leaderError}
                />
              )}
              <p className="floor-hint">{t('games:hall.floorHint')} <span>↗</span></p>
            </div>
          </div>
        )}

        <footer className="scene-footer">
          <span>✦ &nbsp; {t('games:hall.footerLeft')}</span>
          <span>{t('games:hall.footerRight')} &nbsp; ✦</span>
        </footer>
      </main>

      <Dialog open={Boolean(invite)} onOpenChange={open => { if (!open) closeSeating() }}>
        <DialogContent className="hall-seat-dialog">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">{t('games:preview.title')}</DialogTitle>
          </DialogHeader>
          {invite && <InviteTableCard invite={invite} autoCharacterId={characterId} embedded />}
        </DialogContent>
      </Dialog>
    </div>
  )
}