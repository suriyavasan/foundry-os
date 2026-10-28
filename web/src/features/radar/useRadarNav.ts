import { useCallback } from 'react'
import { useSearchParams } from 'react-router'
import { isRadarTab, type RadarTab } from './radarLogic'

/** The current tab (from `?tab=`) and a setter that replaces the URL entry, so tabs do not pile up in history. */
export function useRadarTab(): [RadarTab, (tab: RadarTab) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: RadarTab = isRadarTab(raw) ? raw : 'briefs'
  const set = useCallback(
    (next: RadarTab) =>
      setParams(
        (p) => {
          const n = new URLSearchParams(p)
          n.set('tab', next)
          return n
        },
        { replace: true },
      ),
    [setParams],
  )
  return [tab, set]
}
