// Shared helpers, inlined into every Code node by build.js (a Code node cannot import local files).
function truncate(value, max) {
  const s = String(value == null ? '' : value);
  return s.length > max ? `${s.slice(0, Math.max(0, max - 1))}…` : s;
}

// Error text is stored and shown to people: never let a token, key or password ride along in it.
function redact(text) {
  // A key must stand alone (or be a known compound such as client_secret): an error CODE like INVALID_WEBHOOK_SECRET is not a secret.
  const key = '(?<![A-Za-z0-9_])(?:api[_-]?key|x-api-key|client[_-]?secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|private[_-]?key|token|secret|password|passwd|authorization|x-webhook-secret|x-commitmentos-key)';
  return String(text == null ? '' : text)
    .replace(/(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi, '$1 [redacted]')
    .replace(new RegExp(`(${key}["']?\\s*[:=]\\s*["']?)[^\\s"',;]+`, 'gi'), '$1[redacted]')
    .replace(/(AIza|sk-|xox[abp]-)[A-Za-z0-9_-]{10,}/g, '[redacted]');
}

// An HTTP error from n8n reads `503 - "<response body>"`. That body can echo the request (a validation error quotes its
// `input`, which may be somebody's email), so keep the status and CommitmentOS's own structured fields only.
function safeHttpMessage(message) {
  const m = /^(\d{3}) - ([\s\S]*)$/.exec(String(message));
  if (!m) return String(message);
  let body = m[2].trim();
  for (let i = 0; i < 2 && typeof body === 'string'; i += 1) {
    try { body = JSON.parse(body); } catch (e) { return `HTTP ${m[1]}`; }
  }
  if (!body || typeof body !== 'object') return `HTTP ${m[1]}`;
  if (Array.isArray(body.detail)) {
    const fields = body.detail.map((d) => (d && Array.isArray(d.loc) ? d.loc.filter((x) => typeof x === 'string').join('.') : null)).filter(Boolean);
    return `HTTP ${m[1]}: the request was invalid (${[...new Set(fields)].slice(0, 6).join(', ') || 'no field named'})`;
  }
  const code = typeof body.code === 'string' ? body.code : typeof body.status === 'string' ? body.status : null;
  const detail = typeof body.detail === 'string' ? body.detail : typeof body.error_message === 'string' ? body.error_message : null;
  return [`HTTP ${m[1]}`, code, detail && truncate(detail, 200)].filter(Boolean).join(': ');
}

// n8n hands errors over as Error objects, {message}, {error:{message}} or plain strings
function errorMessage(e) {
  if (e == null) return 'unknown error';
  if (typeof e === 'string') return safeHttpMessage(e);
  const inner = e.error && typeof e.error === 'object' ? e.error : e;
  const parts = [inner.message || inner.description || e.message, /^\d{3}$/.test(String(inner.httpCode)) ? `HTTP ${inner.httpCode}` : null].filter(Boolean);
  return parts.length ? safeHttpMessage(parts.join(' - ')) : JSON.stringify(inner).slice(0, 300);
}

const isEmail = (s) => typeof s === 'string' && /^[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+$/.test(s.trim());
