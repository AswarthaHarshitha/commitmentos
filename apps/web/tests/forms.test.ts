import { describe, expect, it } from "vitest";
import { buildPatch, type FormState } from "@/components/edit-obligation-dialog";
import { parseHours } from "@/lib/hours";

describe("parseHours", () => {
  it("reads a comma or space separated list, largest first, without repeats", () => {
    expect(parseHours("6, 24")).toEqual([24, 6]);
    expect(parseHours("24 6 6")).toEqual([24, 6]);
    expect(parseHours("48,24,6,1")).toEqual([48, 24, 6, 1]);
  });

  it("treats an empty field as 'use the default'", () => {
    expect(parseHours("")).toBeNull();
    expect(parseHours("  ,  ")).toBeNull();
  });

  it("rejects what the server would reject, with a message a person can act on", () => {
    expect(() => parseHours("0")).toThrow(/between 1 and 720/);
    expect(() => parseHours("721")).toThrow(/between 1 and 720/);
    expect(() => parseHours("1.5")).toThrow(/whole hours/);
    expect(() => parseHours("-3")).toThrow(/whole hours/);
    expect(() => parseHours("a day")).toThrow(/whole hours/);
    expect(() => parseHours("1 2 3 4 5 6")).toThrow(/at most five/);
  });
});

const base: FormState = { title: "Pay rent", description: "", type: "PAYMENT", priority: "HIGH", date: "2026-10-01", time: "", name: "Landlord", email: "", recurrence: "" };

describe("buildPatch", () => {
  it("sends nothing when nothing changed, so saving cannot overwrite a newer server value", () => {
    expect(buildPatch(base, { ...base })).toEqual({});
  });

  it("sends only the fields that changed", () => {
    expect(buildPatch(base, { ...base, title: "  Pay September rent ", priority: "URGENT" })).toEqual({ title: "Pay September rent", priority: "URGENT" });
  });

  it("sends a new deadline as a local date and time, for the server to read in the user's zone", () => {
    expect(buildPatch(base, { ...base, date: "2026-10-02", time: "17:30" })).toEqual({ due: { date: "2026-10-02", time: "17:30" } });
    expect(buildPatch(base, { ...base, date: "2026-10-05" })).toEqual({ due: { date: "2026-10-05" } });
  });

  it("clears the deadline explicitly when the date is emptied", () => {
    expect(buildPatch(base, { ...base, date: "" })).toEqual({ clear_due: true });
  });

  it("treats adding a time to a date-only deadline as a change", () => {
    expect(buildPatch(base, { ...base, time: "09:00" })).toEqual({ due: { date: "2026-10-01", time: "09:00" } });
  });

  it("clears a repeat with the dedicated flag rather than an empty value", () => {
    const weekly = { ...base, recurrence: "WEEKLY" };
    expect(buildPatch(weekly, { ...weekly, recurrence: "" })).toEqual({ clear_recurrence: true });
    expect(buildPatch(base, { ...base, recurrence: "MONTHLY" })).toEqual({ recurrence: "MONTHLY" });
  });

  it("does not send an emptied name or email, which the server would ignore", () => {
    expect(buildPatch(base, { ...base, name: "" })).toEqual({});
    expect(buildPatch(base, { ...base, email: "  landlord@example.com " })).toEqual({ counterparty_email: "landlord@example.com" });
  });
});
