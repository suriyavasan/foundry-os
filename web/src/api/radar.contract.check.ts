/**
 * Compile-time drift check between the hand-written M16 client types (radar.ts) and the backend's OpenAPI schema
 * (schema.d.ts, `pnpm gen:api`). If the backend contract changes, `pnpm typecheck` fails here. No runtime code.
 */
import type * as R from './radar'
import type { components } from './schema'

type S = components['schemas']

/** The server shape must satisfy what the client reads. */
type Accepts<Client, Server> = [Server] extends [Client] ? true : false
/** Request bodies: what the client sends must be accepted by the server schema. */
type Sends<Server, Client> = [Client] extends [Server] ? true : false

function check<_ extends true>(): void {}

check<Accepts<R.RadarStatus, S['RadarStatus']>>()
check<Accepts<R.RadarSource, S['RadarSource']>>()
check<Accepts<R.RadarKeyword, S['RadarKeyword']>>()
check<Accepts<R.RadarItem, S['RadarItem']>>()
check<Accepts<R.RadarBrief, S['RadarBrief']>>()
check<Accepts<R.RadarBriefDetail, S['RadarBriefDetail']>>()
check<Accepts<R.RadarIdea, S['RadarIdea']>>()
check<Accepts<R.RadarProbeResult, S['RadarProbeResult']>>()
check<Accepts<R.RadarFetchNowResult, S['RadarFetchNowResult']>>()

check<Sends<S['RadarSourceCreate'], R.RadarSourceCreate>>()
check<Sends<S['RadarSourceUpdate'], R.RadarSourceUpdate>>()
check<Sends<S['RadarKeywordCreate'], R.RadarKeywordCreate>>()
check<Sends<S['RadarKeywordUpdate'], R.RadarKeywordUpdate>>()
check<Sends<S['RadarIdeaDecision'], R.IdeaDecision>>()
