import type { DuePrecision } from "./api";

export type DueTone = "calm" | "soon" | "overdue" | "done" | "none";
export interface DueInfo {
  /** "Due today", "Due in 3 days", "Overdue by 2 days" */
  label: string;
  /** the concrete moment, in the user's timezone: "5:00 PM", "Mon, Sep 28 · 9:15 AM", "by end of day" */
  detail: string | null;
  tone: DueTone;
}

const MS_MINUTE = 60_000;
const MS_HOUR = 3_600_000;
const MS_DAY = 86_400_000;

interface Parts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  weekday: string;
}

const partsFormatters = new Map<string, Intl.DateTimeFormat>();

/** The wall-clock reading of `date` in an IANA timezone (DST-aware, no library). */
export function localParts(date: Date, timeZone: string): Parts {
  let fmt = partsFormatters.get(timeZone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      weekday: "short",
    });
    partsFormatters.set(timeZone, fmt);
  }
  const out: Record<string, string> = {};
  for (const part of fmt.formatToParts(date)) out[part.type] = part.value;
  return {
    year: Number(out.year),
    month: Number(out.month),
    day: Number(out.day),
    hour: Number(out.hour) % 24,
    minute: Number(out.minute),
    weekday: out.weekday ?? "",
  };
}

/** Calendar-day number (days since 1970-01-01 of the local date), so differences ignore clock times and DST. */
export function localDayNumber(date: Date, timeZone: string): number {
  const p = localParts(date, timeZone);
  return Date.UTC(p.year, p.month - 1, p.day) / MS_DAY;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatTime(date: Date, timeZone: string): string {
  const p = localParts(date, timeZone);
  const h = p.hour % 12 === 0 ? 12 : p.hour % 12;
  return `${h}:${String(p.minute).padStart(2, "0")} ${p.hour < 12 ? "AM" : "PM"}`;
}

/** "Sep 28", or "Sep 28, 2027" when it is not this year (in the user's timezone). */
export function formatDay(date: Date, timeZone: string, now: Date = new Date()): string {
  const p = localParts(date, timeZone);
  const sameYear = p.year === localParts(now, timeZone).year;
  return `${MONTHS[p.month - 1]} ${p.day}${sameYear ? "" : `, ${p.year}`}`;
}

export function formatWeekdayDay(date: Date, timeZone: string, now: Date = new Date()): string {
  return `${localParts(date, timeZone).weekday}, ${formatDay(date, timeZone, now)}`;
}

/** "Fri, Sep 25 at 5:00 PM IST" - the precise form used on the detail page. */
export function formatDateTime(iso: string, timeZone: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const zone = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "short" }).formatToParts(date).find((p) => p.type === "timeZoneName")?.value ?? "";
  return `${formatWeekdayDay(date, timeZone, now)} at ${formatTime(date, timeZone)}${zone ? ` ${zone}` : ""}`;
}

const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;

export function describeDeadline(input: {
  dueAt: string | null;
  precision: DuePrecision | null;
  timeZone: string;
  now: Date;
}): DueInfo {
  const { dueAt, precision, timeZone, now } = input;
  if (!dueAt) return { label: "No deadline", detail: null, tone: "none" };
  const due = new Date(dueAt);
  const dateOnly = precision === "DATE";

  if (now.getTime() > due.getTime()) {
    const late = now.getTime() - due.getTime();
    let label: string;
    if (dateOnly) {
      const days = Math.max(1, localDayNumber(now, timeZone) - localDayNumber(due, timeZone));
      label = `Overdue by ${plural(days, "day")}`;
    } else if (late < MS_HOUR) label = `Overdue by ${plural(Math.max(1, Math.floor(late / MS_MINUTE)), "minute")}`;
    else if (late < MS_DAY) label = `Overdue by ${plural(Math.floor(late / MS_HOUR), "hour")}`;
    else label = `Overdue by ${plural(Math.floor(late / MS_DAY), "day")}`;
    return { label, detail: dateOnly ? `was due ${formatWeekdayDay(due, timeZone, now)}` : `was due ${formatWeekdayDay(due, timeZone, now)} · ${formatTime(due, timeZone)}`, tone: "overdue" };
  }

  const days = localDayNumber(due, timeZone) - localDayNumber(now, timeZone);
  const within24h = due.getTime() - now.getTime() <= MS_DAY;
  const time = dateOnly ? "by end of day" : formatTime(due, timeZone);
  if (days === 0) return { label: "Due today", detail: time, tone: "soon" };
  if (days === 1) return { label: "Due tomorrow", detail: time, tone: within24h ? "soon" : "calm" };
  if (days < 7) return { label: `Due in ${days} days`, detail: `${formatWeekdayDay(due, timeZone, now)}${dateOnly ? "" : ` · ${time}`}`, tone: "calm" };
  return { label: `Due ${formatDay(due, timeZone, now)}`, detail: `${localParts(due, timeZone).weekday}${dateOnly ? "" : ` · ${time}`}`, tone: "calm" };
}

