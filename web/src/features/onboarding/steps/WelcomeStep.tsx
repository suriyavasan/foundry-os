import { ArrowRight } from 'lucide-react'
import { MovingBorderButton } from '../fx/MovingBorderButton'
import { TextGenerate } from '../fx/TextGenerate'

/** Step 1: one sentence and one button. */
export function WelcomeStep({ onStart }: { onStart: () => void }) {
  return (
    <div className="text-center">
      <p className="mb-4 text-[11.5px] font-medium tracking-[0.18em] text-accent-fg uppercase">Company OS</p>
      <TextGenerate text="A company that runs itself." className="text-[40px] leading-[1.1] font-semibold tracking-tight text-fg sm:text-[52px]" />
      <p className="mx-auto mt-5 max-w-md text-[15px] leading-relaxed text-fg-2">
        You are the Founder. Your AI Co-founder plans, hires and manages a team of AI employees, and reports to you. Setting up takes about two minutes.
      </p>
      <div className="mt-9 flex justify-center">
        <MovingBorderButton autoFocus onClick={onStart} icon={undefined}>
          Start
          <ArrowRight className="size-4" aria-hidden />
        </MovingBorderButton>
      </div>
    </div>
  )
}
