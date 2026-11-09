// Import compatibility (GL-133): how a mapping, or a file's column names, fit a
// schema. The mapping step recomputes this from local state on every dropdown
// change; the API enforces the same hard rules on preview-with-mapping and commit.

export interface CompatColumn {
  key: string
  label: string
  required?: boolean
  type?: string
  archived?: boolean
}

export interface Compat {
  matched: number
  total: number
  ignored: string[]
  unfilled: CompatColumn[]
  requiredMissing: CompatColumn[]
}

export function mappingCompat(
  sourceColumns: string[],
  mapping: Record<string, string>,
  columns: CompatColumn[],
  { skipJson = false }: { skipJson?: boolean } = {},
): Compat {
  const used = new Set(Object.values(mapping))
  const unfilled = columns.filter((c) => !c.archived && !used.has(c.key) && !(skipJson && c.type === 'json'))
  return {
    matched: sourceColumns.filter((c) => mapping[c] !== undefined).length,
    total: sourceColumns.length,
    ignored: sourceColumns.filter((c) => mapping[c] === undefined),
    unfilled,
    requiredMissing: unfilled.filter((c) => c.required),
  }
}

/** Compatibility of a file's column names with a schema, before a dataset exists.
 * Same matching rule as the import (trimmed, case-insensitive key), plus the
 * importer's handling of nested files: a `json` column keeps its dotted leaves,
 * so `meta.a` belongs to the json column `meta`. A hint only: inferred names
 * can differ from what the import will see. */
export function fileCompat(fileColumns: string[], columns: CompatColumn[]): Compat {
  const active = columns.filter((c) => !c.archived)
  const mapping: Record<string, string> = {}
  const taken = new Set<string>()
  for (const name of fileColumns) {
    const lower = name.trim().toLowerCase()
    const exact = active.find((c) => c.key.toLowerCase() === lower && !taken.has(c.key))
    const leaf = active.find((c) => c.type === 'json' && lower.startsWith(`${c.key.toLowerCase()}.`))
    const hit = exact ?? leaf
    if (hit) {
      mapping[name] = hit.key
      if (hit === exact) taken.add(hit.key)
    }
  }
  return mappingCompat(fileColumns, mapping, active, { skipJson: true })
}

export function labelList(columns: CompatColumn[]): string {
  return columns.map((c) => c.label || c.key).join(', ')
}
