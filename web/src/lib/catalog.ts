import type { CatalogItem, InstallSkillResult } from '@/api/types'
import { scanVerdictInfo, trustLabel } from './skills'

/** `owner/repo` to browse a GitHub repository result (its slug, or parsed from `github:owner/repo[@ref][:path]`). */
export function repoQueryFor(item: Pick<CatalogItem, 'slug' | 'source_ref'>): string {
  if (/^[\w.-]+\/[\w.-]+$/.test(item.slug)) return item.slug
  const m = /^github:([\w.-]+\/[\w.-]+?)(?:[@:].*)?$/.exec(item.source_ref)
  return m ? m[1]! : item.slug
}

/** Plain-language outcome of an install, naming license, trust and scan, and what quarantine means. */
export function describeInstall(r: InstallSkillResult): { headline: string; detail: string } {
  const verb = r.outcome === 'installed' ? 'Installed' : r.outcome === 'updated' ? 'Updated' : r.outcome === 'unchanged' ? 'Already up to date' : r.outcome
  const bits = [
    `License: ${r.skill.license ?? 'none declared'}`,
    `Trust: ${trustLabel(r.skill.trust).toLowerCase()}`,
    `Scan: ${scanVerdictInfo(r.skill.scan_verdict).label.toLowerCase()}`,
  ]
  const detail = r.quarantined
    ? `${bits.join(' · ')}. Quarantined: it is stored so you can inspect it, but no agent can use it until you review the scan report and explicitly unquarantine it.`
    : `${bits.join(' · ')}. It is active and can be assigned to agents.`
  return { headline: r.quarantined ? `${verb}, but quarantined` : verb, detail }
}
