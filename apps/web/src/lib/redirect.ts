/**
 * Where to send someone after signing in. Only a path inside this site is accepted: anything that could leave it
 * ("//evil.example", "https://evil.example", "/\evil.example", javascript:) falls back to the overview, so a crafted
 * login link cannot bounce a signed-in person to another site.
 */
export function safeNext(next: string | null | undefined, fallback = "/overview"): string {
  if (!next) return fallback;
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return fallback;
  // control characters (tab, newline) are stripped by browsers before parsing, so "/\t/evil.example" would become "//evil.example"
  if ([...next].some((c) => c.charCodeAt(0) < 0x20 || c.charCodeAt(0) === 0x7f)) return fallback;
  let url: URL;
  try {
    url = new URL(next, "https://placeholder.invalid");
  } catch {
    return fallback;
  }
  if (url.origin !== "https://placeholder.invalid") return fallback;
  if (url.pathname === "/login" || url.pathname === "/register") return fallback;
  return `${url.pathname}${url.search}${url.hash}`;
}
