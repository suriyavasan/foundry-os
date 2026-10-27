import { useState } from 'react'
import { useProjects, useRepositories } from '@/api'
import type { ProjectRepository } from '@/api/types'
import { Dialog, Field, Select } from '@/components/ui'
import type { RepoChoice } from '@/lib/repos'
import { RepoConnectForm } from './RepoConnectForm'

const NEW_PROJECT = 'new'

function Body({ companyId, projectId, onDone }: { companyId: string; projectId: string | null; onDone: (repo: ProjectRepository) => void }) {
  const projects = useProjects(companyId)
  const repos = useRepositories(companyId)
  const [target, setTarget] = useState<string>(projectId ?? NEW_PROJECT)
  const [choice, setChoice] = useState<RepoChoice | null>(null)
  // A project can have one repository: offer only those without a live binding.
  const bound = new Set((repos.data ?? []).filter((r) => r.status !== 'disconnected').map((r) => r.project_id))
  const free = (projects.data ?? []).filter((p) => !bound.has(p.id) && p.status !== 'completed' && p.status !== 'cancelled')
  const fixed = projectId !== null
  const project = target === NEW_PROJECT ? null : (projects.data ?? []).find((p) => p.id === target)

  return (
    <div className="space-y-4">
      {!fixed && (
        <Field label="Project" hint="A project has one repository. Choose a project, or make a new one with the repository.">
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={target} disabled={choice !== null} onChange={(e) => setTarget(e.target.value)}>
              <option value={NEW_PROJECT}>A new project</option>
              {free.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      )}
      {choice === null ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <button type="button" onClick={() => setChoice('new')} className="rounded-lg border border-line-strong bg-surface p-4 text-left hover:border-accent hover:bg-hover">
            <span className="block text-[13.5px] font-semibold text-fg">Create a new repository</span>
            <span className="mt-1 block text-[12.5px] text-fg-3">Foundry makes an empty GitHub repository and pushes the first commit.</span>
          </button>
          <button type="button" onClick={() => setChoice('existing')} className="rounded-lg border border-line-strong bg-surface p-4 text-left hover:border-accent hover:bg-hover">
            <span className="block text-[13.5px] font-semibold text-fg">Connect an existing repository</span>
            <span className="mt-1 block text-[12.5px] text-fg-3">Foundry clones it, learns the codebase and opens pull requests.</span>
          </button>
        </div>
      ) : (
        <RepoConnectForm
          key={`${choice}:${target}`}
          companyId={companyId}
          choice={choice}
          projectId={project ? project.id : null}
          seed={{ projectName: '', repo: '', description: project?.description?.slice(0, 350) ?? '' }}
          doneLabel="Done"
          onDone={onDone}
          onCancel={() => setChoice(null)}
          cancelLabel="Choose differently"
        />
      )}
    </div>
  )
}

/** Connect a repository to a project (or to a new project) after onboarding: the same form as the onboarding step. */
export function ConnectRepoDialog({ companyId, open, onOpenChange, projectId = null, onConnected }: { companyId: string; open: boolean; onOpenChange: (open: boolean) => void; projectId?: string | null; onConnected?: (repo: ProjectRepository) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Connect a repository" description="Put a project’s code on GitHub." size="lg">
      {open && (
        <Body
          companyId={companyId}
          projectId={projectId}
          onDone={(repo) => {
            onOpenChange(false)
            onConnected?.(repo)
          }}
        />
      )}
    </Dialog>
  )
}
