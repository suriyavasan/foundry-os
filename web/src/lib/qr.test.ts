import { describe, expect, it } from 'vitest'
import { qrMatrix, qrPath } from './qr'

const URI = 'otpauth://totp/Company%20OS:maya?secret=JBSWY3DPEHPK3PXP&issuer=Company%20OS'

describe('qr', () => {
  it('encodes an otpauth URI into a square matrix with finder patterns', () => {
    const m = qrMatrix(URI)
    expect(m.length).toBeGreaterThanOrEqual(25)
    expect(m.every((row) => row.length === m.length)).toBe(true)
    // the three 7x7 finder patterns have a dark corner and a dark centre
    const n = m.length
    for (const [r, c] of [[0, 0], [0, n - 7], [n - 7, 0]] as const) {
      expect(m[r]![c]).toBe(true)
      expect(m[r + 3]![c + 3]).toBe(true)
      expect(m[r + 1]![c + 1]).toBe(false)
    }
  })
  it('is deterministic', () => {
    expect(qrMatrix(URI)).toEqual(qrMatrix(URI))
    expect(qrMatrix(URI)).not.toEqual(qrMatrix(URI + 'x'))
  })
  it('builds a path that covers exactly the dark modules', () => {
    const m = [[true, true, false], [false, true, true], [false, false, false]]
    expect(qrPath(m)).toBe('M0 0h2v1h-2zM1 1h2v1h-2z')
  })
})
