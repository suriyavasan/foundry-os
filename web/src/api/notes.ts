import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { confirmOptimistic, optimisticNote, patchNote, rollbackOptimistic, withOptimistic, withReply } from '@/lib/notes'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { ClientNote, CreateNoteReplyRequest, CreateNoteRequest, FounderNote, NoteDetail, NoteReply, UpdateNoteRequest } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** Every note of the company (bounded). Filtering, search and ordering are done client-side (lib/notes). */
export function useNotes(cid: string | undefined) {
  return useQuery({
    queryKey: qk.notes(cid ?? ''),
    queryFn: ({ signal }) => fetchAllPages<ClientNote>(`${co(cid)}/notes`, {}, signal),
    enabled: !!cid,
    retry,
  })
}

/** One note with its whole reply thread. */
export function useNote(cid: string | undefined, id: string | undefined) {
  return useQuery({
    queryKey: qk.note(cid ?? '', id ?? ''),
    queryFn: ({ signal }) => apiFetch<NoteDetail>(`${co(cid)}/notes/${id}`, { signal }),
    enabled: !!cid && !!id,
    retry,
  })
}

/**
 * Post a note. The list shows it immediately (optimistic row); if the server refuses, the row is removed
 * again and the caller gets the error to put the text back in the composer.
 */
export function useCreateNote(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateNoteRequest) => apiFetch<FounderNote>(`${co(cid)}/notes`, { method: 'POST', body }),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: qk.notes(cid) })
      const tmp = optimisticNote(vars.body, vars.attachments ?? [], cid)
      qc.setQueryData<ClientNote[]>(qk.notes(cid), (old) => withOptimistic(old, tmp))
      return { pendingId: tmp.id }
    },
    onError: (_e, _v, ctx) => {
      if (ctx) qc.setQueryData<ClientNote[]>(qk.notes(cid), (old) => rollbackOptimistic(old, ctx.pendingId))
    },
    onSuccess: (real, _v, ctx) => {
      qc.setQueryData<ClientNote[]>(qk.notes(cid), (old) => confirmOptimistic(old, ctx?.pendingId ?? '', real))
      void qc.invalidateQueries({ queryKey: qk.activityAll(cid) })
    },
  })
}

/** Reply in a note's thread. Replying re-opens triage (the AI Co-founder looks at the note again). */
export function useReplyToNote(cid: string, noteId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateNoteReplyRequest) => apiFetch<NoteReply>(`${co(cid)}/notes/${noteId}/replies`, { method: 'POST', body }),
    onSuccess: (reply) => {
      qc.setQueryData<NoteDetail>(qk.note(cid, noteId), (old) =>
        old && !old.replies.some((r) => r.id === reply.id) ? { ...old, replies: [...old.replies, reply], reply_count: (old.reply_count ?? old.replies.length) + 1, last_reply: reply } : old,
      )
      qc.setQueryData<ClientNote[]>(qk.notes(cid), (old) => withReply(old, reply))
      void qc.invalidateQueries({ queryKey: qk.note(cid, noteId) })
      void qc.invalidateQueries({ queryKey: qk.notes(cid) })
      void qc.invalidateQueries({ queryKey: qk.cofounderSummary(cid) })
    },
  })
}

/** Pin / unpin / archive. Optimistic, rolled back on error. */
export function useUpdateNote(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: UpdateNoteRequest & { id: string }) => apiFetch<FounderNote>(`${co(cid)}/notes/${id}`, { method: 'PATCH', body }),
    onMutate: async ({ id, ...patch }) => {
      await qc.cancelQueries({ queryKey: qk.notes(cid) })
      const prevList = qc.getQueryData<ClientNote[]>(qk.notes(cid))
      const prevDetail = qc.getQueryData<NoteDetail>(qk.note(cid, id))
      const apply: Partial<FounderNote> = { ...(patch.pinned !== undefined ? { pinned: patch.pinned } : null), ...(patch.status ? { status: patch.status } : null) }
      qc.setQueryData<ClientNote[]>(qk.notes(cid), (old) => patchNote(old, id, apply))
      qc.setQueryData<NoteDetail>(qk.note(cid, id), (old) => (old ? { ...old, ...apply } : old))
      return { prevList, prevDetail, id }
    },
    onError: (_e, _v, ctx) => {
      if (!ctx) return
      if (ctx.prevList) qc.setQueryData(qk.notes(cid), ctx.prevList)
      if (ctx.prevDetail) qc.setQueryData(qk.note(cid, ctx.id), ctx.prevDetail)
    },
    onSettled: (_d, _e, vars) => {
      void qc.invalidateQueries({ queryKey: qk.notes(cid) })
      void qc.invalidateQueries({ queryKey: qk.note(cid, vars.id) })
    },
  })
}
