import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import type { CompleteTaskRequest, Task } from './types'

const co = (cid: string) => `/companies/${cid}`

/**
 * A human finishing or blocking their own task (docs/specs/m7 §3). An optional file is uploaded first as an
 * artifact of the task (`POST /artifacts`, multipart), then the task is completed with the comment and the artifact id.
 * ASSUMPTION: `POST /tasks/{id}/complete {comment?, artifact_ids?}`; blocking uses the existing `POST /tasks/{id}/block {reason}`.
 */
export function useHumanTaskAction(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { task: Task; action: 'complete'; comment: string; file: File | null } | { task: Task; action: 'block'; comment: string }) => {
      if (v.action === 'block') return apiFetch<Task>(`${co(cid)}/tasks/${v.task.id}/block`, { method: 'POST', body: { reason: v.comment } })
      const artifact_ids: string[] = []
      if (v.file) {
        const form = new FormData()
        form.append('file', v.file)
        form.append('title', v.file.name)
        form.append('kind', 'other')
        form.append('task_id', v.task.id)
        const artifact = await apiFetch<{ id: string }>(`${co(cid)}/artifacts`, { method: 'POST', body: form })
        artifact_ids.push(artifact.id)
      }
      const body: CompleteTaskRequest = { ...(v.comment.trim() ? { comment: v.comment.trim() } : null), ...(artifact_ids.length ? { artifact_ids } : null) }
      return apiFetch<Task>(`${co(cid)}/tasks/${v.task.id}/complete`, { method: 'POST', body })
    },
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: qk.tasksAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.artifactsAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.activityAll(cid) }),
        qc.invalidateQueries({ queryKey: qk.overview(cid) }),
      ]),
  })
}
