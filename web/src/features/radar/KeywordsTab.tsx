import { Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { ApiError, errorMessage } from '@/api'
import {
  useCreateRadarKeyword,
  useDeleteRadarKeyword,
  useDeriveRadarKeywords,
  useRadarKeywords,
  useUpdateRadarKeyword,
  type KeywordKind,
  type KeywordOrigin,
  type RadarKeyword,
} from '@/api/radar'
import { Badge, Boundary, Button, ConfirmDialog, Dialog, Field, FormError, Input, Select, Switch, useToast } from '@/components/ui'
import { KEYWORD_KIND, KEYWORD_ORIGIN_LABEL } from './radarLogic'
import { UntrustedText } from './Untrusted'

const ORIGINS: KeywordOrigin[] = ['owner', 'derived', 'agent']

export function KeywordsTab({ companyId, canAct }: { companyId: string; canAct: boolean }) {
  const q = useRadarKeywords(companyId)
  const derive = useDeriveRadarKeywords(companyId)
  const toast = useToast()
  const [editing, setEditing] = useState<RadarKeyword | 'new' | null>(null)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12.5px] text-fg-3">What the radar looks for. Items that match are kept and scored by the weight (1 to 5); an item that matches a negative keyword is dropped. At most 40 are on at once.</p>
        {canAct && (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              loading={derive.isPending}
              icon={<RefreshCw className="size-3.5" />}
              onClick={() => derive.mutate(undefined, { onSuccess: () => toast.success('Keywords derived again from your profile and goals'), onError: (err) => toast.error(errorMessage(err, 'Could not derive the keywords')) })}
            >
              Derive again
            </Button>
            <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setEditing('new')}>
              Add a keyword
            </Button>
          </div>
        )}
      </div>
      <Boundary
        query={q}
        compact={false}
        milestone="M16"
        unavailableTitle="Keywords are not built yet"
        unavailableText="Radar keywords come with M16."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No keywords yet', description: canAct ? 'Derive them from your company profile and goals, or add your own. Without keywords only sources where every item matters are kept.' : 'The owner has not set any keywords yet.' }}
      >
        {(keywords) => (
          <div className="space-y-4">
            {ORIGINS.map((origin) => {
              const group = keywords.filter((k) => k.origin === origin)
              if (group.length === 0) return null
              return (
                <section key={origin} aria-label={KEYWORD_ORIGIN_LABEL[origin]} className="space-y-1.5">
                  <h3 className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">{KEYWORD_ORIGIN_LABEL[origin]}</h3>
                  <ul className="divide-y divide-line rounded-md border border-line bg-surface">
                    {group.map((k) => (
                      <KeywordRow key={k.id} companyId={companyId} keyword={k} canAct={canAct} onEdit={() => setEditing(k)} />
                    ))}
                  </ul>
                </section>
              )
            })}
          </div>
        )}
      </Boundary>
      {editing && <KeywordDialog companyId={companyId} keyword={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function KeywordRow({ companyId, keyword: k, canAct, onEdit }: { companyId: string; keyword: RadarKeyword; canAct: boolean; onEdit: () => void }) {
  const update = useUpdateRadarKeyword(companyId)
  const del = useDeleteRadarKeyword(companyId)
  const toast = useToast()
  const [confirm, setConfirm] = useState(false)
  const kind = KEYWORD_KIND[k.kind] ?? { label: k.kind, hint: '' }
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2">
      <UntrustedText className="min-w-0 flex-1 text-[13px] font-medium text-fg">{k.term}</UntrustedText>
      <Badge tone={k.kind === 'negative' ? 'danger' : 'neutral'} title={kind.hint}>
        {kind.label}
      </Badge>
      <span className="text-[12px] text-fg-3" title="Weight in the score">
        weight {k.weight}
      </span>
      {canAct ? (
        <>
          <Switch
            label={`${k.term} is ${k.enabled ? 'on' : 'off'}`}
            checked={k.enabled}
            loading={update.isPending}
            onCheckedChange={(next) => update.mutate({ id: k.id, enabled: next }, { onError: (err) => toast.error(errorMessage(err, 'Could not change the keyword')) })}
          />
          <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={onEdit} aria-label={`Edit ${k.term}`}>
            Edit
          </Button>
          <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm(true)} aria-label={`Delete ${k.term}`}>
            Delete
          </Button>
          <ConfirmDialog
            open={confirm}
            onOpenChange={setConfirm}
            title={`Delete "${k.term}"?`}
            description="The radar stops looking for it. Items already collected are kept."
            confirmLabel="Delete keyword"
            loading={del.isPending}
            error={del.error ? errorMessage(del.error) : null}
            onConfirm={() => del.mutate(k.id, { onSuccess: () => setConfirm(false) })}
          />
        </>
      ) : (
        <Badge tone={k.enabled ? 'success' : 'neutral'}>{k.enabled ? 'On' : 'Off'}</Badge>
      )}
    </li>
  )
}

