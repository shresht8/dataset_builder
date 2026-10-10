// Helpers for `json` column values (GL-3.5-14): a one-line grid summary, the
// pretty form the drawer edits, and parsing with a line/column on error.

const MAX_KEYS_SHOWN = 4

/** One-line summary for a grid cell — never raw multi-line JSON.
 * Arrays of objects with a `name` list the names (`f, g`); other arrays show
 * their length and first item (`[3] "a"…`); objects their keys. */
export function summarizeJson(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]'
    const names = value.map((item) =>
      item && typeof item === 'object' && !Array.isArray(item) && typeof (item as Record<string, unknown>).name === 'string'
        ? ((item as Record<string, unknown>).name as string)
        : null,
    )
    if (names.every((name) => name !== null)) return names.join(', ')
    return `[${value.length}] ${summarizeJson(value[0])}${value.length > 1 ? '…' : ''}`
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    if (keys.length === 0) return '{}'
    const shown = keys.slice(0, MAX_KEYS_SHOWN).join(', ')
    return `{${shown}${keys.length > MAX_KEYS_SHOWN ? ', …' : ''}}`
  }
  return JSON.stringify(value)
}

/** The drawer's editable form: pretty-printed, 2-space indent. */
export function formatJson(value: unknown): string {
  return value === undefined ? '' : JSON.stringify(value, null, 2)
}

export type ParsedJson = { ok: true; value: unknown } | { ok: false; error: string }

/** Parse editor text. An empty editor means "no value" (null). */
export function parseJsonText(text: string): ParsedJson {
  if (text.trim() === '') return { ok: true, value: null }
  try {
    return { ok: true, value: JSON.parse(text) }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return { ok: false, error: withLineColumn(text, message) }
  }
}

// Browsers report JSON errors differently: Chrome gives "at position N"
// (and lately also "(line L column C)"), Firefox "at line L column C".
function withLineColumn(text: string, message: string): string {
  if (/line \d+ column \d+/.test(message)) return message
  const position = /position (\d+)/.exec(message)
  if (!position) return message
  const before = text.slice(0, Number(position[1]))
  const line = before.split('\n').length
  const column = before.length - before.lastIndexOf('\n')
  return `${message} (line ${line}, column ${column})`
}
