import { errorMessage, isNotDeployed, useAiSetup, useModelProviders } from '@/api'
import { Button, Field, Select } from '@/components/ui'
import { savedWorkModel, workModelKey, workModelOptions, type WorkModelSelection } from '@/lib/aiSetup'

/**
 * The optional work model (M12): the model for heavy work runs. Planning, standups and reviews keep the main model; work tasks
 * use this one; with a coding CLI in hybrid mode the work goes to the CLI instead. Only a model whose provider test passed can
 * be chosen (the server checks the same thing), and it can always be cleared again.
 *
 * Controlled: `value` is the pending selection (`undefined` = untouched, `null` = cleared, a selection = chosen); the parent
 * compares it with what is saved and sends it with the one Save of the AI setup.
 */
export function WorkModelSection({
  companyId,
  canEdit,
  value,
  onChange,
}: {
  companyId: string
  canEdit: boolean
  value: WorkModelSelection | null | undefined
  onChange: (sel: WorkModelSelection | null) => void
}) {
  const ai = useAiSetup(companyId)
  const providers = useModelProviders(companyId)
  const saved = savedWorkModel(ai.data)
  const current = value === undefined ? saved : value
  const options = workModelOptions(providers.data, saved, ai.data?.work_model?.provider_name)
  const hybrid = ai.data?.work_mode === 'hybrid'

  if (providers.isPending && !providers.isError) return <p role="status" className="text-[13px] text-fg-3">Looking for tested models…</p>
  if (providers.isError && !isNotDeployed(providers.error)) {
    return (
      <p role="alert" className="text-[13px] text-danger">
        Could not read your model providers: {errorMessage(providers.error)}{' '}
        <button type="button" className="underline" onClick={() => void providers.refetch()}>
          Retry
        </button>
      </p>
    )
  }
  if (providers.isError) {
    return (
      <p role="status" className="rounded-md border border-line bg-raised p-3 text-[13px] text-fg-2">
        This server does not list model providers yet, so a separate work model cannot be chosen. Work runs use the main model.
      </p>
    )
  }

  return (
    <section aria-label="Work model" className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field
          className="min-w-64 flex-1"
          label="Model for heavy work"
          hint={
            options.length === 0
              ? 'No tested model to choose from yet. Connect another provider (or model) above and run its test; it appears here.'
              : 'Only models whose provider test passed are listed. To use another model, connect and test it in the Model API section first.'
          }
        >
          {({ id, describedBy }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              value={workModelKey(current)}
              disabled={!canEdit}
              onChange={(e) => onChange(options.find((o) => o.key === e.target.value) ?? null)}
            >
              <option value="">Same as the main model</option>
              {options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {canEdit && current && (
          <Button variant="ghost" onClick={() => onChange(null)}>
            Clear work model
          </Button>
        )}
      </div>
      {hybrid && current && (
        <p role="status" className="text-[12px] text-fg-3">
          Hybrid mode is on: work tasks of the role families allowed to use the coding CLI go to the CLI. This model still serves the work that stays on the model API.
        </p>
      )}
      {!canEdit && <p className="text-[12px] text-fg-3">Only an owner or admin can change the work model.</p>}
    </section>
  )
}
