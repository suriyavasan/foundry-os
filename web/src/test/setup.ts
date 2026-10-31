import { configure } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll } from 'vitest'
import { loadFaces } from '@/lib/faces'

// Full-canvas renders are heavy; under the parallel full suite they can exceed Testing
// Library's 1 s default for findBy*/waitFor and flake. Give async queries more headroom.
configure({ asyncUtilTimeout: 5000 })


// Illustrated faces load on demand in the app; tests draw them from the first render.
beforeAll(() => loadFaces())

afterEach(() => {
  cleanup()
})
