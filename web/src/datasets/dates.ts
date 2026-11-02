// `date` column values (GL-3.5-10): calendar dates stored as `YYYY-MM-DD`.
// They are always handled as strings -- never through `new Date("YYYY-MM-DD")`,
// which JS parses as UTC midnight and so shows the previous day west of UTC.

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/** A real calendar date in `YYYY-MM-DD` form (years 0001-9999), as the API requires. */
export function isDate(text: string): boolean {
  const match = DATE.exec(text)
  if (!match) return false
  const [year, month, day] = match.slice(1).map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  // Day 0 of the next month is the last day of this one (UTC, so no shifting).
  return day <= new Date(Date.UTC(year, month, 0)).getUTCDate()
}

export const DATE_HINT = 'Enter a date as YYYY-MM-DD'
