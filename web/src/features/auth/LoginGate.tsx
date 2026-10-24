import { KeyRound, RefreshCw, Terminal } from 'lucide-react'
import { Button } from '@/components/ui'

export const BACKEND_START_COMMAND = 'cd backend && uv run uvicorn app.main:app --port 8700'

/**
 * Shown when `/api/me` answers 401: the browser has no owner session. There is nothing to type here on
 * purpose; the backend prints a private sign-in link to its terminal and opening it sets the session cookie.
 */
export function LoginGate({ onRetry, retrying }: { onRetry: () => void; retrying?: boolean }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex size-8 items-center justify-center rounded-md bg-accent-subtle text-accent-fg">
            <KeyRound className="size-4" aria-hidden />
          </span>
          <span className="text-[13px] font-semibold tracking-wide text-fg-2">Foundry AI OS</span>
        </div>

        <div className="rounded-md border border-line bg-surface p-6">
          <h1 className="text-[20px] leading-7 font-semibold text-fg">Open Foundry AI OS from the login link printed in your terminal</h1>
          <p className="mt-2 text-[13.5px] text-fg-2">
            This browser is not signed in. Foundry AI OS has no password: when the backend starts it prints a private, one-time sign-in link to its terminal, and opening that link signs this browser in.
          </p>

          <ol className="mt-5 space-y-4 text-[13px] text-fg-2">
            <li className="flex gap-3">
              <Step n={1} />
              <div className="min-w-0 flex-1">
                <p className="text-fg">Start the backend, or find the terminal where it is already running.</p>
                <pre className="mt-1.5 overflow-x-auto rounded-md border border-line bg-raised px-3 py-2 font-mono text-[12px] text-fg">
                  <Terminal className="mr-1.5 inline size-3 text-fg-3" aria-hidden />
                  {BACKEND_START_COMMAND}
                </pre>
              </div>
            </li>
            <li className="flex gap-3">
              <Step n={2} />
              <p className="text-fg">Copy the login link it prints at startup and open it in this browser. It sends you back here, signed in.</p>
            </li>
            <li className="flex gap-3">
              <Step n={3} />
              <p>
                Later, <code className="rounded-sm bg-raised px-1 py-0.5 font-mono text-[12px] text-fg">foundryos up</code> starts everything and prints the link for you.
              </p>
            </li>
          </ol>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Button variant="primary" icon={<RefreshCw className="size-3.5" />} loading={retrying} onClick={onRetry}>
              I opened the link, try again
            </Button>
            <p className="text-[12px] text-fg-3">Use the same browser and the same address (localhost and 127.0.0.1 keep separate sign-ins).</p>
          </div>
        </div>
        <p className="mt-4 text-center text-[12px] text-fg-3">The link is a secret. Do not share it or paste it into chats or tickets.</p>
      </div>
    </main>
  )
}

function Step({ n }: { n: number }) {
  return (
    <span aria-hidden className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-neutral-subtle text-[11.5px] font-semibold text-fg-2">
      {n}
    </span>
  )
}