/** "just now", "12 min ago", "3 hours ago", "yesterday", "Sep 21" - for feeds. */
export function timeAgo(iso: string, now: Date, timeZone: string): string {
  const then = new Date(iso);
  const diff = now.getTime() - then.getTime();
  if (diff < 0) return `in ${formatDuration(-diff)}`;
  if (diff < MS_MINUTE) return "just now";
  if (diff < MS_HOUR) return `${Math.floor(diff / MS_MINUTE)} min ago`;
  if (diff < MS_DAY) return `${plural(Math.floor(diff / MS_HOUR), "hour")} ago`;
  const days = localDayNumber(now, timeZone) - localDayNumber(then, timeZone);
  if (days === 1) return "yesterday";
  if (days < 7) return `${plural(days, "day")} ago`;
  return formatDay(then, timeZone, now);
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  if (ms < MS_MINUTE) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  if (ms < MS_HOUR) return `${Math.round(ms / MS_MINUTE)} min`;
  if (ms < MS_DAY) return `${Math.round(ms / MS_HOUR)} h`;
  return `${Math.round(ms / MS_DAY)} d`;
}

/** Greeting for the current part of the day (the server decides the part, so the greeting agrees with the clock the deadline rules use). */
export function greeting(part: "morning" | "afternoon" | "evening"): string {
  return `Good ${part}`;
}

// ------------------------------------------------------------------------------------------------ editing

const pad2 = (n: number) => String(n).padStart(2, "0");

/** The values for `<input type="date">` and `<input type="time">` that show `iso` on the wall clock of `timeZone`. */
export function toLocalInputs(iso: string, timeZone: string): { date: string; time: string } {
  const p = localParts(new Date(iso), timeZone);
  return { date: `${p.year}-${pad2(p.month)}-${pad2(p.day)}`, time: `${pad2(p.hour)}:${pad2(p.minute)}` };
}

/**
 * The instant at which the wall clock in `timeZone` reads `date` `time` ("2026-10-02", "17:00"). Around a DST change an ambiguous
 * reading (fall back) takes the earlier instant, and one that does not exist (spring forward) moves forward by the gap, so 02:30
 * becomes 03:30 - the same choices `Date` makes for local times.
 */
export function zonedTimeToUtc(date: string, time: string, timeZone: string): Date {
  const [y = 1970, m = 1, d = 1] = date.split("-").map(Number);
  const [hh = 0, mm = 0] = time.split(":").map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  // what the wall clock reads at `instant`, minus the instant: the zone's offset then
  const offsetAt = (instant: number) => {
    const p = localParts(new Date(instant), timeZone);
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - Math.floor(instant / MS_MINUTE) * MS_MINUTE;
  };
  const first = wall - offsetAt(wall);
  const second = wall - offsetAt(first);
  const candidates = [first, second].sort((a, b) => a - b);
  const exact = candidates.find((c) => Math.floor(c / MS_MINUTE) * MS_MINUTE + offsetAt(c) === wall);
  return new Date(exact ?? candidates[1] ?? first);
}

/** "Today", "Tomorrow", "Sunday, Sep 27": how a list of days is headed. */
export function dayHeading(date: Date, timeZone: string, now: Date): string {
  const days = localDayNumber(date, timeZone) - localDayNumber(now, timeZone);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone }).format(date);
  return `${weekday}, ${formatDay(date, timeZone, now)}`;
}

/** The calendar date ("2026-09-26") `days` after today's date in `timeZone` - counted on the calendar, so a 23- or 25-hour day cannot skip or repeat one. */
export function addLocalDays(now: Date, timeZone: string, days: number): string {
  const p = localParts(now, timeZone);
  return new Date(Date.UTC(p.year, p.month - 1, p.day + days)).toISOString().slice(0, 10);
}

// ------------------------------------------------------------------------------------------------ lists and feeds

/** "Thu, Sep 24 · 3:20 PM": a moment inside a list, without repeating the time zone on every line (the page says which zone it is). */
export function formatMoment(when: string | Date, timeZone: string, now: Date): string {
  const date = typeof when === "string" ? new Date(when) : when;
  return `${formatWeekdayDay(date, timeZone, now)} · ${formatTime(date, timeZone)}`;
}

/** "Fri, Oct 2 · 3:00 – 4:00 PM" for an event within one day (the shared AM/PM said once), or both ends written out. */
export function formatRange(startIso: string, endIso: string, timeZone: string, now: Date): string {
  const start = new Date(startIso);
  const end = new Date(endIso);
  if (localDayNumber(start, timeZone) !== localDayNumber(end, timeZone)) return `${formatMoment(start, timeZone, now)} – ${formatMoment(end, timeZone, now)}`;
  const from = formatTime(start, timeZone);
  const to = formatTime(end, timeZone);
  const shared = from.slice(-2) === to.slice(-2);
  return `${formatWeekdayDay(start, timeZone, now)} · ${shared ? from.slice(0, -3) : from} – ${to}`;
}
