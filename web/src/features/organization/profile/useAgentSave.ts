import { useState } from 'react'
import { ApiError, errorMessage, useUpdateAgent } from '@/api'
import type { Agent, UpdateAgentRequest } from '@/api/types'
import { useToast } from '@/components/ui'

/** PATCH an agent and surface 409/422 errors inline (form-level message + per-field errors). */
export function useAgentSave(agent: Agent) {
  const update = useUpdateAgent(agent.company_id, agent.id)
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  async function save(body: UpdateAgentRequest, successMessage = 'Saved'): Promise<boolean> {
    setError(null)
    setFieldErrors({})
    try {
      await update.mutateAsync(body)
      toast.success(successMessage)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        setFieldErrors(fe)
        if (Object.keys(fe).length === 0) setError(err.message)
        else setError('Some fields need attention.')
      } else {
        setError(errorMessage(err))
      }
      return false
    }
  }

  return { save, saving: update.isPending, error, fieldErrors, clearError: () => setError(null) }
}
