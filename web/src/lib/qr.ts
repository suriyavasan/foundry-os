import qrcode from 'qrcode-generator'

/** Module matrix of a QR code for `text` (true = dark). Error correction M; the version is picked to fit. */
export function qrMatrix(text: string): boolean[][] {
  const qr = qrcode(0, 'M')
  qr.addData(text, 'Byte')
  qr.make()
  const n = qr.getModuleCount()
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => qr.isDark(r, c)))
}

/** One SVG path covering every dark module (horizontal runs merged), in module units. */
export function qrPath(matrix: boolean[][]): string {
  const parts: string[] = []
  matrix.forEach((row, y) => {
    let x = 0
    while (x < row.length) {
      if (!row[x]) {
        x++
        continue
      }
      let end = x
      while (end < row.length && row[end]) end++
      parts.push(`M${x} ${y}h${end - x}v1h${x - end}z`)
      x = end
    }
  })
  return parts.join('')
}
