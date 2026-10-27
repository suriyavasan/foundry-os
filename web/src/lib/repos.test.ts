import { describe, expect, it } from 'vitest'
import { defaultPolicy, makeInspect, makeRepo } from '@/test/m14'
import {
  codeSummary,
  createErrorHint,
  diffPolicy,
  directPushBlockedReason,
  emptyRepoForm,
  parseRepoRef,
  permissionChecklist,
  permissionState,
  protectionLines,
  pullStageIndex,
  slugRepoName,
  textToPaths,
  toRepositoryCreate,
  tokenExpiresSoon,
  tokenExpiryText,
  validBranchName,
  validateRepoForm,
  type RepoFormValues,
} from './repos'

const form = (over: Partial<RepoFormValues> = {}): RepoFormValues => ({ ...emptyRepoForm({ repo: 'acme-labs' }), ...over })

describe('repository references and names', () => {
  it('parses owner/name and github.com URLs like the server', () => {
    expect(parseRepoRef('octo/hello')).toEqual({ owner: 'octo', name: 'hello' })
    expect(parseRepoRef('  https://github.com/octo/hello.git ')).toEqual({ owner: 'octo', name: 'hello' })
    expect(parseRepoRef('https://github.com/octo/hello/')).toEqual({ owner: 'octo', name: 'hello' })
    expect(parseRepoRef('octo/hello.git')).toBeNull()
    expect(parseRepoRef('octo/..')).toBeNull()
    expect(parseRepoRef('hello')).toBeNull()
    expect(parseRepoRef('-octo/hello')).toBeNull()
  })

  it('checks branch names with git ref-format rules', () => {
    for (const ok of ['main', 'develop', 'release/1.0', 'foundry/acme']) expect(validBranchName(ok), ok).toBe(true)
    for (const bad of ['', 'a..b', '/x', 'x/', '-x', 'a b', 'a~b', 'a^b', 'a:b', 'x.lock', 'a//b', '.hidden', 'HEAD', '@', 'x.', 'a@{b']) expect(validBranchName(bad), bad).toBe(false)
  })

  it('suggests a repository name from a company name', () => {
    expect(slugRepoName('Acme Labs!')).toBe('acme-labs')
    expect(slugRepoName('  Café Münch  ')).toBe('cafe-munch')
    expect(slugRepoName('!!!')).toBe('')
  })
})

describe('the repository form', () => {
  it('validates a new repository', () => {
    expect(validateRepoForm(form(), { choice: 'new' })).toEqual({})
    expect(validateRepoForm(form({ repo: '' }), { choice: 'new' }).repo).toBeTruthy()
    expect(validateRepoForm(form({ repo: 'a b' }), { choice: 'new' }).repo).toBeTruthy()
    expect(validateRepoForm(form({ repo: 'x.git' }), { choice: 'new' }).repo).toBeTruthy()
    expect(validateRepoForm(form({ owner: '-bad' }), { choice: 'new' }).owner).toBeTruthy()
    expect(validateRepoForm(form({ defaultBranch: 'a..b' }), { choice: 'new' }).defaultBranch).toBeTruthy()
    expect(validateRepoForm(form({ description: 'x'.repeat(351) }), { choice: 'new' }).description).toBeTruthy()
  })

  it('requires explicit confirmation for a public repository', () => {
    expect(validateRepoForm(form({ visibility: 'public' }), { choice: 'new' }).confirmPublic).toBeTruthy()
    expect(validateRepoForm(form({ visibility: 'public', confirmPublic: true }), { choice: 'new' })).toEqual({})
    expect(toRepositoryCreate(form({ visibility: 'public', confirmPublic: true }), { choice: 'new', accountId: 'a' })).toMatchObject({ visibility: 'public', confirm_public: true })
    expect(toRepositoryCreate(form(), { choice: 'new', accountId: 'a' })).not.toHaveProperty('confirm_public')
  })

  it('validates an existing repository reference', () => {
    expect(validateRepoForm(form({ repo: '' }), { choice: 'existing' }).repo).toBeTruthy()
    expect(validateRepoForm(form({ repo: 'nope' }), { choice: 'existing' }).repo).toBeTruthy()
    expect(validateRepoForm(form({ repo: 'octo/hello' }), { choice: 'existing' })).toEqual({})
    expect(validateRepoForm(form({ repo: 'octo/hello', issueIntake: 'labeled', intakeLabel: ' ' }), { choice: 'existing' }).intakeLabel).toBeTruthy()
  })

  it('builds the create body: policy fields left on "Recommended" are not sent', () => {
    expect(toRepositoryCreate(form({ owner: 'octo', description: ' Sell lemonade ' }), { choice: 'new', accountId: 'a1' })).toEqual({
      mode: 'create',
      connected_account_id: 'a1',
      project_name: 'acme-labs',
      name: 'acme-labs',
      owner: 'octo',
      visibility: 'private',
      description: 'Sell lemonade',
    })
    expect(
      toRepositoryCreate(form({ repo: 'https://github.com/octo/legacy', projectName: 'Legacy app', defaultBranch: 'develop', mergeMode: 'pr_human_merge', ciMode: 'required', issueIntake: 'labeled', intakeLabel: 'bot' }), { choice: 'existing', accountId: 'a1' }),
    ).toEqual({
      mode: 'connect',
      connected_account_id: 'a1',
      project_name: 'Legacy app',
      full_name: 'https://github.com/octo/legacy',
      default_branch: 'develop',
      policy: { merge_mode: 'pr_human_merge', ci_mode: 'required', issue_intake: 'labeled', intake_label: 'bot' },
    })
  })

  it('binds an existing project instead of naming a new one, and publishes local history only for a bound project', () => {
    const body = toRepositoryCreate(form({ repo: 'octo/legacy', publishLocal: true }), { choice: 'existing', accountId: 'a1', projectId: 'p9' })
    expect(body).toMatchObject({ project_id: 'p9', publish_local: true })
    expect(body).not.toHaveProperty('project_name')
    expect(toRepositoryCreate(form({ repo: 'octo/legacy', publishLocal: true }), { choice: 'existing', accountId: 'a1' })).not.toHaveProperty('publish_local')
  })
})

