/**
 * Client-side password feedback (docs/specs/m7 §1: >= 12 characters, strength check, not in a common list).
 * The server is the authority; this only tells the person what is wrong before they submit. It is a small
 * zxcvbn-style estimate (character pool, repeats, sequences, keyboard runs, a common-password list), not zxcvbn.
 */

export const PASSWORD_MIN_LENGTH = 12

/** Lowercase. Short entries only matter as the stem of a longer password ("password" + digits). */
const COMMON = [
  '123456', '1234567', '12345678', '123456789', '1234567890', '12345678901', '123456789012', '1234567890123',
  '111111', '000000', '121212', '654321', '987654321', '0987654321', '1q2w3e4r', '1q2w3e4r5t', '1qaz2wsx', '1qaz2wsx3edc',
  'password', 'passw0rd', 'p@ssword', 'p@ssw0rd', 'pass1234', 'password1', 'password12', 'password123', 'password1234', 'password12345',
  'qwerty', 'qwerty123', 'qwertyuiop', 'qwertyuiop123', 'qwerty123456', 'qazwsx', 'qazwsxedc', 'asdfgh', 'asdfghjkl', 'asdfghjkl123', 'zxcvbnm', 'zxcvbnm123',
  'abc123', 'abcd1234', 'abcdef', 'abcdefg', 'abcdefgh', 'abcdefghij', 'abcdefghijkl', 'abc12345',
  'iloveyou', 'iloveyou123', 'iloveyou1234', 'letmein', 'letmein123', 'welcome', 'welcome1', 'welcome123', 'welcome1234',
  'admin', 'admin123', 'admin1234', 'administrator', 'root', 'rootroot', 'toor', 'login', 'master', 'superman', 'batman', 'dragon', 'monkey',
  'football', 'baseball', 'basketball', 'soccer', 'hockey', 'princess', 'sunshine', 'shadow', 'michael', 'jordan', 'harley', 'ranger', 'buster',
  'trustno1', 'changeme', 'changeme123', 'default', 'secret', 'secret123', 'letmein1', 'whatever', 'freedom', 'starwars', 'pokemon', 'passpass',
  'companyos', 'company-os', 'companyos123', 'opensesame', 'mypassword', 'mypassword1', 'myp@ssword', 'thequickbrownfox', 'correcthorsebatterystaple',
  'internet', 'computer', 'ninja', 'hello', 'hello123', 'helloworld', 'helloworld123', 'test', 'test123', 'testtest', 'testing', 'testing123', 'guest', 'user',
  'aaaaaa', 'aaaaaaaa', 'aaaaaaaaaaaa', 'qqqqqq', 'zzzzzz', 'asdasd', 'asdfasdf', 'asdf1234', 'q1w2e3r4', 'q1w2e3r4t5', 'zaq12wsx', 'mnbvcxz', 'poiuytrewq', 'lkjhgfdsa',
]

const COMMON_SET: ReadonlySet<string> = new Set(COMMON)

