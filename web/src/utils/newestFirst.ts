type Stamped = { createdAt?: string | null };

const time = (v?: string | null) => {
  const t = v ? Date.parse(v) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
};

/**
 * Lists show the latest entry on top: newest first by `at` (the record's
 * creation time by default). Records without a time go last; ties keep
 * their order. A column sort the user clicks still overrides this.
 */
export function newestFirst<T>(rows: readonly T[], at: (r: T) => string | null | undefined = (r) => (r as Stamped).createdAt): T[] {
  return [...rows].sort((a, b) => time(at(b)) - time(at(a)));
}
