import { describe, expect, it } from "vitest";
import { addLocalDays, dayHeading, describeDeadline, formatDateTime, formatDuration, formatMoment, formatRange, localDayNumber, localParts, timeAgo, toLocalInputs, zonedTimeToUtc } from "@/lib/time";

const IST = "Asia/Kolkata";
const NY = "America/New_York";
const at = (iso: string) => new Date(iso);

describe("localParts", () => {
  it("reads the wall clock of a timezone, DST-aware", () => {
    expect(localParts(at("2026-09-25T06:30:00Z"), IST)).toMatchObject({ year: 2026, month: 9, day: 25, hour: 12, minute: 0 });
    expect(localParts(at("2026-07-01T12:00:00Z"), NY).hour).toBe(8); // EDT
    expect(localParts(at("2026-12-01T12:00:00Z"), NY).hour).toBe(7); // EST
  });

  it("treats midnight as hour 0, never 24", () => {
    expect(localParts(at("2026-09-24T18:30:00Z"), IST)).toMatchObject({ day: 25, hour: 0, minute: 0 });
  });
});

describe("describeDeadline", () => {
  // Fri 2026-09-25 12:00 in Kolkata
  const now = at("2026-09-25T06:30:00Z");
  const dl = (dueAt: string | null, precision: "DATE" | "DATETIME" | null, tz = IST, n = now) => describeDeadline({ dueAt, precision, timeZone: tz, now: n });

  it("says so when there is no deadline", () => {
    expect(dl(null, null)).toEqual({ label: "No deadline", detail: null, tone: "none" });
  });

  it("due later today is 'Due today' with the time, and is soon", () => {
    expect(dl("2026-09-25T11:30:00Z", "DATETIME")).toEqual({ label: "Due today", detail: "5:00 PM", tone: "soon" });
  });

  it("a date-only deadline today reads 'by end of day'", () => {
    expect(dl("2026-09-25T18:29:59Z", "DATE")).toEqual({ label: "Due today", detail: "by end of day", tone: "soon" });
  });

  it("tomorrow within 24 hours is soon; tomorrow beyond 24 hours is calm", () => {
    expect(dl("2026-09-26T03:30:00Z", "DATETIME")).toMatchObject({ label: "Due tomorrow", detail: "9:00 AM", tone: "soon" });
    expect(dl("2026-09-26T11:30:00Z", "DATETIME")).toMatchObject({ label: "Due tomorrow", detail: "5:00 PM", tone: "calm" });
  });

  it("a few days out counts days and shows the weekday", () => {
    expect(dl("2026-09-28T03:45:00Z", "DATETIME")).toEqual({ label: "Due in 3 days", detail: "Mon, Sep 28 · 9:15 AM", tone: "calm" });
    expect(dl("2026-09-28T18:29:59Z", "DATE")).toEqual({ label: "Due in 3 days", detail: "Mon, Sep 28", tone: "calm" });
  });

  it("a week or more out shows the date", () => {
    expect(dl("2026-10-15T18:29:59Z", "DATE")).toEqual({ label: "Due Oct 15", detail: "Thu", tone: "calm" });
    expect(dl("2027-01-05T06:30:00Z", "DATETIME").label).toBe("Due Jan 5, 2027");
  });

  it("overdue counts in the unit that fits, and is the only red", () => {
    expect(dl("2026-09-25T06:00:00Z", "DATETIME")).toMatchObject({ label: "Overdue by 30 minutes", tone: "overdue" });
    expect(dl("2026-09-25T05:29:59Z", "DATETIME").label).toBe("Overdue by 1 hour");
    expect(dl("2026-09-24T18:29:59Z", "DATE")).toMatchObject({ label: "Overdue by 1 day", detail: "was due Thu, Sep 24", tone: "overdue" });
    expect(dl("2026-09-22T18:29:59Z", "DATE").label).toBe("Overdue by 3 days");
    expect(dl("2026-09-23T06:00:00Z", "DATETIME").label).toBe("Overdue by 2 days");
  });

  it("uses the user's calendar days, not 24-hour blocks, across a DST change", () => {
    // New York: clocks go back on Sun 2026-11-01. Sat 22:00 EDT -> Mon 08:00 EST is two calendar days.
    const n = at("2026-10-31T22:00:00-04:00");
    expect(dl("2026-11-02T13:00:00Z", "DATETIME", NY, n)).toMatchObject({ label: "Due in 2 days", detail: "Mon, Nov 2 · 8:00 AM" });
  });

  it("the same instant is 'today' in one timezone and 'tomorrow' in another", () => {
    const n = at("2026-09-25T20:00:00Z"); // 01:30 Sat in Kolkata, 16:00 Fri in New York
    const due = "2026-09-26T05:00:00Z";
    expect(dl(due, "DATETIME", IST, n).label).toBe("Due today");
    expect(dl(due, "DATETIME", NY, n).label).toBe("Due tomorrow");
  });
});

