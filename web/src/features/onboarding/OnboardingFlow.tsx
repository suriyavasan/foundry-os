import { useReducer, useState } from 'react'
import { useNavigate } from 'react-router'
import { seedCompanyIdea } from '@/api'
import { useToast } from '@/components/ui'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { passkeyEnvironment } from '@/lib/webauthn'
import { flowReducer, initialFlow, canGoBack, type FlowMode } from '@/lib/onboarding'
import { setLastCompanyId } from '@/lib/storage'
import { StepFrame } from './StepFrame'
import { AccountStep } from './steps/AccountStep'
import { PasskeyStep } from './steps/PasskeyStep'
import { AiStep } from './steps/AiSteps'
import { CompanyStep } from './steps/CompanyStep'
import { TeamStep } from './steps/TeamStep'
import { WelcomeStep } from './steps/WelcomeStep'

/**
 * The onboarding: five full-screen steps for a first run (`setup`), or the one AI step as a gate for a company that is not
 * AI-ready (`gate`). The step order and what is allowed is lib/onboarding (tested on its own); this component only renders
 * the current step and performs the one side effect that has to wait for a working AI: posting the one-line idea as the
 * first Founder's note (the server refuses notes until the AI is ready).
 */
export function OnboardingFlow({
  mode,
  companyId: gateCompanyId = null,
  setupTokenRequired = false,
  onDone,
}: {
  mode: FlowMode
  /** gate mode: the company whose AI is not ready. */
  companyId?: string | null
  setupTokenRequired?: boolean
  onDone: () => void
}) {
  const [flow, dispatch] = useReducer(flowReducer, undefined, () => initialFlow(mode, { companyId: gateCompanyId }))
  // After the account exists, an optional "add a passkey" screen can sit between the account and the company. It is only
  // offered when the server speaks passkeys and this browser and address can use them, and it never blocks the flow.
  const [passkeyOffer, setPasskeyOffer] = useState(false)
  const navigate = useNavigate()
  const toast = useToast()
  const companyId = flow.companyId
  // A person who just made the company is its owner; for an existing company the role decides what they may change.
  const role = useCompanyRole(companyId ?? '')
  const canEdit = mode === 'setup' || role === undefined ? true : canEditPolicy(role)
  const back = canGoBack(flow) ? () => dispatch({ type: 'back' }) : undefined

  function launch() {
    if (companyId) {
      setLastCompanyId(companyId)
      navigate(`/c/${companyId}`, { replace: true, state: { focus: 'command-center' } })
    }
    dispatch({ type: 'finish' })
    onDone()
  }

  async function afterAi() {
    if (mode === 'setup' && companyId) {
      const where = await seedCompanyIdea(companyId, flow.idea)
      if (where === 'failed') toast.error('The company was created, but your idea could not be saved. Add it from the Command Center.')
      dispatch({ type: 'next' })
      return
    }
    onDone()
  }

  switch (flow.step) {
    case 'welcome':
      return (
        <StepFrame flow={flow} step="welcome" fx="both">
          <WelcomeStep onStart={() => dispatch({ type: 'next' })} />
        </StepFrame>
      )
    case 'account':
      if (passkeyOffer) {
        return (
          <StepFrame flow={flow} step="account" fx="spotlight">
            <PasskeyStep onDone={() => dispatch({ type: 'account_created' })} />
          </StepFrame>
        )
      }
      return (
        <StepFrame flow={flow} step="account" onBack={back} fx="spotlight">
          <AccountStep
            tokenRequired={setupTokenRequired}
            onDone={(session) => {
              if (passkeyEnvironment(session) === 'ready') setPasskeyOffer(true)
              else dispatch({ type: 'account_created' })
            }}
          />
        </StepFrame>
      )
    case 'company':
      return (
        <StepFrame flow={flow} step="company" fx="spotlight">
          <CompanyStep onDone={(id, idea) => dispatch({ type: 'company_created', companyId: id, idea })} />
        </StepFrame>
      )
    case 'ai':
      return (
        <StepFrame flow={flow} step="ai" wide fx="spotlight">
          {companyId && <AiStep companyId={companyId} canEdit={canEdit} finishLabel={mode === 'setup' ? 'Save and continue' : 'Save and open my company'} onDone={afterAi} />}
        </StepFrame>
      )
    case 'team':
      return (
        <StepFrame flow={flow} step="team" onBack={back} fx="beams">
          {companyId && <TeamStep companyId={companyId} companyIdea={flow.idea} onLaunch={launch} />}
        </StepFrame>
      )
  }
}
