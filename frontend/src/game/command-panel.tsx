// Command panel extracted from the Codex demo (DemoApp.tsx, GM view).
export interface PanelCommand {
  key: string
  label: string
  onClick: () => void
}

export function CommandPanel({ title, hint, commands, id = 'commands-title' }: { title: string; hint: string; commands: PanelCommand[]; id?: string }) {
  return (
    <section className="command-panel" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      <p>{hint}</p>
      <div>{commands.map(command => <button key={command.key} className="secondary-button" onClick={command.onClick}>{command.label}</button>)}</div>
    </section>
  )
}