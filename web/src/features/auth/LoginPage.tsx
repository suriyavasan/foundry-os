import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { AuthShell } from './AuthShell'
import { LoginForm } from './LoginForm'

/** The reset command named on the login page. It belongs to the CLI (M9), which does not exist yet. */
export const RESET_PASSWORD_COMMAND = 'foundryos auth reset-password'

export function LoginPage() {
  const [forgot, setForgot] = useState(false)
  return (
    <AuthShell>
      <h1 className="text-[20px] leading-7 font-semibold text-fg">Sign in</h1>
      <p className="mt-1 mb-5 text-[13px] text-fg-3">Welcome back. Your company kept working while you were away.</p>
      {/* The session cookie is set by the server; the auth gate swaps to the app as soon as the session query updates. */}
      <LoginForm onSuccess={() => undefined} passkeys />

      <div className="mt-5 border-t border-line pt-4">
        <button
          type="button"
          onClick={() => setForgot((f) => !f)}
          aria-expanded={forgot}
          aria-controls="forgot-password"
          className="inline-flex items-center gap-1 text-[12.5px] text-fg-2 hover:text-fg"
        >
          Forgot your password?
          <ChevronDown className={'size-3.5 transition-transform ' + (forgot ? 'rotate-180' : '')} aria-hidden />
        </button>
        {forgot && (
          <div id="forgot-password" className="mt-2 space-y-2 text-[12.5px] text-fg-2">
            <p>A password is reset from the computer that runs Foundry AI OS, with the command line:</p>
            <pre className="overflow-x-auto rounded-md border border-line bg-raised px-3 py-2 font-mono text-[12px] text-fg">{RESET_PASSWORD_COMMAND} &lt;username&gt;</pre>
            <p className="text-fg-3">That command ships with the CLI in M9 and does not exist yet. Until then, a password can only be reset by whoever administers this install.</p>
          </div>
        )}
      </div>
    </AuthShell>
  )
}
