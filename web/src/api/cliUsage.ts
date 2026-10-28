import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import { qk } from './keys'
import { isNotDeployed } from './work'

/** One coding CLI's usage in the period (`GET /companies/{cid}/finance/cli-usage`). Money is integer micro-USD by provenance. */
export interface CliUsageRow {
  /** `cli:<id>`, e.g. `cli:claude_code`. */
  runtime: string
  runs: number
  succeeded: number
  /** failed + cancelled + timed out */
  failed: number
  input_tokens: number
  output_tokens: number
  duration_seconds: number
  verified_micros: number
  estimated_micros: number
  unknown_micros: number
  /** Runs on the person's own CLI login: counted, never given a dollar amount. */
  subscription_runs: number
  agents: string[]
}

export interface CliUsage {
  from: string
  to: string
  timezone: string
  items: CliUsageRow[]
}

/** Company-local calendar range (YYYY-MM-DD, inclusive). A 404 means an older backend: callers show "not available". */
export function useCliUsage(cid: string | undefined, from: string, to: string) {
  return useQuery({
    queryKey: qk.financeCliUsage(cid ?? '', from, to),
    queryFn: ({ signal }) => apiFetch<CliUsage>(`/companies/${cid}/finance/cli-usage`, { query: { from, to }, signal }),
    enabled: !!cid,
    retry: (n, e) => !isNotDeployed(e) && n < 2,
  })
}
