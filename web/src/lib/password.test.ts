import { describe, expect, it } from 'vitest'
import { PASSWORD_MIN_LENGTH, checkPassword, estimateBits, isCommonPassword, validateNewPassword } from './password'

describe('password rules', () => {
  it('requires at least 12 characters', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(12)
    expect(checkPassword('Abc!2345678').tooShort).toBe(true)
    expect(checkPassword('Abc!23456789').tooShort).toBe(false)
    expect(checkPassword('Abc!2345678').feedback[0]).toMatch(/at least 12 characters \(1 more\)/)
  })

  it('counts characters, not UTF-16 units', () => {
    expect(checkPassword('😀'.repeat(12)).length).toBe(12)
  })

  it('flags common passwords, their suffixed and l33t forms, and single repeated characters', () => {
    for (const pw of ['password1234', 'Password123!', 'P@ssw0rd', 'qwertyuiop123', 'iloveyou1234', 'aaaaaaaaaaaaa', '123456789012', 'Welcome1234']) {
      expect(isCommonPassword(pw), pw).toBe(true)
    }
    expect(isCommonPassword('violet-lantern-orbit-seven')).toBe(false)
    expect(isCommonPassword('')).toBe(false)
  })

  it('warns about a common password and refuses it even when long enough', () => {
    const c = checkPassword('password1234')
    expect(c.common).toBe(true)
    expect(c.acceptable).toBe(false)
    expect(c.feedback.join(' ')).toMatch(/commonly used/)
  })

  it('refuses a password built from the username or name', () => {
    const c = checkPassword('maya.chen-2026-xyz', { username: 'maya.chen' })
    expect(c.containsAccount).toBe(true)
    expect(c.acceptable).toBe(false)
    expect(checkPassword('violet-lantern-orbit-seven', { username: 'maya.chen', displayName: 'Maya Chen' }).acceptable).toBe(true)
  })

  it('accepts a long unrelated passphrase', () => {
    const c = checkPassword('violet-lantern-orbit-seven')
    expect(c.acceptable).toBe(true)
    expect(c.score).toBeGreaterThanOrEqual(3)
  })
})

describe('password strength', () => {
  it('scores weak patterns low and long random-looking ones high', () => {
    expect(checkPassword('').score).toBe(0)
    expect(checkPassword('abcdefghijkl').score).toBeLessThanOrEqual(1) // a run
    expect(checkPassword('qwertyuiopas').score).toBeLessThanOrEqual(1) // keyboard row
    expect(checkPassword('aaaaaaaaaaaaZ').score).toBeLessThanOrEqual(1)
    expect(checkPassword('Tr0ub4dor&3-horse-staple-xyz').score).toBe(4)
  })

  it('never rates a too-short password above Weak', () => {
    expect(checkPassword('Xk9$mQ2!').score).toBeLessThanOrEqual(1)
  })

  it('estimates fewer bits for repeats than for varied characters', () => {
    expect(estimateBits('aaaaaaaaaaaa')).toBeLessThan(estimateBits('aZ3$kQ9!mW2#'))
  })

  it('labels every score', () => {
    expect(checkPassword('violet-lantern-orbit-seven').label).toMatch(/Good|Strong/)
    expect(checkPassword('abc').label).toBe('Very weak')
  })
})

describe('validateNewPassword', () => {
  it('reports each problem on the right field', () => {
    expect(validateNewPassword('', '')).toEqual({ password: 'Choose a password', confirm: 'Type the password again' })
    expect(validateNewPassword('short', 'short').password).toMatch(/at least 12/)
    expect(validateNewPassword('password1234', 'password1234').password).toMatch(/too common/)
    expect(validateNewPassword('violet-lantern-orbit-seven', 'violet-lantern-orbit-sevn').confirm).toMatch(/do not match/)
    expect(validateNewPassword('violet-lantern-orbit-seven', 'violet-lantern-orbit-seven')).toEqual({})
  })
})
