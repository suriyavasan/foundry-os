import { CheckCircle2, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useAiConnectionLogin, useStartAiConnectionLogin } from '@/api'
import { Button } from '@/components/ui'
import { terminalLoginCommand, type AiConnection } from '@/lib/aiStack'

/**
 * Sign-in for a plan connection (B9 rules). Only the CLI's own flow signs in: the button starts it, the browser window
 * (or the link) is where the account is used, and the terminal command is the alternative. This component has no field for
 * a password, a code or a token and never will.
 */
export function PlanSignIn({ companyId, conn, canEdit }: { companyId: string; conn: AiConnection; canEdit: boolean }) {
  const login = useAiConnectionLogin(companyId, conn.id, true)
  const start = useStartAiConnectionLogin(companyId)
  const [error, setError] = useState<string | null>(null)
  const l = login.data?.login
  const running = l?.state === 'running'
  const failed = l?.state === 'failed'
  const signedIn = login.data?.signed_in ?? conn.status.signed_in === true
  const isClaude = conn.cli_id === 'claude_code'

  async function go(method?: 'claudeai' | 'console') {
    setError(null)
    try {
      await start.mutateAsync({ id: conn.id, method })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  if (signedIn && !running) {
    return (
      <p className="flex items-center gap-2 text-[12.5px] text-fg">
        <CheckCircle2 className="size-4 text-success" aria-hidden />
        <span className="font-medium">Signed in</span>
        {(login.data?.email ?? conn.status.account) && <span className="text-fg-2">{login.data?.email ?? conn.status.account}</span>}
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {running ? (
        <div role="status" className="space-y-1.5 rounded-md border border-line bg-raised p-3 text-[12.5px] text-fg-2">
          <p className="font-medium text-fg">Finish signing in in the browser window that opened.</p>
          {l?.url && (
            <p>
              <a href={l.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-fg hover:underline">
                Open the sign-in page <ExternalLink className="size-3" aria-hidden />
              </a>
            </p>
          )}
          <p className="text-fg-3">Waiting for you to finish. This page updates by itself.</p>
        </div>
      ) : (
        <>
          {failed && (
            <p role="alert" className="text-[12.5px] text-danger">
              Sign-in did not finish{l?.message ? `: ${l.message}` : '.'} Try again.
            </p>
          )}
          {error && (
            <p role="alert" className="text-[12.5px] text-danger">
              {error}
            </p>
          )}
          {canEdit ? (
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" size="sm" loading={start.isPending} onClick={() => void go(isClaude ? 'claudeai' : undefined)}>
                {failed ? 'Try signing in again' : 'Sign in'}
              </Button>
              {isClaude && (
                <button type="button" className="text-[12.5px] text-accent-fg hover:underline" disabled={start.isPending} onClick={() => void go('console')}>
                  Use a Claude Console account (API billing) instead
                </button>
              )}
            </div>
          ) : (
            <p className="text-[12px] text-fg-3">Only an owner or admin can sign in.</p>
          )}
        </>
      )}
      <p className="text-[12px] text-fg-3">
        On a server or over SSH, run <code className="font-mono text-fg-2">{terminalLoginCommand(conn.label)}</code> there instead.
      </p>
    </div>
  )
}
