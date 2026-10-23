/**
 * Heuristic mirror of the server rule (backend/app/services/agents.py, SECRET_WORDS):
 * a runtime_config key is secret-looking if any of its words — split on `_ - .`, spaces
 * and camelCase — is a secret word. Word-based so `max_tokens` or `keyboard` pass.
 * The server is the authority; this only gives earlier feedback.
 */
const SECRET_WORDS = new Set([
  'key',
  'apikey',
  'privatekey',
  'secret',
  'secrets',
  'token',
  'password',
  'passwd',
  'pwd',
  'passphrase',
  'credential',
  'credentials',
  'bearer',
  'cookie',
  'authorization',
])

export function looksLikeSecretKey(key: string): boolean {
  return key
    .split(/[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])/)
    .filter(Boolean)
    .some((w) => SECRET_WORDS.has(w.toLowerCase()))
}
