import { ArrowRight } from 'lucide-react'
import { motion } from 'motion/react'
import { useAgents, useAiSetup, useRepositories } from '@/api'
import type { Agent } from '@/api/types'
import { Avatar } from '@/components/avatar'
import { MovingBorderButton } from '../fx/MovingBorderButton'
import { StepHeading } from '../StepFrame'
import { codeSummary } from '@/lib/repos'
import { usePrefersReducedMotion } from '@/lib/motion'
import { AI_COFOUNDER_LABEL, findCofounder } from '@/lib/terms'
import { inferRoleFamily } from '@/lib/roleFamily'

const WORKING_RING = { token: '--live-working', dashed: false, pulse: true, dimmed: false }

function Teammate({ agent, role, i }: { agent: Agent; role: string; i: number }) {
  const reduced = usePrefersReducedMotion()
  return (
    <motion.li
      className="flex items-center gap-3 rounded-lg border border-line-strong bg-surface p-3"
      initial={reduced ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: reduced ? 0 : 0.2 + i * 0.25 }}
    >
      <Avatar family={inferRoleFamily(agent)} size="lg" ring={reduced ? { ...WORKING_RING, pulse: false } : WORKING_RING} />
      <div className="min-w-0">
        <p className="truncate text-[14px] font-semibold text-fg">{agent.name}</p>
        <p className="truncate text-[12.5px] text-fg-3">{role}</p>
      </div>
    </motion.li>
  )
}

/** Step 6: the first two employees, a summary of the choices, and the launch button. */
export function TeamStep({ companyId, companyIdea, onLaunch }: { companyId: string; companyIdea: string; onLaunch: () => void }) {
  const agents = useAgents(companyId)
  const ai = useAiSetup(companyId)
  const repos = useRepositories(companyId)
  const repo = repos.data?.find((r) => r.is_company_default) ?? repos.data?.[0]
  const list = agents.data ?? []
  const cofounder = findCofounder(list)
  const hr = list.find((a) => a.status !== 'retired' && a.status !== 'rejected' && a.kind !== 'founder' && inferRoleFamily(a) === 'hr')
  return (
    <div>
      <StepHeading eyebrow="Meet your team" title="Meet your team">
        Two people are already at their desks. Tell the AI Co-founder what you want; it will hire the rest, with HR.
      </StepHeading>
      <ul aria-label="Your first employees" className="grid gap-3 sm:grid-cols-2">
        {cofounder ? <Teammate agent={cofounder} role={AI_COFOUNDER_LABEL} i={0} /> : null}
        {hr ? <Teammate agent={hr} role="HR" i={1} /> : null}
      </ul>
      {agents.isSuccess && !cofounder && <p className="mt-2 text-[12.5px] text-fg-3">Your AI Co-founder is being set up and will appear on the canvas.</p>}

      <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-5 gap-y-2 rounded-lg border border-line bg-surface p-4 text-[13px]">
        {companyIdea && (
          <>
            <dt className="text-fg-3">Your idea</dt>
            <dd className="min-w-0 break-words text-fg">{companyIdea}</dd>
          </>
        )}
        {ai.data && ai.data.stack_count > 0 && (
          <>
            <dt className="text-fg-3">AI</dt>
            <dd className="text-fg">{ai.data.stack_count === 1 ? '1 AI connected' : `${ai.data.stack_count} AIs connected`}</dd>
          </>
        )}
        {repo && (
          <>
            <dt className="text-fg-3">Code</dt>
            <dd className="min-w-0 font-mono text-[12.5px] break-words text-fg">{codeSummary(repo)}</dd>
          </>
        )}
      </dl>

      <div className="mt-8 flex justify-center">
        <MovingBorderButton autoFocus onClick={onLaunch}>
          Launch
          <ArrowRight className="size-4" aria-hidden />
        </MovingBorderButton>
      </div>
    </div>
  )
}
