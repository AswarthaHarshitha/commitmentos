// @include _common.js
// Normalize + clean one incoming message into the envelope the API expects.
// Accepts (a) the ingest webhook payload {user_email, message:{...}} and (b) a Gmail Trigger item.
const MAX_BODY = 20000;

function htmlToText(html) {
  return String(html || '')
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|tr|li|h[1-6]|blockquote)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d{1,10});/g, (_, n) => (Number(n) > 0 && Number(n) < 0x110000 ? String.fromCodePoint(Number(n)) : ' '))
    .replace(/&amp;/g, '&')
    .replace(/[ \t]+/g, ' ');
}

const REPLY_MARKER = /^\s*(?:On\s.{5,200}\swrote:|-{2,}\s*Original Message\s*-{2,})\s*$/i;
const FORWARD_SUBJECT = /^\s*(?:fwd?|fw)\s*:/i;

// Drop what a reply merely quotes, so an old request is not "detected" again. A *forward* is the opposite: the
// quoted part is the whole point, so it is kept (recognised by its subject, or by a marker with nothing above it).
function stripQuoted(text, subject) {
  const lines = String(text).split('\n');
  const forwarded = FORWARD_SUBJECT.test(subject || '');
  const marker = lines.findIndex((l) => REPLY_MARKER.test(l));
  let kept = lines;
  if (!forwarded && marker > 0 && lines.slice(0, marker).join('').trim()) kept = lines.slice(0, marker);
  const signature = kept.findIndex((l) => /^--\s?$/.test(l));
  if (signature > 0) kept = kept.slice(0, signature);
  return kept.filter((l) => (forwarded || !/^\s*>/.test(l)) && !/^\s*Sent from my (iPhone|iPad|Android)/i.test(l)).join('\n');
}

function cleanText(text, html, subject) {
  let t = text && String(text).trim() ? String(text) : htmlToText(html);
  t = t.replace(/\r\n?/g, '\n').replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]/g, '');
  t = stripQuoted(t, subject).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return t.length > MAX_BODY ? t.slice(0, MAX_BODY) : t;
}

function parseAddress(value) {
  if (!value) return { email: null, name: null };
  const first = Array.isArray(value.value) ? value.value[0] : Array.isArray(value) ? value[0] : null;
  if (first && first.address) return { email: String(first.address).toLowerCase(), name: first.name || null };
  const text = typeof value === 'string' ? value : value.text || '';
  const m = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(text) || /^\s*()([^\s<>]+@[^\s<>]+)\s*$/.exec(text);
  return m ? { email: m[2].toLowerCase(), name: m[1].trim() || null } : { email: null, name: null };
}

function addressList(...fields) {
  const out = new Set();
  for (const f of fields) {
    const list = f && Array.isArray(f.value) ? f.value : Array.isArray(f) ? f : [];
    for (const a of list) if (a && a.address) out.add(String(a.address).toLowerCase());
  }
  return [...out].slice(0, 20);
}

function isoDate(ms, dateText) {
  const fromMs = ms != null && ms !== '' && Number.isFinite(Number(ms)) ? new Date(Number(ms)) : null;
  const d = fromMs || (dateText ? new Date(dateText) : null);
  return d && !Number.isNaN(d.getTime()) ? d.toISOString() : null;
}

function fromGmail(item, ownerEmail, nowIso) {
  if (!isEmail(ownerEmail)) throw new Error('Gmail path: the mailbox owner email is not configured (node "Mailbox owner")');
  const sender = parseAddress(item.from);
  const owner = ownerEmail.trim().toLowerCase();
  const rfc = item.messageId || (item.headers && (item.headers['message-id'] || item.headers['Message-ID'])) || null;
  return {
    user_email: owner,
    message: {
      source_type: 'GMAIL',
      external_id: String(item.id),
      thread_id: item.threadId ? String(item.threadId) : null,
      rfc_message_id: rfc ? String(rfc) : null,
      sender_email: sender.email,
      sender_name: sender.name,
      subject: item.subject ? String(item.subject).slice(0, 500) : null,
      body: cleanText(item.text, item.html || item.textAsHtml, item.subject),
      received_at: isoDate(item.internalDate, item.date) || nowIso,
      direction: sender.email === owner ? 'OUTBOUND' : 'INBOUND',
      recipients: addressList(item.to, item.cc),
      is_synthetic: false,
    },
    correlation_id: String(item.id),
    trigger: 'GMAIL',
  };
}

function fromWebhook(body, nowIso) {
  const message = body && body.message;
  if (!message || typeof message !== 'object') throw new Error('Invalid ingest payload: "message" is missing');
  if (!isEmail(body.user_email)) throw new Error('Invalid ingest payload: "user_email" is missing or not an email address');
  if (typeof message.external_id !== 'string' || !message.external_id.trim() || message.external_id.length > 512) {
    throw new Error('Invalid ingest payload: "message.external_id" must be a non-empty string of at most 512 characters');
  }
  const text = message.body == null ? '' : String(message.body);
  const subject = message.subject == null ? null : String(message.subject).slice(0, 500);
  if (!text.trim() && !(subject && subject.trim())) throw new Error('Invalid ingest payload: the message has neither a body nor a subject');
  const sourceType = String(message.source_type || 'DEMO').toUpperCase();
  const sender = message.sender_email ? String(message.sender_email).trim().toLowerCase() : null;
  const owner = body.user_email.trim().toLowerCase();
  return {
    user_email: owner,
    message: {
      source_type: sourceType,
      external_id: message.external_id.trim(),
      thread_id: message.thread_id ? String(message.thread_id) : null,
      rfc_message_id: message.rfc_message_id ? String(message.rfc_message_id) : null,
      sender_email: sender,
      sender_name: message.sender_name ? String(message.sender_name).slice(0, 200) : null,
      subject,
      body: cleanText(text, null, subject),
      received_at: isoDate(null, message.received_at) || nowIso,
      direction: message.direction === 'OUTBOUND' || (sender && sender === owner) ? 'OUTBOUND' : 'INBOUND',
      recipients: Array.isArray(message.recipients) ? message.recipients.filter(isEmail).map((r) => r.trim().toLowerCase()).slice(0, 20) : [],
      is_synthetic: message.is_synthetic === true || sourceType === 'DEMO',
    },
    correlation_id: message.external_id.trim(),
    trigger: 'WEBHOOK',
  };
}

function normalizeIncoming(json, { ownerEmail = null, nowIso = new Date().toISOString() } = {}) {
  if (json && json.body && typeof json.body === 'object' && json.body.message) return fromWebhook(json.body, nowIso);
  if (json && (json.threadId || json.labelIds || json.id) && !json.body) return fromGmail(json, ownerEmail, nowIso);
  throw new Error('Unrecognised incoming item: expected the ingest payload or a Gmail message');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { normalizeIncoming, htmlToText, stripQuoted, cleanText, parseAddress, isoDate, MAX_BODY };
}
