import { Check, CircleHelp, X } from 'lucide-react'
import { permissionChecklist, permissionState, type PermissionState, type RepoChoice } from '@/lib/repos'

const STATE_TEXT: Record<PermissionState, string> = { ok: 'granted', missing: 'missing', unknown: 'not checked' }

/**
 * The fine-grained token permissions for the chosen path, one row each: what to select on GitHub, what it is used for and
 * what stops working without it. Given the token's `capabilities` (from the inspect or the binding) each row says granted /
 * missing.
 */
export function PermissionChecklist({ choice, capabilities }: { choice: RepoChoice; capabilities?: Record<string, unknown> | null }) {
  const rows = permissionChecklist(choice)
  return (
    <ul aria-label="Token permissions" className="divide-y divide-line rounded-md border border-line bg-surface text-[12.5px]">
      {rows.map((row) => {
        const state = permissionState(row, capabilities)
        return (
          <li key={row.permission} className="flex items-start gap-2.5 px-3 py-2">
            <span className="mt-0.5 shrink-0" aria-hidden>
              {state === 'ok' ? <Check className="size-3.5 text-success" /> : state === 'missing' ? <X className="size-3.5 text-danger" /> : <CircleHelp className="size-3.5 text-fg-3" />}
            </span>
            <div className="min-w-0">
              <p className="text-fg">
                <span className="font-medium">{row.permission}</span>: {row.access}
                {row.optional ? <span className="text-fg-3"> (optional)</span> : null}
                <span className="sr-only"> — {STATE_TEXT[state]}</span>
              </p>
              <p className="text-fg-3">
                {row.used}
                {state === 'missing' ? <span className="text-danger"> · Without it: {row.missing}</span> : null}
              </p>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
