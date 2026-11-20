import { useEffect, useRef, useState, type FormEvent } from 'react'
import { demoSource, type Character, type Command, type Entry, type Setup, type Table } from './demo'
import { DiceRenderer, type D20Value, type DiceStyle } from '@/game/dice-renderer'
import { AvatarImage } from '@/game/avatar'
import { EntryItem, Ornament, StoryCard } from '@/game/story'
import { ActionForm } from '@/game/action-form'
import { CharacterCard } from '@/game/character-card'
import { PartyPanel } from '@/game/party-panel'
import { CommandPanel } from '@/game/command-panel'
import { GateAmbience } from './GateAmbience'
import './demo.css'

const diceExamples: readonly D20Value[] = [1, 7, 20]

type Screen = 'gate' | 'opening' | 'hall' | 'approaching' | 'setup' | 'game'
type Role = 'player' | 'gm'
type TableState = { character: Character; role: Role; entries: Entry[]; action: string; narration: string; dice: { value: D20Value; eventId: number; appearance: DiceStyle } }
type LoadState = { status: 'loading' } | { status: 'error' } | { status: 'ready'; setup: Setup }

function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true">✦</span><span>ПОРТАЛ <b>ИСТОРИЙ</b><small>AI DND PORTAL</small></span></div>
}

