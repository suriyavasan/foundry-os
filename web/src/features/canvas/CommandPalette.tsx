import * as RadixDialog from '@radix-ui/react-dialog'
import { Bot, ListChecks, PanelRight, Search, Zap, type LucideIcon } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useAllTasks, useCompanyControl, isNotDeployed } from '@/api'
import type { Company } from '@/api/types'
import { useTheme } from '@/components/layout/ThemeProvider'
import { drawerOverlay } from '@/components/ui'
import { buildCommands, filterCommands, type CommandGroup, type PaletteCommand } from '@/lib/commands'
import { useCanvas } from './CanvasContext'
import { useAgentMap } from './hooks'

const GROUP_ICON: Record<CommandGroup, LucideIcon> = { Actions: Zap, Panels: PanelRight, Agents: Bot, Tasks: ListChecks }

/** ⌘K / Ctrl+K: jump to any panel, agent or task; create work; pause or resume the company. */
export function CommandPalette({ company }: { company: Company }) {
  const { companyId, openPanel, create, paletteOpen, setPaletteOpen, setPauseOpen } = useCanvas()
  const { agents } = useAgentMap(companyId)
  const tasksQ = useAllTasks(companyId)
  const control = useCompanyControl(companyId)
  const { toggle: toggleTheme } = useTheme()
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const listId = useId()
  const listRef = useRef<HTMLUListElement>(null)

  // Global shortcut.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(!paletteOpen)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [paletteOpen, setPaletteOpen])

  const commands = useMemo(
    () => buildCommands({ agents, tasks: tasksQ.data ?? [], companyStatus: company.status, workEngine: !isNotDeployed(tasksQ.error) }),
    [agents, tasksQ.data, tasksQ.error, company.status],
  )
  const results = useMemo(() => filterCommands(commands, query), [commands, query])
  const active = Math.min(cursor, Math.max(0, results.length - 1))

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [active, results])

  function close() {
    setPaletteOpen(false)
    setQuery('')
    setCursor(0)
  }

  function run(cmd: PaletteCommand) {
    close()
    const a = cmd.action
    switch (a.type) {
      case 'panel':
        openPanel(a.panel)
        break
      case 'create':
        create(a.what)
        break
      case 'theme':
        toggleTheme()
        break
      case 'company':
        // Pausing always goes through the confirm dialog; resuming is safe to do directly.
        if (a.do === 'pause') setPauseOpen(true)
        else void control.mutateAsync('resume')
        break
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((active + 1) % Math.max(1, results.length))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((active - 1 + results.length) % Math.max(1, results.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const cmd = results[active]
      if (cmd) run(cmd)
    }
  }

  return (
    <RadixDialog.Root open={paletteOpen} onOpenChange={(o) => (o ? setPaletteOpen(true) : close())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={drawerOverlay} />
        <RadixDialog.Content
          aria-label="Command palette"
          className="fixed top-[14vh] left-1/2 z-50 flex max-h-[min(520px,70vh)] w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 flex-col overflow-hidden rounded-md bg-surface shadow-pop"
        >
          <RadixDialog.Title className="sr-only">Command palette</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">Type to search panels, agents and tasks. Use the arrow keys and Enter.</RadixDialog.Description>
          <div className="flex items-center gap-2 border-b border-line px-3">
            <Search className="size-4 shrink-0 text-fg-3" aria-hidden />
            <input
              autoFocus
              role="combobox"
              aria-expanded
              aria-controls={listId}
              aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
              aria-label="Search commands"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setCursor(0)
              }}
              onKeyDown={onKeyDown}
              placeholder="Jump to a panel, agent or task, or run an action…"
              className="h-11 flex-1 bg-transparent text-[14px] text-fg outline-none placeholder:text-fg-3"
            />
            <kbd className="rounded-sm border border-line-strong px-1 text-[11px] text-fg-3">Esc</kbd>
          </div>
          <ul ref={listRef} id={listId} role="listbox" aria-label="Results" className="min-h-0 flex-1 overflow-y-auto p-1">
            {results.length === 0 && <li role="presentation" className="px-3 py-6 text-center text-[13px] text-fg-3">No matches for &ldquo;{query}&rdquo;</li>}
            {results.map((cmd, i) => {
              const showGroup = i === 0 || results[i - 1]!.group !== cmd.group
              const Icon = GROUP_ICON[cmd.group]
              return (
                <li key={cmd.id} role="presentation">
                  {showGroup && <div className="px-2 pt-2 pb-1 text-[11px] font-medium tracking-wide text-fg-3 uppercase">{cmd.group}</div>}
                  <div
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === active}
                    onMouseMove={() => setCursor(i)}
                    onClick={() => run(cmd)}
                    className={'flex cursor-pointer items-center gap-2.5 rounded-sm px-2 py-1.5 text-[13px] ' + (i === active ? 'bg-hover text-fg' : 'text-fg-2')}
                  >
                    <Icon className="size-3.5 shrink-0 text-fg-3" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{cmd.label}</span>
                    {cmd.hint && <span className="shrink-0 text-[12px] text-fg-3">{cmd.hint}</span>}
                  </div>
                </li>
              )
            })}
          </ul>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}
