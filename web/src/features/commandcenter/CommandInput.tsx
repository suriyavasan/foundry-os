import clsx from 'clsx'
import { CornerDownLeft } from 'lucide-react'
import { useEffect, useId, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { Button, Tooltip } from '@/components/ui'
import { isMac } from '@/features/canvas/TopBar'
import {
  HISTORY_START,
  applySuggestion,
  enterAcceptsSuggestion,
  enterSends,
  historyKeyApplies,
  matchCommands,
  parseInput,
  pushHistory,
  stepHistory,
  usageOf,
  type HistoryCursor,
} from '@/lib/commandCenter'
import { isSubmitShortcut } from '@/lib/notes'
import { readCommandHistory, writeCommandHistory } from '@/lib/storage'
import { AI_COFOUNDER_LABEL } from '@/lib/terms'

/** DOM id of the widget's input: the command palette and the launch screen focus it. */
export const COMMAND_INPUT_ID = 'command-center-input'
export const MAX_INPUT_CHARS = 10_240

/** Lets a parent put text in the box (the example chips of an empty feed). */
export interface CommandInputHandle {
  fill: (text: string) => void
}

/**
 * The Command Center input. `/` opens the command list (↑/↓ to move, Tab or Enter to accept); ↑/↓ browse what this browser typed
 * before; ⌘/Ctrl+Enter always sends; Enter sends a one-line command and adds a line to free text. Whatever is sent appears at
 * once in the feed above, so every input visibly gets an output.
 */
export function CommandInput({
  companyId,
  onSubmit,
  id = COMMAND_INPUT_ID,
  autoFocus,
  compact = false,
  handle,
}: {
  companyId: string
  onSubmit: (input: string) => void
  id?: string
  autoFocus?: boolean
  compact?: boolean
  handle?: Ref<CommandInputHandle>
}) {
  const [value, setValue] = useState('')
  const [history, setHistory] = useState<string[]>(() => readCommandHistory(companyId))
  const [cursor, setCursor] = useState<HistoryCursor>(HISTORY_START)
  const [active, setActive] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)
  const listId = useId()
  const hintId = useId()
  const mod = isMac() ? '⌘' : 'Ctrl'

  const suggestions = dismissed ? [] : matchCommands(value)
  const open = suggestions.length > 0
  const idx = Math.min(active, Math.max(0, suggestions.length - 1))

  // Grow with the content up to a few lines.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`
  }, [value])

  useImperativeHandle(handle, () => ({
    fill: (text: string) => {
      setValue(text)
      setActive(0)
      setDismissed(true)
      ref.current?.focus()
    },
  }))

  function setText(v: string) {
    setValue(v)
    setActive(0)
    setDismissed(false)
    if (error) setError(null)
  }

  function send() {
    const parsed = parseInput(value)
    if (parsed.kind === 'empty') {
      setError('Type a command (try /help) or a message for the AI Co-founder.')
      return
    }
    if (value.length > MAX_INPUT_CHARS) {
      setError(`That is too long: at most ${MAX_INPUT_CHARS.toLocaleString()} characters. Split it in two.`)
      return
    }
    const text = value.trim()
    const next = pushHistory(history, text)
    setHistory(next)
    writeCommandHistory(companyId, next)
    setCursor(HISTORY_START)
    setValue('')
    setError(null)
    onSubmit(text)
    ref.current?.focus()
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.nativeEvent.isComposing) return
    if (isSubmitShortcut(e)) {
      e.preventDefault()
      send()
      return
    }
    if (open) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActive((idx + 1) % suggestions.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActive((idx - 1 + suggestions.length) % suggestions.length)
        return
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && enterAcceptsSuggestion(value, suggestions[idx]))) {
        e.preventDefault()
        accept(idx)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setDismissed(true)
        return
      }
    }
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey) {
      const dir = e.key === 'ArrowUp' ? 'up' : 'down'
      if (historyKeyApplies(dir, value, e.currentTarget.selectionStart ?? value.length)) {
        const r = stepHistory(history, cursor, dir, value)
        if (r.value !== value || r.cursor !== cursor) {
          e.preventDefault()
          setCursor(r.cursor)
          setValue(r.value)
          setDismissed(true)
        }
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && enterSends(value)) {
      e.preventDefault()
      send()
    }
  }

  function accept(i: number) {
    const def = suggestions[i]
    if (!def) return
    setValue(applySuggestion(def))
    setActive(0)
    setDismissed(true)
    requestAnimationFrame(() => ref.current?.focus())
  }

  return (
    <form
      className="relative"
      onSubmit={(e) => {
        e.preventDefault()
        send()
      }}
    >
      <label htmlFor={id} className="sr-only">
        Command or message for the {AI_COFOUNDER_LABEL}
      </label>
      <div className={clsx('flex items-start gap-2 rounded-md border border-line-strong bg-canvas px-2.5 py-2 transition-colors focus-within:border-accent focus-within:ring-1 focus-within:ring-accent', error && 'border-danger')}>
        <span aria-hidden className="mt-px font-mono text-[13px] leading-5 font-semibold text-accent-fg select-none">
          ❯
        </span>
        <textarea
          ref={ref}
          id={id}
          autoFocus={autoFocus}
          rows={1}
          value={value}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={open ? `${listId}-${idx}` : undefined}
          aria-describedby={hintId}
          aria-keyshortcuts="Meta+Enter Control+Enter"
          aria-invalid={!!error || undefined}
          spellCheck={false}
          placeholder={compact ? `Type / for commands, or tell the ${AI_COFOUNDER_LABEL} anything` : `Type / for commands, or write anything for the ${AI_COFOUNDER_LABEL}`}
          className="block max-h-[132px] min-h-5 w-full resize-none bg-transparent font-mono text-[12.5px] leading-5 text-fg outline-none placeholder:font-sans placeholder:text-fg-3"
        />
        <Tooltip content={`Send (${mod} + Enter)`}>
          <Button type="submit" size="sm" variant="primary" className="-my-0.5 shrink-0" icon={<CornerDownLeft className="size-3.5" />} aria-keyshortcuts="Meta+Enter Control+Enter">
            Send
          </Button>
        </Tooltip>
      </div>

      {open && (
        <ul id={listId} role="listbox" aria-label="Commands" className="absolute right-0 left-0 z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-line-strong bg-surface p-1 shadow-pop">
          {suggestions.map((s, i) => (
            <li
              key={s.name}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === idx}
              onMouseDown={(e) => {
                e.preventDefault()
                accept(i)
              }}
              onMouseMove={() => setActive(i)}
              className={clsx('cursor-pointer rounded-sm px-2 py-1.5', i === idx ? 'bg-hover' : '')}
            >
              <span className="font-mono text-[12.5px] text-fg">{usageOf(s)}</span>
              <span className="block text-[12px] text-fg-3">
                {s.description}
                {s.changes ? ' · changes things' : ''}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p id={hintId} className="mt-1.5 text-[11.5px] text-fg-3">
        {error ? (
          <span role="alert" className="text-danger">
            {error}
          </span>
        ) : (
          <>
            <kbd className="rounded-sm border border-line-strong px-1">↵</kbd> runs a command · <kbd className="rounded-sm border border-line-strong px-1">{mod} ↵</kbd> sends · <kbd className="rounded-sm border border-line-strong px-1">↑</kbd>
            <kbd className="rounded-sm border border-line-strong px-1">↓</kbd> history
          </>
        )}
      </p>
    </form>
  )
}
