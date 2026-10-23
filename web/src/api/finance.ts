import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { normalizeBreakdown, normalizeSummary, normalizeTrend, type TrendDay } from '@/lib/finance'
import { apiFetch, fetchAllPages } from './client'
import { qk } from './keys'
import type { ExternalExpense, ExternalExpenseCreate, FinanceBreakdownRow, FinanceGroupBy, FinanceRun, FinanceSummary, Page } from './types'
import { isNotDeployed } from './work'

const co = (cid: string | undefined) => `/companies/${cid}`
const retry = (n: number, e: Error) => !isNotDeployed(e) && n < 2

/** All finance reads take the company-local calendar range (YYYY-MM-DD, inclusive). */
export function useFinanceSummary(cid: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: qk.financeSummary(cid ?? '', from, to),
    queryFn: async ({ signal }): Promise<FinanceSummary | null> => normalizeSummary(await apiFetch<unknown>(`${co(cid)}/finance/summary`, { query: { from, to }, signal })),
    enabled: !!cid,
    retry,
  })
}

export function useFinanceBreakdown(cid: string | undefined, groupBy: FinanceGroupBy, from: string, to: string) {
  return useQuery({
    queryKey: qk.financeBreakdown(cid ?? '', groupBy, from, to),
    queryFn: async ({ signal }): Promise<FinanceBreakdownRow[]> =>
      normalizeBreakdown(await apiFetch<unknown>(`${co(cid)}/finance/breakdown`, { query: { from, to, group_by: groupBy }, signal })),
    enabled: !!cid,
    retry,
  })
}

/** One entry per day of the range (days without spend are zero-filled client-side). */
export function useFinanceTrend(cid: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: qk.financeTrend(cid ?? '', from, to),
    queryFn: async ({ signal }): Promise<TrendDay[]> => normalizeTrend(await apiFetch<unknown>(`${co(cid)}/finance/trend`, { query: { from, to }, signal }), from, to),
    enabled: !!cid,
    retry,
  })
}

export function useFinanceRuns(cid: string | undefined, from: string, to: string) {
  return useInfiniteQuery({
    queryKey: qk.financeRuns(cid ?? '', from, to),
    enabled: !!cid,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => apiFetch<Page<FinanceRun>>(`${co(cid)}/finance/runs`, { query: { from, to, limit: 25, cursor: pageParam }, signal }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry,
  })
}

export function useExpenses(cid: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: qk.expenses(cid ?? '', from, to),
    queryFn: ({ signal }) => fetchAllPages<ExternalExpense>(`${co(cid)}/finance/expenses`, { from, to }, signal, 5),
    enabled: !!cid,
    retry,
  })
}

export function useCreateExpense(cid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ExternalExpenseCreate) => apiFetch<ExternalExpense>(`${co(cid)}/finance/expenses`, { method: 'POST', body }),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: qk.financeAll(cid) }), qc.invalidateQueries({ queryKey: qk.activityAll(cid) })]),
  })
}
