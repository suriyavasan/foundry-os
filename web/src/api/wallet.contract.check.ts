/**
 * Compile-time drift check between the hand-written M15 client types (wallet.ts) and the backend's OpenAPI schema
 * (schema.d.ts, `pnpm gen:api`). If the backend contract changes, `pnpm typecheck` fails here. No runtime code.
 */
import type { components } from './schema'
import type * as W from './wallet'

type S = components['schemas']

/** The server shape must satisfy what the client reads. */
type Accepts<Client, Server> = [Server] extends [Client] ? true : false
/** Request bodies: what the client sends must be accepted by the server schema. */
type Sends<Server, Client> = [Client] extends [Server] ? true : false

function check<_ extends true>(): void {}

check<Accepts<W.WalletOverview, S['WalletOverview']>>()
check<Accepts<W.CardAccount, S['CardAccountOut']>>()
check<Accepts<W.CardAccountCheck, S['CardAccountCheck']>>()
check<Accepts<W.FundingInstructions, S['FundingInstructions']>>()
check<Accepts<W.CardProviderList, S['CardProviderList']>>()
check<Accepts<W.AgentCard, S['AgentCardOut']>>()
check<Accepts<W.CardReveal, S['CardReveal']>>()
check<Accepts<W.CardTransaction, S['CardTransactionOut']>>()
check<Accepts<W.PurchaseRequest, S['PurchaseRequestOut']>>()
check<Accepts<W.PurchaseRequestDetail, S['PurchaseRequestDetail']>>()
check<Accepts<W.Subscription, S['SubscriptionOut']>>()
check<Accepts<W.WalletSyncResult, S['WalletSyncResult']>>()

check<Sends<S['CardAccountCreate'], W.CardAccountCreate>>()
check<Sends<S['PurchaseMarkPaid'], W.PurchaseMarkPaid>>()