function KeywordDialog({ companyId, keyword, onClose }: { companyId: string; keyword: RadarKeyword | null; onClose: () => void }) {
  const create = useCreateRadarKeyword(companyId)
  const update = useUpdateRadarKeyword(companyId)
  const toast = useToast()
  const [term, setTerm] = useState(keyword?.term ?? '')
  const [kind, setKind] = useState<KeywordKind>(keyword?.kind ?? 'topic')
  const [weight, setWeight] = useState(String(keyword?.weight ?? 3))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const pending = create.isPending || update.isPending

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setFormError(null)
    const ce: Record<string, string> = {}
    if (!term.trim()) ce.term = 'Enter a word or phrase'
    else if (term.trim().length > 80) ce.term = 'At most 80 characters'
    const w = Number(weight)
    if (!Number.isInteger(w) || w < 1 || w > 5) ce.weight = 'A whole number from 1 to 5'
    setErrors(ce)
    if (Object.keys(ce).length) return
    try {
      if (keyword) {
        // Only what changed.
        await update.mutateAsync({
          id: keyword.id,
          ...(term.trim() !== keyword.term ? { term: term.trim() } : {}),
          ...(kind !== keyword.kind ? { kind } : {}),
          ...(w !== keyword.weight ? { weight: w } : {}),
        })
        toast.success('Keyword saved')
      } else {
        await create.mutateAsync({ term: term.trim(), kind, weight: w })
        toast.success('Keyword added')
      }
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setErrors(err.fieldErrors)
        setFormError(Object.keys(err.fieldErrors).length ? 'Some fields need attention.' : err.message)
      } else {
        setFormError(errorMessage(err, 'Could not save the keyword'))
      }
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !pending && onClose()}
      title={keyword ? 'Edit keyword' : 'Add a keyword'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="radar-keyword" loading={pending}>
            {keyword ? 'Save' : 'Add keyword'}
          </Button>
        </>
      }
    >
      <form id="radar-keyword" onSubmit={submit} noValidate className="space-y-3">
        <Field label="Word or phrase" required error={errors.term}>
          {({ id, invalid, describedBy }) => <Input id={id} value={term} invalid={invalid} aria-describedby={describedBy} maxLength={80} onChange={(e) => setTerm(e.target.value)} />}
        </Field>
        <Field label="Kind" error={errors.kind} hint={KEYWORD_KIND[kind].hint}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={kind} onChange={(e) => setKind(e.target.value as KeywordKind)}>
              {(Object.keys(KEYWORD_KIND) as KeywordKind[]).map((k) => (
                <option key={k} value={k}>
                  {KEYWORD_KIND[k].label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Weight" error={errors.weight} hint="1 (barely matters) to 5 (matters a lot).">
          {({ id, invalid, describedBy }) => <Input id={id} type="number" min={1} max={5} step={1} value={weight} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setWeight(e.target.value)} />}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