describe("timeAgo", () => {
  const now = at("2026-09-25T06:30:00Z");
  it.each([
    ["2026-09-25T06:29:40Z", "just now"],
    ["2026-09-25T06:18:00Z", "12 min ago"],
    ["2026-09-25T03:30:00Z", "3 hours ago"],
    ["2026-09-24T03:00:00Z", "yesterday"],
    ["2026-09-21T09:00:00Z", "4 days ago"],
    ["2026-09-10T09:00:00Z", "Sep 10"],
    ["2026-09-25T08:30:00Z", "in 2 h"],
  ])("%s -> %s", (iso, expected) => expect(timeAgo(iso, now, IST)).toBe(expected));
});

describe("formatting", () => {
  it("formats the precise moment with a zone", () => {
    expect(formatDateTime("2026-09-25T11:30:00Z", IST, at("2026-09-25T00:00:00Z"))).toBe("Fri, Sep 25 at 5:00 PM GMT+5:30");
  });
  it("formats durations", () => {
    expect([formatDuration(240), formatDuration(2300), formatDuration(41_000), formatDuration(190_000)]).toEqual(["240 ms", "2.3 s", "41 s", "3 min"]);
  });
  it("counts local days", () => {
    expect(localDayNumber(at("2026-09-25T18:29:59Z"), IST) - localDayNumber(at("2026-09-25T18:30:00Z"), IST)).toBe(-1);
  });
});

describe("editing helpers", () => {
  it("shows an instant on the wall clock of the user's zone", () => {
    expect(toLocalInputs("2026-09-25T11:30:00Z", "Asia/Kolkata")).toEqual({ date: "2026-09-25", time: "17:00" });
    expect(toLocalInputs("2026-09-25T02:00:00Z", "America/Los_Angeles")).toEqual({ date: "2026-09-24", time: "19:00" });
  });

  it("converts a wall-clock reading back to the same instant", () => {
    expect(zonedTimeToUtc("2026-09-25", "17:00", "Asia/Kolkata").toISOString()).toBe("2026-09-25T11:30:00.000Z");
    expect(zonedTimeToUtc("2026-09-24", "19:00", "America/Los_Angeles").toISOString()).toBe("2026-09-25T02:00:00.000Z");
  });

  it("round-trips through the inputs for instants either side of a DST change", () => {
    for (const iso of ["2026-03-07T20:00:00Z", "2026-03-08T20:00:00Z", "2026-10-31T20:00:00Z", "2026-11-01T20:00:00Z"]) {
      const { date, time } = toLocalInputs(iso, "America/New_York");
      expect(zonedTimeToUtc(date, time, "America/New_York").toISOString()).toBe(new Date(iso).toISOString());
    }
  });

  it("uses the offset in force on that day, not today's", () => {
    // New York is UTC-5 in January and UTC-4 in July: the same 09:00 reading is a different instant
    expect(zonedTimeToUtc("2027-01-15", "09:00", "America/New_York").toISOString()).toBe("2027-01-15T14:00:00.000Z");
    expect(zonedTimeToUtc("2027-07-15", "09:00", "America/New_York").toISOString()).toBe("2027-07-15T13:00:00.000Z");
  });

  it("moves a time inside the spring-forward gap forward by the gap, as Date does", () => {
    // 02:30 on 2027-03-14 does not exist in New York (clocks jump 02:00 -> 03:00)
    const resolved = zonedTimeToUtc("2027-03-14", "02:30", NY);
    expect(toLocalInputs(resolved.toISOString(), NY)).toEqual({ date: "2027-03-14", time: "03:30" });
    expect(resolved.toISOString()).toBe("2027-03-14T07:30:00.000Z"); // 03:30 EDT
  });

  it("takes the first of two readings when the clock falls back", () => {
    // 01:30 on 2027-11-07 happens twice in New York: 01:30 EDT (05:30Z) and 01:30 EST (06:30Z)
    expect(zonedTimeToUtc("2027-11-07", "01:30", NY).toISOString()).toBe("2027-11-07T05:30:00.000Z");
  });

  it("headings for lists of days", () => {
    const now = new Date("2026-09-25T07:00:00Z"); // Fri 12:30 in Kolkata
    expect(dayHeading(new Date("2026-09-25T15:00:00Z"), "Asia/Kolkata", now)).toBe("Today");
    expect(dayHeading(new Date("2026-09-26T06:00:00Z"), "Asia/Kolkata", now)).toBe("Tomorrow");
    expect(dayHeading(new Date("2026-09-24T06:00:00Z"), "Asia/Kolkata", now)).toBe("Yesterday");
    expect(dayHeading(new Date("2026-09-27T06:00:00Z"), "Asia/Kolkata", now)).toBe("Sunday, Sep 27");
  });

  it("changes the heading when the local date changes, not when 24 hours pass", () => {
    // 20:30 UTC on the 25th is already the 26th in Kolkata: "Tomorrow" for a user at 12:30 IST on the 25th
    const now = new Date("2026-09-25T07:00:00Z");
    expect(dayHeading(new Date("2026-09-25T20:30:00Z"), "Asia/Kolkata", now)).toBe("Tomorrow");
  });
});

