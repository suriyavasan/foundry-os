import { Lock } from 'lucide-react'
import { useAiSetup, useLogout } from '@/api'
import { Button } from '@/components/ui'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { Wordmark } from '@/features/auth/AuthShell'
import { blockingReasonText } from '@/lib/aiSetup'
import { OnboardingFlow } from './OnboardingFlow'

/**
 * Shown instead of the canvas while the company's AI is not ready (spec §6): the AI step, full screen. The platform
 * is unusable without a working API, so there is no way past this screen except finishing it. A person who cannot change
 * the setup (not an owner or admin) is told who can.
 */
export function AiGate({ companyId, onDone }: { companyId: string; onDone: () => void }) {
  const role = useCompanyRole(companyId)
  const ai = useAiSetup(companyId)
  const logout = useLogout()
  if (role && !canEditPolicy(role)) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas px-5">
        <div className="w-full max-w-md">
          <div className="mb-6">
            <Wordmark />
          </div>
          <div className="rounded-lg border border-line bg-surface p-6">
            <Lock className="mb-3 size-5 text-fg-3" aria-hidden />
            <h1 className="text-[20px] font-semibold text-fg">This company is waiting for its AI to be connected</h1>
            <p className="mt-2 text-[13.5px] text-fg-2">{blockingReasonText(ai.data?.blocking_reason)}</p>
            <p className="mt-2 text-[13.5px] text-fg-2">An owner or admin has to add an AI (a plan sign-in or an API key) and test it before anyone can use this company.</p>
            <div className="mt-5 flex gap-2">
              <Button onClick={() => void ai.refetch()} loading={ai.isFetching}>
                Check again
              </Button>
              <Button variant="ghost" onClick={() => logout.mutate()}>
                Log out
              </Button>
            </div>
          </div>
        </div>
      </main>
    )
  }
  return <OnboardingFlow mode="gate" companyId={companyId} onDone={onDone} />
}
