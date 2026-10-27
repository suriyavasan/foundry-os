import { usePurchaseForTask } from '@/api/wallet'
import { CheckoutPacket } from './CheckoutPacket'

/**
 * The checkout packet of a task, when the task is the human checkout task of a purchase request (the request's
 * `checkout_task_id`). Renders nothing for any other task and on a backend without purchasing.
 */
export function TaskCheckout({ companyId, taskId }: { companyId: string; taskId: string }) {
  const { purchase } = usePurchaseForTask(companyId, taskId)
  if (!purchase) return null
  return <CheckoutPacket companyId={companyId} purchase={purchase} />
}
