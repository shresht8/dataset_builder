// Names of datasets, collections, folders and store schemas (GL-143). The API's
// DATASET_NAME_PATTERN (backend/groundline_api/schemas/dataset.py) stays the
// hard check; this one lets the UI say what's wrong while the user types.
const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,99}$/

export const NAME_RULE = 'Use lowercase letters, digits, - and _, starting with a letter or digit (at most 100).'

/** Why `name` isn't a valid name, or null when it is. An empty name is left to the disabled button. */
export function nameProblem(name: string): string | null {
  if (name === '' || NAME_PATTERN.test(name)) return null
  return NAME_RULE
}

/** `name` itself when it's free, else the first free `name-2`, `name-3`, ... */
export function freeName(name: string, taken: Set<string>): string {
  if (!taken.has(name)) return name
  for (let n = 2; ; n += 1) {
    const suffix = `-${n}`
    const candidate = `${name.slice(0, 100 - suffix.length)}${suffix}`
    if (!taken.has(candidate)) return candidate
  }
}
