import { useCallback } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { parsePanel, withPanel } from '@/lib/panels'

export const WALLET_TABS = ['overview', 'purchases', 'cards', 'subscriptions', 'transactions', 'setup'] as const
export type WalletTab = (typeof WALLET_TABS)[number]

export const isWalletTab = (v: string | null): v is WalletTab => !!v && (WALLET_TABS as readonly string[]).includes(v)

/** Open the Wallet drawer, optionally on one tab: `?panel=wallet&tab=setup`. Pushes a history entry like any panel. */
export function useOpenWallet() {
  const location = useLocation()
  const navigate = useNavigate()
  return useCallback(
    (tab?: WalletTab) => {
      const params = new URLSearchParams(withPanel(location.search, { kind: 'wallet' }))
      if (tab) params.set('tab', tab)
      else params.delete('tab')
      const depth = (location.state as { panelDepth?: number } | null)?.panelDepth ?? 0
      const open = parsePanel(location.search)
      navigate(
        { pathname: location.pathname, search: `?${params.toString().replace(/%3A/g, ':')}` },
        { state: { panelDepth: open ? depth + 1 : 1 } },
      )
    },
    [navigate, location.pathname, location.search, location.state],
  )
}

/** The current tab (from `?tab=`) and a setter that replaces the URL entry, so tabs do not pile up in history. */
export function useWalletTab(): [WalletTab, (tab: WalletTab) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: WalletTab = isWalletTab(raw) ? raw : 'overview'
  const set = useCallback(
    (next: WalletTab) =>
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
