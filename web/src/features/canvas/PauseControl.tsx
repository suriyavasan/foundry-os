import { Pause, Play } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useCompanyControl } from '@/api'
import type { Company } from '@/api/types'
import { Button, ConfirmDialog, useToast } from '@/components/ui'
import { useCanvas } from './CanvasContext'

/** The kill switch. Pausing is one click away but always confirmed, with consequences spelled out. */
export function PauseControl({ company }: { company: Company }) {
  const control = useCompanyControl(company.id)
  const toast = useToast()
  const { pauseOpen: open, setPauseOpen: setOpen } = useCanvas()
  const [error, setError] = useState<string | null>(null)
  const pausing = company.status === 'active'

  async function confirm() {
    setError(null)
    try {
      await control.mutateAsync(pausing ? 'pause' : 'resume')
      toast.success(pausing ? `${company.name} paused` : `${company.name} resumed`)
      setOpen(false)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <>
      <Button
        variant={pausing ? 'danger' : 'primary'}
        size="sm"
        icon={pausing ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
        onClick={() => {
          setError(null)
          setOpen(true)
        }}
      >
        <span className="hidden sm:inline">{pausing ? 'Pause company' : 'Resume company'}</span>
        <span className="sm:hidden">{pausing ? 'Pause' : 'Resume'}</span>
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={(o) => {
          if (!o) setError(null)
          setOpen(o)
        }}
        title={pausing ? `Pause ${company.name}?` : `Resume ${company.name}?`}
        confirmLabel={pausing ? 'Pause company' : 'Resume company'}
        destructive={pausing}
        loading={control.isPending}
        error={error}
        onConfirm={() => void confirm()}
        description={
          pausing ? (
            <div className="space-y-2">
              <p>
                This is the kill switch. Pausing <strong className="font-semibold text-fg">cancels work that is running right now</strong> and{' '}
                <strong className="font-semibold text-fg">stops all dispatch</strong>: no agent starts a new run until you resume.
              </p>
              <p>Tasks, history and spend are kept. Nothing is deleted. You can resume at any time.</p>
            </div>
          ) : (
            <p>Dispatch restarts. Ready tasks assigned to active agents are picked up again; cancelled runs are not resumed automatically.</p>
          )
        }
      />
    </>
  )
}
