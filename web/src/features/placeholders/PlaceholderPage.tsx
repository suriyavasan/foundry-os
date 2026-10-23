import { Page } from '@/components/layout/Page'
import { Card, NotAvailable } from '@/components/ui'

/** Honest placeholder for a nav area whose backend does not exist yet. No mock data. */
function PlaceholderPage({
  title,
  description,
  milestone,
  heading,
  what,
  points,
}: {
  title: string
  description: string
  milestone: string
  heading: string
  what: string
  points: string[]
}) {
  return (
    <Page title={title} description={description}>
      <Card>
        <NotAvailable milestone={milestone} title={heading} description={what} />
        <ul className="mx-auto mb-8 max-w-md list-disc space-y-1 pl-8 text-[13px] text-fg-2">
          {points.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </Card>
    </Page>
  )
}

export const ProjectsPage = () => (
  <PlaceholderPage
    title="Projects"
    description="Groups of related work towards a goal."
    milestone="M3"
    heading="Projects are not built yet"
    what="Projects will organise tasks under the company's strategic goal."
    points={['Project list with owner, status and progress', 'Linked tasks and dependencies', 'Plans proposed by the AI Founder for your approval']}
  />
)

export const TasksPage = () => (
  <PlaceholderPage
    title="Tasks"
    description="Units of work assigned to agents."
    milestone="M3"
    heading="Tasks are not built yet"
    what="Tasks, dependencies and execution runs arrive with the work engine. Runtime execution follows in M4."
    points={['Task board and list by status, assignee and project', 'Dependencies between tasks', 'Run history with timings and errors per attempt']}
  />
)

export const ArtifactsPage = () => (
  <PlaceholderPage
    title="Artifacts"
    description="Documents and files produced by agents."
    milestone="M7"
    heading="Artifacts are not built yet"
    what="Artifact storage with versioning and provenance ships in M7."
    points={['Browse deliverables by project, task and agent', 'Version history for each artifact', 'Who produced it, from which task run']}
  />
)

export const FinancePage = () => (
  <PlaceholderPage
    title="Finance & Usage"
    description="What the company spends, and on what."
    milestone="M6"
    heading="Finance is not built yet"
    what="The usage ledger and budget enforcement ship in M6. Until then, no spend is tracked and no figures are shown."
    points={[
      'Spend by agent, model and project',
      'Each cost labelled verified, estimated or unknown',
      'Budgets and remaining headroom (per-agent limits you set today are stored but not enforced yet)',
    ]}
  />
)

export const ApprovalsPage = () => (
  <PlaceholderPage
    title="Approvals"
    description="Decisions that need a human."
    milestone="M6"
    heading="Approvals are not built yet"
    what="The approval inbox arrives with governance in M6. Each approval will authorise one specific, scoped action."
    points={['Pending approvals with the exact action requested', 'Approve or deny with a recorded reason', 'History of past approvals']}
  />
)