function DemoApp() {
  const [screen, setScreen] = useState<Screen>('gate')
  const [selected, setSelected] = useState<Table>(demoSource.tables[0])
  const [activeTable, setActiveTable] = useState(demoSource.tables[0].id)
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [states, setStates] = useState<Record<string, TableState>>({})
  const [request, setRequest] = useState(0)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const lastTable = useRef<string | null>(null)
  const needsSetup = screen === 'approaching' || screen === 'setup'

  useEffect(() => {
    if (screen === 'opening' || screen === 'approaching') {
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const timer = window.setTimeout(() => setScreen(screen === 'opening' ? 'hall' : 'setup'), reduced ? 0 : 850)
      return () => window.clearTimeout(timer)
    }
    if (screen === 'hall' && lastTable.current) {
      document.getElementById(`table-${lastTable.current}`)?.focus()
      lastTable.current = null
    } else {
      headingRef.current?.focus()
    }
  }, [screen])

  useEffect(() => {
    if (!needsSetup) return
    let cancelled = false
    demoSource.loadSetup(selected.id).then(model => {
      if (cancelled) return
      setStates(previous => previous[selected.id] ? previous : { ...previous, [selected.id]: { character: demoSource.initialCharacter(model), role: 'player', entries: [], action: '', narration: '', dice: { value: 7, eventId: 0, appearance: 'amber' } } })
      setLoad({ status: 'ready', setup: model })
    }).catch(() => { if (!cancelled) setLoad({ status: 'error' }) })
    return () => { cancelled = true }
  }, [selected.id, request, needsSetup])

  function enter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (screen !== 'gate') return
    event.currentTarget.reset()
    setScreen('opening')
  }
  function openTable(table: Table) {
    setSelected(table)
    setLoad({ status: 'loading' })
    setScreen('approaching')
  }
  function backToHall() {
    lastTable.current = selected.id
    setActiveTable(selected.id)
    setScreen('hall')
  }
  function updateState(update: (state: TableState) => TableState) {
    setStates(previous => previous[selected.id] ? { ...previous, [selected.id]: update(previous[selected.id]) } : previous)
  }
  function addEntry(entry: Entry, clear: 'action' | 'narration' | null = null) {
    updateState(state => ({ ...state, entries: [...state.entries, entry], ...(clear ? { [clear]: '' } : {}) }))
  }
  function submitText(kind: 'action' | 'narration') {
    const text = state?.[kind].trim()
    if (text) addEntry(kind === 'action' ? demoSource.submitAction(text) : demoSource.submitNarration(text), kind)
  }

  const gateVisible = screen === 'gate' || screen === 'opening'
  const hallVisible = screen === 'hall' || screen === 'approaching'
  const active = demoSource.tables.find(table => table.id === activeTable) ?? demoSource.tables[0]
  const state = states[selected.id]
  const model = load.status === 'ready' ? load.setup : null
  const avatar = model?.avatars.find(item => item.id === state?.character.avatar)

  return <div className={`app codex-demo screen-${screen}`}>
    {gateVisible && <main className="gate scene">
      <GateAmbience />
      <div className="gate-doors" aria-hidden="true"><div /><div /></div>
      <header className="site-header"><Brand /><span className="header-note"><span className="status-dot" /> ДЕМО-ВЕРСИЯ</span></header>
      <div className="gate-content" inert={screen === 'opening'}>
        <div className="gate-intro"><div className="eyebrow"><span /> МЕСТО, ГДЕ НАЧИНАЮТСЯ ИСТОРИИ</div><h1 ref={headingRef} tabIndex={-1}>Войди в мир,<br /><em>созданный для историй.</em></h1><p>Здесь за каждым столом ждёт приключение,<br className="desktop-break" /> а каждая история начинается с тебя.</p><div className="intro-signature"><span>✧</span> ТВОЁ ПРИКЛЮЧЕНИЕ ЖДЁТ</div></div>
        <section className="login-panel" aria-labelledby="login-title"><div className="panel-top"><span>✧</span><span>ДОБРО ПОЖАЛОВАТЬ</span><span>✧</span></div><h2 id="login-title">Врата открыты</h2><p className="panel-lead">Займи своё место у огня.</p><Ornament />
          <form onSubmit={enter} autoComplete="off"><label htmlFor="traveler">ИМЯ ПУТНИКА <span>необязательно</span></label><input id="traveler" placeholder="Как тебя называть?" maxLength={60} autoComplete="off" /><label htmlFor="password">ПАРОЛЬ <span>необязательно</span></label><input id="password" type="password" placeholder="••••••••" autoComplete="new-password" /><button className="primary-button" type="submit">ВОЙТИ В ПОРТАЛ <span>→</span></button></form>
          <p className="privacy-note"><span aria-hidden="true">◇</span> Это условный вход, не авторизация. Данные не отправляются, не сохраняются и не записываются в журнал.</p>
        </section>
      </div><footer className="scene-footer"><span>✦ &nbsp; ИСТОРИИ ЖДУТ СВОИХ ГЕРОЕВ</span><span>ПРИКЛЮЧЕНИЕ НАЧИНАЕТСЯ ЗДЕСЬ &nbsp; ✦</span></footer>
    </main>}
    {hallVisible && <main className="hall scene">
      <header className="site-header"><Brand /><div className="header-actions"><span className="header-note"><span className="status-dot" /> ДЕМО-РЕЖИМ</span><button className="text-button" onClick={() => setScreen('gate')} disabled={screen === 'approaching'}>Выйти <span>↗</span></button></div></header>
      <div className="hall-heading"><div className="eyebrow"><span /> ДОБРО ПОЖАЛОВАТЬ В ЗАЛ</div><h1 ref={headingRef} tabIndex={-1}>У каждого стола <em>своя история.</em></h1><p>Присмотрись к столам. Возможно, одна из этих историй ждёт именно тебя.</p></div>
      <div className="hall-layout"><div className="hall-floor" aria-label="Игровые столы"><div className="floor-label"><span>✧</span> ВЫБЕРИ СВОЁ ПРИКЛЮЧЕНИЕ <span>✧</span></div><div className="table-row">{demoSource.tables.map((table, index) => <button key={table.id} id={`table-${table.id}`} className={`table-choice table-${index + 1} ${activeTable === table.id ? 'is-active' : ''} ${screen === 'approaching' && selected.id === table.id ? 'is-approaching' : ''}`} onMouseEnter={() => setActiveTable(table.id)} onFocus={() => setActiveTable(table.id)} onClick={() => openTable(table)} disabled={screen === 'approaching'} aria-label={`${table.title}, ${table.players} игроков. Открыть стол`}><span className="table-shadow" /><span className="table-top"><span className="table-rim" /><span className="table-map">⌁<br />✧</span><span className="table-candle">✦</span><span className="table-dice">⬡</span><span className="table-cup">◉</span></span><span className="table-name"><small>СТОЛ {table.number}</small>{table.title}</span></button>)}</div><p className="floor-hint">Наведи на стол или выбери его с клавиатуры <span>↗</span></p></div>
        <aside className="table-details" aria-live="polite"><div className="detail-top"><span>ИСТОРИЯ ЗА СТОЛОМ</span><span>№ {active.number}</span></div><div className="detail-symbol">✧</div><p className="detail-kicker">{active.subtitle}</p><h2>{active.title}</h2><p className="detail-description">{active.description}</p><div className="detail-meta"><div><span>ПУТНИКИ</span><strong>{active.players}</strong></div><div><span>ДЛЯ ГЕРОЕВ</span><strong>{active.level}</strong></div></div><div className="detail-mood">✦ &nbsp; {active.mood}</div><button className="primary-button" onClick={() => openTable(active)} disabled={screen === 'approaching'}>ПОДОЙТИ К СТОЛУ <span>→</span></button><p className="detail-foot">Демонстрационные столы · без подключения</p></aside></div>
      <footer className="scene-footer"><span>✦ &nbsp; ЗАЛ ИСТОРИЙ</span><span>ВЫБЕРИ СТОЛ, ЧТОБЫ НАЧАТЬ &nbsp; ✦</span></footer>
    </main>}
    {(screen === 'setup' || screen === 'game') && <main className="game scene">
      <header className="site-header game-header"><Brand /><div className="game-header-center"><span>СТОЛ {selected.number} · ЛОКАЛЬНОЕ ДЕМО</span><strong>{selected.title}</strong></div><div className="header-actions"><span className="header-note"><span className="status-dot" /> БЕЗ СЕРВЕРА</span><button className="text-button" onClick={backToHall}>← В зал</button></div></header>
      {screen === 'setup' && <div className="setup-layout"><div className="setup-heading"><div className="eyebrow"><span /> СТОЛ {selected.number} · {selected.title}</div><h1 ref={headingRef} tabIndex={-1}>Твоё место <em>за столом.</em></h1><p>Выберите роль и подготовьтесь к локальной демонстрации.</p></div>
        <div className="demo-banner">Вход, данные, действия и ведущий условны. Сервер и ИИ не подключены; всё хранится только до обновления страницы.</div>
        {load.status === 'loading' && <p className="load-message" role="status">Загружаем демо-данные карточки…</p>}
        {load.status === 'error' && <div className="load-message" role="alert"><p>Не удалось загрузить демо-данные карточки.</p><button className="secondary-button" onClick={() => { setLoad({ status: 'loading' }); setRequest(value => value + 1) }}>Попробовать снова</button></div>}
        {model && state && <><fieldset className="role-picker"><legend>Кем вы хотите быть?</legend><label><input type="radio" name="role" checked={state.role === 'player'} onChange={() => updateState(current => ({ ...current, role: 'player' }))} /><span><strong>Игрок</strong><small>Создать героя и исследовать историю</small></span></label><label><input type="radio" name="role" checked={state.role === 'gm'} onChange={() => updateState(current => ({ ...current, role: 'gm' }))} /><span><strong>Ведущий</strong><small>Самостоятельно описывать происходящее</small></span></label></fieldset>
          {state.role === 'player' ? <div className="setup-grid"><section className="setup-card" aria-labelledby="character-title"><div className="section-kicker">ВАШ ГЕРОЙ · ДЕМО</div><h2 id="character-title">Карточка персонажа</h2><p className="help-text">Имя и образ можно изменить в любой момент.</p><label className="field-label" htmlFor="character-name">Имя персонажа</label><input className="field-input" id="character-name" value={state.character.name} maxLength={60} placeholder="Например, Эйрин" onChange={event => { const value = event.target.value; updateState(current => ({ ...current, character: { ...current.character, name: value } })) }} /><label className="field-label" htmlFor="character-concept">Образ или занятие</label><input className="field-input" id="character-concept" value={state.character.concept} maxLength={80} placeholder="Например, хранитель троп" onChange={event => { const value = event.target.value; updateState(current => ({ ...current, character: { ...current.character, concept: value } })) }} />
            <div className="stats-heading"><div><h3>Характеристики</h3><p>Демо-модель, не правила SRD или настоящей игры.</p></div><div className="points" aria-live="polite"><strong>{demoSource.remaining(model, state.character)}</strong><span>из {model.points} очков<br />осталось</span></div></div><p className="help-text">Каждый пункт сверх минимума стоит одно очко. Диапазон указан у каждого параметра.</p><div className="stat-list">{model.stats.map(stat => { const value = state.character.stats[stat.id]; const remaining = demoSource.remaining(model, state.character); return <div className="stat-row" key={stat.id}><div><strong>{stat.label}</strong><small>{stat.min}–{stat.max}</small></div><div className="stepper"><button type="button" aria-label={`Уменьшить: ${stat.label}`} disabled={value <= stat.min} onClick={() => updateState(current => ({ ...current, character: demoSource.changeStat(model, current.character, stat.id, current.character.stats[stat.id] - 1) }))}>−</button><output aria-label={`${stat.label}: ${value}`}>{value}</output><button type="button" aria-label={`Увеличить: ${stat.label}`} disabled={value >= stat.max || remaining <= 0} onClick={() => updateState(current => ({ ...current, character: demoSource.changeStat(model, current.character, stat.id, current.character.stats[stat.id] + 1) }))}>+</button></div></div> })}</div></section>
            <fieldset className="setup-card avatar-picker"><legend>Выберите портрет</legend><p className="help-text">Все шесть образов доступны каждому; выбор не ограничивает персонажа.</p><div className="avatar-grid">{model.avatars.map(item => <label className="avatar-option" key={item.id}><input type="radio" name="avatar" checked={state.character.avatar === item.id} onChange={() => updateState(current => ({ ...current, character: { ...current.character, avatar: item.id } }))} /><span className="avatar-option-inner"><AvatarImage name={item.name} image={item.image} /><strong>{item.name}</strong></span></label>)}</div></fieldset></div> : <section className="setup-card gm-intro"><div className="section-kicker">ЖИВОЙ ВЕДУЩИЙ · ДЕМО</div><h2>История в ваших руках</h2><p>В демонстрационной панели можно писать описание сцены и добавлять условные команды. Это не ИИ и не подключение к реальным игрокам.</p><p>Карточка игрока останется в памяти при переключении роли.</p></section>}
          <div className="setup-actions"><button className="secondary-button" onClick={backToHall}>← Вернуться в зал</button><button className="primary-button" onClick={() => setScreen('game')}>{state.role === 'player' ? 'ОТКРЫТЬ ЭКРАН ИГРОКА' : 'ОТКРЫТЬ ПАНЕЛЬ ВЕДУЩЕГО'} <span>→</span></button></div></>}
      </div>}
      {screen === 'game' && model && state && <div className="game-layout"><section className="story-column" aria-labelledby="game-title"><div className="story-heading"><div><div className="eyebrow"><span /> {state.role === 'player' ? 'ЭКРАН ИГРОКА' : 'ПАНЕЛЬ ВЕДУЩЕГО'} · ДЕМО</div><h1 id="game-title" ref={headingRef} tabIndex={-1}>{state.role === 'player' ? selected.title : 'Пульт ведущего'}</h1><p>{state.role === 'player' ? 'Глава первая · Начало пути' : selected.title}</p></div><div className="chapter-number">01<small>ГЛАВА</small></div></div>
        <div className="demo-banner">Локальная демонстрация: нет сервера, ИИ, настоящих игроков или ведущего. Действия и команды не влияют на игру.</div>
        <div className="game-navigation"><button className="secondary-button" onClick={() => setScreen('setup')}>← Роль и персонаж</button><button className="secondary-button" onClick={() => updateState(current => ({ ...current, role: current.role === 'player' ? 'gm' : 'player' }))}>Перейти к виду {state.role === 'player' ? 'ведущего' : 'игрока'}</button></div>
        <section className="dice-panel" aria-labelledby="dice-title"><div className="dice-panel-heading"><div><div className="section-kicker">ЛОКАЛЬНЫЙ ПРИМЕР · DEMO</div><h2 id="dice-title">Грань судьбы</h2><p>Визуальное превью заданных значений, не игровой бросок.</p></div><span aria-hidden="true">✧</span></div>
          <div className="dice-panel-content"><DiceRenderer value={state.dice.value} eventId={state.dice.eventId} appearance={state.dice.appearance} /><div className="dice-controls"><fieldset><legend>Показать пример d20</legend><div className="dice-example-buttons">{diceExamples.map(value => <button key={value} type="button" className="secondary-button" onClick={() => updateState(current => ({ ...current, dice: { ...current.dice, value, eventId: current.dice.eventId + 1 } }))}>Показать {value}</button>)}</div><p>Повторное нажатие воспроизводит анимацию.</p></fieldset><fieldset><legend>Внешний вид</legend><div className="dice-style-options">{([['amber', 'Янтарь'], ['jade', 'Нефрит'], ['amethyst', 'Аметист'], ['gold', 'Золото'], ['malachite', 'Малахит'], ['ruby', 'Рубин']] as const).map(([appearance, label]) => <label key={appearance}><input type="radio" name="dice-appearance" checked={state.dice.appearance === appearance} onChange={() => updateState(current => ({ ...current, dice: { ...current.dice, appearance } }))} /><span>{label}</span></label>)}</div><p>Цвет не меняет значение.</p></fieldset></div></div>
        </section>
        <StoryCard title={state.role === 'player' ? 'Начало истории' : 'Текущая сцена'} note="ВЫМЫШЛЕННОЕ ОПИСАНИЕ · ДЕМО" text={selected.scene} question={state.role === 'player' ? 'Что вы предпримете?' : 'Опишите, что происходит дальше.'} />
        {state.entries.filter(entry => state.role === 'gm' || entry.kind !== 'command').map((entry, index) => <EntryItem key={index} label={`${entry.kind === 'action' ? 'ДЕЙСТВИЕ ИГРОКА' : entry.kind === 'narration' ? 'ОПИСАНИЕ ВЕДУЩЕГО' : 'КОМАНДА ВЕДУЩЕГО'} · ЛОКАЛЬНО`} text={entry.text} />)}
        {state.role === 'player' ? <ActionForm id="action" label="ЧТО ВЫ ДЕЛАЕТЕ?" value={state.action} onChange={value => updateState(current => ({ ...current, action: value }))} onSubmit={() => submitText('action')} placeholder="Опишите действие вашего героя..." rows={3} submitLabel="Добавить демонстрационное действие" footerLeft="Только в памяти этой страницы; ответа ведущего нет" footerRight="ДЕМО · БЕЗ ИИ" /> : <><ActionForm id="narration" label="ВАШЕ ОПИСАНИЕ СЦЕНЫ" value={state.narration} onChange={value => updateState(current => ({ ...current, narration: value }))} onSubmit={() => submitText('narration')} placeholder="Опишите происходящее своими словами..." rows={4} submitLabel="Добавить описание сцены" footerLeft="Текст виден только в этом браузере" footerRight="ДЕМО · БЕЗ ИИ" /><CommandPanel title="Демо-команды" hint="Добавляют запись в ленту, но не выполняют игровые правила." commands={([['check', 'Запросить проверку'], ['pause', 'Объявить паузу'], ['scene', 'Предложить смену сцены']] as [Command, string][]).map(([command, label]) => ({ key: command, label, onClick: () => addEntry(demoSource.submitCommand(command)) }))} /></>}
      </section><aside className="party-column"><div className="aside-heading"><span>{state.role === 'player' ? 'ВАШ ГЕРОЙ' : 'ПЕРСОНАЖ ИГРОКА'}</span><span>✧</span></div><CharacterCard portrait={avatar ? { name: avatar.name, image: avatar.image } : null} artNote="ПЕРСОНАЖ · ДЕМО" kicker={avatar?.name ?? 'ПОРТРЕТ'} name={state.character.name.trim() || 'Без имени'} concept={state.character.concept.trim() || 'Образ не указан'} stats={model.stats.map(stat => ({ key: stat.id, short: stat.short, value: state.character.stats[stat.id] }))} /><PartyPanel heading="ОТРЯД · ДЕМО" count="3 ГЕРОЯ" members={[{ key: 'hero', name: state.character.name.trim() || 'Ваш герой', note: state.character.concept.trim() || 'Персонаж игрока', portrait: <span className="portrait">✧</span> }, { key: 'torin', name: 'Торин', note: 'Условный спутник', portrait: <span className="portrait portrait-two">Т</span> }, { key: 'liara', name: 'Лиара', note: 'Условная спутница', portrait: <span className="portrait portrait-three">Л</span> }]} note="Спутники — вымышленные примеры, не подключённые игроки." /></aside></div>}
    </main>}
  </div>
}

export default DemoApp
