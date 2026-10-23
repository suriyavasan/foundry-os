import { configure } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Full-canvas renders are heavy; under the parallel full suite they can exceed Testing
// Library's 1 s default for findBy*/waitFor and flake. Give async queries more headroom.
configure({ asyncUtilTimeout: 5000 })


afterEach(() => {
  cleanup()
})
