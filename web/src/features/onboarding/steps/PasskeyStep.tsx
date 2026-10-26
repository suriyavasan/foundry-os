import { ArrowRight, Check, Fingerprint } from 'lucide-react'
import { motion } from 'motion/react'
import { useRef, useState } from 'react'
import { useRegisterPasskey } from '@/api'
import type { PasskeyInfo } from '@/api/types'
import { Button, FormError } from '@/components/ui'
import { usePrefersReducedMotion } from '@/lib/motion'
import { defaultPasskeyName, describePasskeyFailure, passkeyFailureText } from '@/lib/webauthn'
import { MovingBorderButton } from '../fx/MovingBorderButton'
import { StepHeading } from '../StepFrame'

/**
 * Between the account and the company: an optional offer to add a passkey. It is shown only when the server speaks
 * passkeys and this browser and address can use them, and it never blocks: skipping is always one click, a failed or
 * cancelled attempt just says so and leaves both choices open.
 */
export function PasskeyStep({ onDone }: { onDone: () => void }) {
  const register = useRegisterPasskey()
  const reduced = usePrefersReducedMotion()
  const control = useRef<AbortController | null>(null)
  const [added, setAdded] = useState<PasskeyInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function create() {
    setError(null)
    const ctl = new AbortController()
    control.current = ctl
    try {
      const passkey = await register.mutateAsync({ name: defaultPasskeyName(), signal: ctl.signal })
      if (!ctl.signal.aborted) setAdded(passkey)
    } catch (err) {
      if (ctl.signal.aborted) return
      const f = describePasskeyFailure(err, 'register')
      setError(passkeyFailureText(f, f.retryAfterSeconds ?? 0))
    }
  }

  function skip() {
    control.current?.abort()
    onDone()
  }

  return (
    <motion.div initial={reduced ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduced ? 0 : 0.3, ease: 'easeOut' }}>
      <div className="relative mb-6 flex size-14 items-center justify-center">
        {!reduced && !added && (
          <motion.span
            aria-hidden
            className="absolute inset-0 rounded-full bg-accent-subtle"
            animate={{ scale: [1, 1.35, 1], opacity: [0.7, 0, 0.7] }}
            transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}
          />
        )}
        <span aria-hidden className="relative flex size-14 items-center justify-center rounded-full border border-line-strong bg-surface text-accent-fg">
          {added ? <Check className="size-6" /> : <Fingerprint className="size-6" />}
        </span>
      </div>

      {added ? (
        <div role="status">
          <StepHeading eyebrow="Your account" title="Passkey added">
            From now on you can sign in with {added.name}. You can add more, rename or remove passkeys any time from Account.
          </StepHeading>
          <MovingBorderButton autoFocus onClick={onDone}>
            Continue
            <ArrowRight className="size-4" aria-hidden />
          </MovingBorderButton>
        </div>
      ) : (
        <>
          <StepHeading eyebrow="Your account" title="Add a passkey (recommended)">
            Sign in with your fingerprint, face or device PIN instead of typing a password. A passkey stays on your device, cannot be phished, and counts as two-factor sign-in on its own. Your password keeps working.
          </StepHeading>
          <div className="space-y-4">
            <FormError message={error} />
            <div className="flex flex-wrap items-center gap-3">
              <MovingBorderButton autoFocus onClick={() => void create()} disabled={register.isPending} icon={<Fingerprint className="size-4" aria-hidden />}>
                {register.isPending ? 'Waiting for your device…' : 'Create a passkey'}
              </MovingBorderButton>
              <Button variant="ghost" className="h-10" onClick={skip}>
                Skip for now
              </Button>
            </div>
            <p className="text-[12px] text-fg-3">You can add one later in Account, under Passkeys.</p>
          </div>
        </>
      )}
    </motion.div>
  )
}
