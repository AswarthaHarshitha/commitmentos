/** "24, 6" -> [24, 6] (largest first); null when empty, meaning "use the default"; throws a readable message for anything else. */
export function parseHours(text: string): number[] | null {
  const parts = text.split(/[,\s]+/).filter(Boolean);
  if (parts.length === 0) return null;
  if (parts.length > 5) throw new Error("Use at most five reminders.");
  const hours = parts.map((p) => (/^\d+$/.test(p) ? Number(p) : NaN));
  if (hours.some((h) => !Number.isInteger(h) || h < 1 || h > 720)) throw new Error("Reminders are whole hours between 1 and 720, for example 24, 6.");
  return [...new Set(hours)].sort((a, b) => b - a);
}
