/**
 * The company_agent runtime reports its health as free text (`RuntimeHealth.detail`), including whether
 * a sandbox is available (Docker reachable or not). Show it verbatim, and pull the sandbox sentences out
 * so the answer to "will my agents run isolated?" is not buried. Nothing is inferred beyond the text.
 */
export interface RuntimeDetailParts {
  sandbox: string[]
  rest: string[]
}

export function splitRuntimeDetail(detail: string | null | undefined): RuntimeDetailParts {
  const parts = (detail ?? '')
    .split(/(?<=[.;])\s+|\n+/)
    .map((p) => p.trim())
    .filter(Boolean)
  const sandbox = parts.filter((p) => /sandbox|docker|isolat|container/i.test(p))
  return { sandbox, rest: parts.filter((p) => !sandbox.includes(p)) }
}

export const COMPANY_AGENT_RUNTIME = 'company_agent'
