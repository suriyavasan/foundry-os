import { Ban, GitBranchPlus, GitFork } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { isNotDeployed, useRepoAvailability } from '@/api'
import type { ProjectRepository } from '@/api/types'
import { NotAvailable } from '@/components/ui'
import { RepoConnectForm } from '@/features/repos/RepoConnectForm'
import { slugRepoName, type RepoChoice } from '@/lib/repos'
import { StepHeading } from '../StepFrame'

function ChoiceCard({ icon, title, children, disabled, onClick }: { icon: ReactNode; title: string; children: ReactNode; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex flex-col items-start gap-2 rounded-lg border border-line-strong bg-surface p-4 text-left transition-colors hover:border-accent hover:bg-hover focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line-strong disabled:hover:bg-surface"
    >
      <span className="flex size-8 items-center justify-center rounded-md bg-accent-subtle text-accent-fg" aria-hidden>
        {icon}
      </span>
      <span className="text-[14px] font-semibold text-fg">{title}</span>
      <span className="text-[12.5px] leading-relaxed text-fg-3">{children}</span>
    </button>
  )
}

/**
 * Step 5: where the code lives. Three choices and no pre-selected one: a new GitHub repository, an existing one, or no GitHub
 * (the project keeps a local repository, as before). "No GitHub" needs no network and always works, so a machine without git
 * or an older backend can still finish setting up.
 *
 * `onDone(repo)` is called with the binding (it may still be cloning), or with null for no GitHub; the flow then posts the
 * idea as the first note, so the Co-founder's triage sees the project with its repository.
 */
export function RepoStep({ companyId, companyName, idea, onDone }: { companyId: string; companyName: string; idea: string; onDone: (repo: ProjectRepository | null) => void }) {
  const [choice, setChoice] = useState<RepoChoice | null>(null)
  const availability = useRepoAvailability(companyId)
  const notBuilt = isNotDeployed(availability.error)
  const unavailable = availability.data?.available === false
  const blocked = notBuilt || unavailable
  const seedName = slugRepoName(companyName)

  if (choice) {
    return (
      <div>
        <StepHeading eyebrow="Your code" title={choice === 'new' ? 'Create a GitHub repository' : 'Connect a GitHub repository'}>
          {choice === 'new'
            ? 'Foundry creates the repository, makes the first commit, and from then on opens pull requests as your agents finish work.'
            : 'Foundry clones it, works on its own foundry/ branch and opens pull requests. Before anything else, your team reads the codebase and writes an architecture note.'}
        </StepHeading>
        <RepoConnectForm
          companyId={companyId}
          choice={choice}
          seed={{ repo: choice === 'new' ? seedName : '', description: choice === 'new' ? idea.slice(0, 350) : '' }}
          doneLabel="Continue"
          onDone={(repo) => onDone(repo)}
          onCancel={() => setChoice(null)}
          cancelLabel="Choose differently"
        />
      </div>
    )
  }

  return (
    <div>
      <StepHeading eyebrow="Your code" title="Where should your code live?">
        Your team writes code and documents. Put it in a GitHub repository so you can follow every change, or keep it on this machine for now. You can connect GitHub later from any project.
      </StepHeading>
      {availability.isPending ? (
        <p role="status" className="mb-3 text-[12.5px] text-fg-3">
          Checking that git works on this machine…
        </p>
      ) : notBuilt ? (
        <NotAvailable milestone="M14" compact className="mb-3" description="GitHub projects are not built into this server. Your project keeps a local repository." />
      ) : unavailable ? (
        <NotAvailable milestone="M14" compact className="mb-3" description={availability.data?.reason ?? 'Git is not available on this machine, so repositories cannot be used here.'} />
      ) : availability.isError ? (
        <p role="alert" className="mb-3 text-[12.5px] text-danger">
          Could not check whether GitHub projects work here. You can still continue without GitHub.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-3">
        <ChoiceCard icon={<GitBranchPlus className="size-4" />} title="Create a new repository" disabled={blocked} onClick={() => setChoice('new')}>
          Foundry makes an empty GitHub repository for your company and merges its own work after review and green CI.
        </ChoiceCard>
        <ChoiceCard icon={<GitFork className="size-4" />} title="Connect an existing repository" disabled={blocked} onClick={() => setChoice('existing')}>
          Work in a repository people already use. Foundry opens pull requests and a person merges them.
        </ChoiceCard>
        <ChoiceCard icon={<Ban className="size-4" />} title="No GitHub for now" onClick={() => onDone(null)}>
          Keep the code on this machine. Nothing is sent to GitHub.
        </ChoiceCard>
      </div>
    </div>
  )
}