describe("addLocalDays", () => {
  it("counts calendar days in the user's zone", () => {
    // 19:00 UTC on the 25th is 00:30 on the 26th in Kolkata, so "tomorrow" there is the 27th
    expect(addLocalDays(new Date("2026-09-25T19:00:00Z"), IST, 1)).toBe("2026-09-27");
    expect(addLocalDays(new Date("2026-09-25T18:00:00Z"), IST, 1)).toBe("2026-09-26"); // 23:30 on the 25th
    expect(addLocalDays(new Date("2026-09-25T07:00:00Z"), IST, 1)).toBe("2026-09-26");
  });

  it("does not skip or repeat a day across a DST change", () => {
    // New York springs forward on 2027-03-14 (a 23-hour day); 22:30 local on the 13th + 1 day is the 14th, + 2 is the 15th
    const evening = zonedTimeToUtc("2027-03-13", "22:30", NY);
    expect(addLocalDays(evening, NY, 1)).toBe("2027-03-14");
    expect(addLocalDays(evening, NY, 2)).toBe("2027-03-15");
    expect(addLocalDays(evening, NY, 7)).toBe("2027-03-20");
  });

  it("rolls over month and year ends", () => {
    expect(addLocalDays(new Date("2026-12-31T12:00:00Z"), "UTC", 1)).toBe("2027-01-01");
    expect(addLocalDays(new Date("2028-02-28T12:00:00Z"), "UTC", 1)).toBe("2028-02-29");
  });
});

describe("moments and ranges in lists", () => {
  const now = new Date("2026-09-25T07:00:00Z");

  it("writes a moment without the zone", () => {
    expect(formatMoment("2026-09-24T09:50:00Z", IST, now)).toBe("Thu, Sep 24 · 3:20 PM");
    expect(formatMoment(new Date("2026-09-24T09:50:00Z"), NY, now)).toBe("Thu, Sep 24 · 5:50 AM");
  });

  it("says the shared AM/PM once", () => {
    expect(formatRange("2026-10-02T09:30:00Z", "2026-10-02T10:30:00Z", IST, now)).toBe("Fri, Oct 2 · 3:00 – 4:00 PM");
  });

  it("keeps both when the event crosses noon", () => {
    expect(formatRange("2026-10-02T05:30:00Z", "2026-10-02T07:30:00Z", IST, now)).toBe("Fri, Oct 2 · 11:00 AM – 1:00 PM");
  });

  it("writes both ends in full when it spans days", () => {
    expect(formatRange("2026-10-02T15:00:00Z", "2026-10-03T03:00:00Z", NY, now)).toBe("Fri, Oct 2 · 11:00 AM – 11:00 PM"); // still one day in New York
    expect(formatRange("2026-10-02T20:00:00Z", "2026-10-03T06:00:00Z", NY, now)).toBe("Fri, Oct 2 · 4:00 PM – Sat, Oct 3 · 2:00 AM");
  });

  it("uses the reader's day, not UTC's, to decide whether an event spans days", () => {
    // 22:00-23:30 UTC is 03:30-05:00 the next morning in Kolkata: still one local day
    expect(formatRange("2026-10-02T22:00:00Z", "2026-10-02T23:30:00Z", IST, now)).toBe("Sat, Oct 3 · 3:30 – 5:00 AM");
  });
});
