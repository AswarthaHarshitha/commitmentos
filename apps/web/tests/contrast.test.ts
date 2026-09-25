import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// The design tokens live in one stylesheet; this test reads them from there so a palette change cannot silently
// break readability. WCAG 2.x relative luminance / contrast ratio.
const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8"); // vitest runs from apps/web
const token = (name: string): string => {
  const match = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match?.[1]) throw new Error(`token --color-${name} not found`);
  return match[1];
};

function luminance(hex: string): number {
  const channel = (i: number) => {
    const v = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}
const ratio = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
};

describe("colour tokens meet WCAG AA (4.5:1) wherever text is set on them", () => {
  const surfaces = ["bg", "surface", "surface-2"];
  it.each(surfaces.flatMap((s) => ["ink", "text", "text-2", "text-3"].map((t) => [t, s])))("%s on %s", (fg, bg) => {
    expect(ratio(token(fg!), token(bg!))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(["ok", "warn", "danger", "info"])("%s text on its own tint", (tone) => {
    expect(ratio(token(tone), token(`${tone}-bg`))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(["ok", "warn", "danger", "info"])("%s text on the plain surface and the page background", (tone) => {
    expect(ratio(token(tone), token("surface"))).toBeGreaterThanOrEqual(4.5);
    expect(ratio(token(tone), token("bg"))).toBeGreaterThanOrEqual(4.5);
  });

  it("primary buttons (ivory text on ink) clear AAA", () => {
    expect(ratio(token("surface"), token("ink"))).toBeGreaterThanOrEqual(7);
  });

  it("hairlines are visible against both surfaces without being heavy", () => {
    expect(ratio(token("line-strong"), token("surface"))).toBeGreaterThan(1.3);
    expect(ratio(token("line"), token("surface"))).toBeGreaterThan(1.1);
  });
});
