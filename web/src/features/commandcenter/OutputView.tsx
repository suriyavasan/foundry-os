import clsx from 'clsx'
import { AlertTriangle, ArrowUpRight } from 'lucide-react'
import type { CommandOutput } from '@/api/types'
import { Markdown } from '@/components/ui'
import { normalizeCommandOutput } from '@/lib/commandCenter'
import type { Panel } from '@/lib/panels'

/**
 * A structured output: text (markdown, rendered safely), a table, a list, or an error; links beneath it open panels.
 * Nothing here executes or injects anything from the server: cells are React text, links are parsed into known panels.
 */
export function OutputView({ output, onOpenPanel, className }: { output: CommandOutput; onOpenPanel: (p: Panel) => void; className?: string }) {
  const o = normalizeCommandOutput(output)
  if (!o) return null
  const isError = o.kind === 'error'
  return (
    <div className={clsx('space-y-2', isError && 'rounded-md border border-danger/40 bg-danger-subtle px-3 py-2', className)} data-kind={o.kind}>
      {o.title && (
        <p className={clsx('flex items-center gap-1.5 text-[12.5px] font-semibold', isError ? 'text-danger' : 'text-fg')}>
          {isError && <AlertTriangle className="size-3.5 shrink-0" aria-hidden />}
          {o.title}
        </p>
      )}
      {o.text && (isError ? <p className="text-[13px] break-words text-fg">{o.text}</p> : <Markdown source={o.text} className="text-[13px] text-fg-2" />)}
      {o.kind === 'table' && o.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-[12.5px]">
            {o.columns.length > 0 && (
              <thead>
                <tr>
                  {o.columns.map((c, i) => (
                    <th key={i} scope="col" className="border-b border-line-strong px-2 py-1 font-semibold whitespace-nowrap text-fg">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {o.rows.map((r, i) => (
                <tr key={i} className="border-b border-line last:border-b-0">
                  {r.map((c, j) => (
                    <td key={j} className="px-2 py-1 align-top break-words text-fg-2">
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.kind === 'list' && o.rows.length > 0 && (
        <ul className="space-y-1 text-[13px] text-fg-2">
          {o.rows.map((r, i) => (
            <li key={i} className="flex gap-2 break-words">
              <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-fg-3" />
              <span className="min-w-0">
                <span className="text-fg">{r[0]}</span>
                {r.length > 1 && <span className="text-fg-3"> · {r.slice(1).join(' · ')}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {o.kind === 'table' && o.rows.length === 0 && !o.text && <p className="text-[12.5px] text-fg-3">Nothing to show.</p>}
      {o.kind === 'list' && o.rows.length === 0 && !o.text && <p className="text-[12.5px] text-fg-3">Nothing to show.</p>}
      {o.links.length > 0 && (
        <ul aria-label="Open" className="flex flex-wrap gap-1.5">
          {o.links.map((l) => (
            <li key={`${l.label}:${l.panel.kind}`}>
              <button type="button" onClick={() => onOpenPanel(l.panel)} className="inline-flex items-center gap-1 rounded-sm border border-line bg-raised px-1.5 py-0.5 text-[12px] text-accent-fg transition-colors hover:bg-hover">
                {l.label}
                <ArrowUpRight className="size-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