describe('permissions and the token', () => {
  it('lists Administration only when creating, and Webhooks as optional', () => {
    expect(permissionChecklist('new').map((r) => r.permission)).toContain('Administration')
    expect(permissionChecklist('existing').map((r) => r.permission)).not.toContain('Administration')
    expect(permissionChecklist('existing').find((r) => r.permission === 'Webhooks')!.optional).toBe(true)
  })

  it('reads granted / missing / unknown from the capabilities', () => {
    const rows = permissionChecklist('existing')
    const contents = rows.find((r) => r.permission === 'Contents')!
    expect(permissionState(contents, { contents: true })).toBe('ok')
    expect(permissionState(contents, { contents: false })).toBe('missing')
    expect(permissionState(contents, {})).toBe('unknown')
    expect(permissionState(contents, null)).toBe('unknown')
    expect(permissionState(rows[0]!, { contents: true })).toBe('unknown') // Metadata cannot be probed
    expect(makeInspect().missing_permissions).toEqual([])
  })

  it('says how long a token has left and warns two weeks out', () => {
    const now = Date.parse('2026-10-27T12:00:00Z')
    expect(tokenExpiryText(null, now)).toBe('No expiry reported')
    expect(tokenExpiryText('2026-10-26T00:00:00Z', now)).toBe('Expired')
    expect(tokenExpiryText('2026-10-27T20:00:00Z', now)).toBe('Expires today')
    expect(tokenExpiryText('2026-10-29T13:00:00Z', now)).toBe('Expires in 2 days')
    expect(tokenExpiresSoon('2026-11-05T00:00:00Z', now)).toBe(true)
    expect(tokenExpiresSoon('2027-01-01T00:00:00Z', now)).toBe(false)
    expect(tokenExpiresSoon(null, now)).toBe(false)
  })
})

describe('error hints and protection', () => {
  it('turns the create error codes into next steps', () => {
    expect(createErrorHint('repository_exists')).toMatch(/already exists/)
    expect(createErrorHint('administration_permission_missing')).toMatch(/Administration/)
    expect(createErrorHint('token_cannot_push', { missing_permissions: ['Contents: write'] })).toMatch(/Contents: write/)
    expect(createErrorHint('branch_protected')).toMatch(/pull requests/)
    expect(createErrorHint('something_else')).toBeNull()
  })

  it('refuses direct push on a protected branch and describes the protection', () => {
    expect(directPushBlockedReason({ protection: { protected: false } })).toBeNull()
    expect(directPushBlockedReason({ protection: { protected: true, require_pr: true } })).toMatch(/protected/)
    expect(protectionLines({})).toEqual(['Not checked yet'])
    expect(protectionLines({ branch: 'main', protected: false, rules_known: true })).toEqual(['main is not protected'])
    expect(protectionLines({ branch: 'main', protected: true, require_pr: true, required_approvals: 2, required_checks: ['ci', 'lint'], rules_known: false })).toEqual([
      'main is protected',
      'Changes need a pull request',
      '2 approving reviews required',
      'Required checks: ci, lint',
      'Rulesets could not be read, so this may be incomplete',
    ])
  })
})

describe('policy editing and summaries', () => {
  it('sends only the changed policy fields', () => {
    const cur = defaultPolicy()
    expect(diffPolicy(cur, { ...cur })).toEqual({})
    expect(diffPolicy(cur, { ...cur, merge_mode: 'pr_auto_merge', protected_paths: ['a/**'] })).toEqual({ merge_mode: 'pr_auto_merge', protected_paths: ['a/**'] })
  })
  it('edits protected paths one glob per line', () => {
    expect(textToPaths(' a/**\n\n  b \n')).toEqual(['a/**', 'b'])
  })
  it('places pull request stages on the timeline', () => {
    expect(pullStageIndex('opening')).toBe(0)
    expect(pullStageIndex('changes_requested')).toBe(pullStageIndex('reviewing'))
    expect(pullStageIndex('awaiting_human')).toBe(pullStageIndex('merging'))
    expect(pullStageIndex('closed')).toBe(-1)
  })
  it('summarises the code line for the Team step', () => {
    expect(codeSummary(makeRepo())).toBe('octo/acme-labs · auto-merge')
  })
})