const LEET: Record<string, string> = { '@': 'a', '4': 'a', '0': 'o', '1': 'l', '!': 'i', '3': 'e', $: 's', '5': 's', '7': 't' }
const unLeet = (s: string) => s.replace(/[@4013!$57]/g, (c) => LEET[c] ?? c)
const TRAILING = /[\d!@#$%^&*()_+=.,?-]+$/

/** True for a password on the common list, a trivially altered form of one (suffix digits, l33t), or a single repeated character. */
export function isCommonPassword(pw: string): boolean {
  const lower = pw.toLowerCase().trim()
  if (lower.length === 0) return false
  if (new Set(lower).size === 1) return true
  const forms = new Set([lower, lower.replace(TRAILING, ''), unLeet(lower), unLeet(lower).replace(TRAILING, '')])
  for (const f of forms) {
    if (f.length >= 4 && COMMON_SET.has(f)) return true
  }
  return false
}

export type PasswordScore = 0 | 1 | 2 | 3 | 4

export const PASSWORD_SCORE_LABEL: Record<PasswordScore, string> = {
  0: 'Very weak',
  1: 'Weak',
  2: 'Fair',
  3: 'Good',
  4: 'Strong',
}

const KEYBOARD_RUNS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm', '1234567890', 'azertyuiop', 'qwertzuiop']

function charsetSize(pw: string): number {
  let pool = 0
  if (/[a-z]/.test(pw)) pool += 26
  if (/[A-Z]/.test(pw)) pool += 26
  if (/\d/.test(pw)) pool += 10
  if (/[ -/:-@[-`{-~]/.test(pw)) pool += 33
  if (/[^\x20-\x7e]/.test(pw)) pool += 64
  return Math.max(pool, 1)
}

/** Rough entropy in bits: a full pool-sized draw per new character, almost nothing for repeats, runs and keyboard walks. */
export function estimateBits(pw: string): number {
  const chars = [...pw]
  if (chars.length === 0) return 0
  const perChar = Math.log2(charsetSize(pw))
  const lower = pw.toLowerCase()
  const cheap = new Array<boolean>(chars.length).fill(false)

  // runs of a constant step (aaaa, 1234, 9876) of length >= 3
  let i = 0
  while (i < chars.length - 1) {
    const step = chars[i + 1]!.codePointAt(0)! - chars[i]!.codePointAt(0)!
    if (Math.abs(step) > 1) {
      i++
      continue
    }
    let j = i + 1
    while (j + 1 < chars.length && chars[j + 1]!.codePointAt(0)! - chars[j]!.codePointAt(0)! === step) j++
    if (j - i + 1 >= 3) for (let k = i + 1; k <= j; k++) cheap[k] = true
    i = j
  }
  // keyboard rows, forwards or backwards, 4+ characters
  for (const row of KEYBOARD_RUNS) {
    for (const r of [row, [...row].reverse().join('')]) {
      for (let len = 4; len <= Math.min(r.length, lower.length); len++) {
        for (let at = 0; at + len <= r.length; at++) {
          const seg = r.slice(at, at + len)
          let from = lower.indexOf(seg)
          while (from !== -1) {
            for (let k = from + 1; k < from + len; k++) cheap[k] = true
            from = lower.indexOf(seg, from + 1)
          }
        }
      }
    }
  }

  const seen = new Set<string>()
  let bits = 0
  chars.forEach((c, idx) => {
    if (cheap[idx]) bits += 1
    else if (seen.has(c)) bits += 1.2
    else bits += perChar
    seen.add(c)
  })
  return bits
}

export interface PasswordContext {
  username?: string
  displayName?: string
  email?: string
}

export interface PasswordCheck {
  length: number
  tooShort: boolean
  common: boolean
  containsAccount: boolean
  score: PasswordScore
  label: string
  /** Specific, actionable lines (empty when there is nothing to say). */
  feedback: string[]
  /** Meets the rules the server enforces: long enough, not common, not built from the account name. */
  acceptable: boolean
}

function scoreFor(bits: number): PasswordScore {
  if (bits < 30) return 0
  if (bits < 45) return 1
  if (bits < 60) return 2
  if (bits < 80) return 3
  return 4
}

export function checkPassword(pw: string, ctx: PasswordContext = {}): PasswordCheck {
  const length = [...pw].length
  const tooShort = length < PASSWORD_MIN_LENGTH
  const common = isCommonPassword(pw)
  const lower = pw.toLowerCase()
  const account = [ctx.username, ctx.displayName, ctx.email?.split('@')[0]]
    .map((s) => s?.trim().toLowerCase() ?? '')
    .filter((s) => s.length >= 4)
  const containsAccount = account.some((a) => lower.includes(a))

  let score = length === 0 ? 0 : scoreFor(estimateBits(pw))
  if (common || containsAccount) score = Math.min(score, 1) as PasswordScore
  if (tooShort) score = Math.min(score, 1) as PasswordScore

  const feedback: string[] = []
  if (length > 0) {
    if (tooShort) feedback.push(`Use at least ${PASSWORD_MIN_LENGTH} characters (${PASSWORD_MIN_LENGTH - length} more).`)
    if (common) feedback.push('This password is on lists of commonly used passwords. Pick something else.')
    if (containsAccount) feedback.push('Do not build the password from your name or username.')
    if (!tooShort && !common && !containsAccount && score <= 2) feedback.push('Add a few unrelated words or more characters. Length matters more than symbols.')
  }
  return {
    length,
    tooShort,
    common,
    containsAccount,
    score: score as PasswordScore,
    label: PASSWORD_SCORE_LABEL[score as PasswordScore],
    feedback,
    acceptable: !tooShort && !common && !containsAccount,
  }
}

export interface NewPasswordErrors {
  password?: string
  confirm?: string
}

/** Errors for a new password + confirmation pair; empty when it is fine to submit. */
export function validateNewPassword(password: string, confirm: string, context?: PasswordContext): NewPasswordErrors {
  const check = checkPassword(password, context)
  const out: NewPasswordErrors = {}
  if (!password) out.password = 'Choose a password'
  else if (check.tooShort) out.password = `Use at least ${PASSWORD_MIN_LENGTH} characters`
  else if (check.common) out.password = 'That password is too common. Pick another.'
  else if (check.containsAccount) out.password = 'Do not build the password from your name or username.'
  if (!confirm) out.confirm = 'Type the password again'
  else if (confirm !== password) out.confirm = 'The passwords do not match'
  return out
}
