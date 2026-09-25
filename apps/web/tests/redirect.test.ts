import { describe, expect, it } from "vitest";
import { safeNext } from "@/lib/redirect";

describe("safeNext", () => {
  it("keeps a path inside the site, with its query and hash", () => {
    expect(safeNext("/inbox")).toBe("/inbox");
    expect(safeNext("/inbox?tab=approvals")).toBe("/inbox?tab=approvals");
    expect(safeNext("/obligations/84ba4035#timeline")).toBe("/obligations/84ba4035#timeline");
  });

  it("falls back when there is nothing to follow", () => {
    expect(safeNext(null)).toBe("/overview");
    expect(safeNext(undefined)).toBe("/overview");
    expect(safeNext("")).toBe("/overview");
    expect(safeNext("", "/today")).toBe("/today");
  });

  it.each([
    ["absolute url", "https://evil.example/phish"],
    ["scheme-relative", "//evil.example"],
    ["backslash trick", "/\\evil.example"],
    ["tab inside the slashes", "/\t/evil.example"],
    ["newline before the host", "/\n/evil.example"],
    ["javascript url", "javascript:alert(1)"],
    ["data url", "data:text/html,<script>1</script>"],
    ["relative without a slash", "inbox"],
    ["plain host", "evil.example"],
  ])("refuses %s", (_name, value) => {
    expect(safeNext(value)).toBe("/overview");
  });

  it("does not loop back into the sign-in pages", () => {
    expect(safeNext("/login")).toBe("/overview");
    expect(safeNext("/login?next=/inbox")).toBe("/overview");
    expect(safeNext("/register")).toBe("/overview");
  });
});
