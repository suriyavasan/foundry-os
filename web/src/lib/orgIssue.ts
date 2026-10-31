import type { OrgIssue } from './orgTree'

/** What is wrong around a node of the reporting tree, in words (shown beside the person; the node is never hidden). */
export const OrgIssueText: Record<OrgIssue, string> = {
  no_manager: 'No manager assigned',
  missing_manager: 'Manager is not in this company',
  cycle: 'Part of a reporting cycle',
}
