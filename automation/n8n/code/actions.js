// @include _common.js
// Approved Actions: execute what a person approved (send a follow-up, add a calendar event) and report the result.

const claimedApprovals = (claim) => {
  if (claim && claim.item) return [claim.item]; // push path: GET /approvals/{id}
  return claim && Array.isArray(claim.items) ? claim.items : []; // pull path: POST /approvals/claim
};

function followUpMail(item) {
  const p = item.payload || {};
  return { from: item.from_email, to: p.to, replyTo: item.user_email, subject: p.subject, text: p.body };
}

// ---- iCalendar (RFC 5545): a real invite any calendar app can import -------------------------------------------------
const icsEscape = (s) => String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');
const icsTime = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) throw new Error(`invalid time in calendar payload: ${iso}`);
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
};
// content lines are limited to 75 octets; longer ones continue on the next line after a single space
function icsFold(line) {
  const bytes = Buffer.from(line, 'utf8');
  if (bytes.length <= 75) return line;
  const parts = [];
  let start = 0;
  let limit = 75;
  while (start < bytes.length) {
    let end = Math.min(start + limit, bytes.length);
    while (end < bytes.length && (bytes[end] & 0xc0) === 0x80) end -= 1; // never split a multi-byte character
    parts.push(bytes.subarray(start, end).toString('utf8'));
    start = end;
    limit = 74;
  }
  return parts.join('\r\n ');
}

function buildIcs(item, nowIso = new Date().toISOString()) {
  const p = item.payload || {};
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//CommitmentOS//Calendar Sync//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:approval-${item.id}@commitmentos`,
    `DTSTAMP:${icsTime(nowIso)}`,
    `DTSTART:${icsTime(p.start_at)}`,
    `DTEND:${icsTime(p.end_at)}`,
    `SUMMARY:${icsEscape(p.title)}`,
    ...(p.description ? [`DESCRIPTION:${icsEscape(p.description)}`] : []),
    ...(p.location ? [`LOCATION:${icsEscape(p.location)}`] : []),
    'BEGIN:VALARM',
    'ACTION:DISPLAY',
    `DESCRIPTION:${icsEscape(p.title)}`,
    'TRIGGER:-PT15M',
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR',
  ];
  return `${lines.map(icsFold).join('\r\n')}\r\n`;
}

function inviteMail(item, nowIso) {
  const p = item.payload || {};
  return {
    from: item.from_email,
    to: item.user_email,
    subject: `Calendar invite: ${p.title}`,
    text: `You approved adding "${p.title}" to your calendar.\nOpen the attached invite to add it (${p.start_at} to ${p.end_at}, ${p.timezone || 'UTC'}).`,
    ics: buildIcs(item, nowIso),
  };
}

function executionReport(item, outcome, detail, executionId) {
  const base = { approval_id: item.id, n8n_execution_id: String(executionId) };
  if (outcome === 'executed') return { event: 'approval.executed', ...base, result: detail || {} };
  return { event: 'approval.failed', ...base, error: truncate(redact(errorMessage(detail)), 900) };
}

const localCalendarResult = (item) => ({ provider: 'LOCAL', external_id: `local-${item.id}`, delivered_to: item.user_email });
const googleCalendarResult = (created) => ({ provider: 'GOOGLE', external_id: created && created.id, url: created && created.htmlLink });

function summariseActions(reports) {
  const done = reports.filter((r) => r.event === 'approval.executed').length;
  const failed = reports.filter((r) => r.event === 'approval.failed');
  return { total: reports.length, executed: done, failed: failed.length, errors: failed.slice(0, 5).map((r) => r.error), status: failed.length === 0 ? 'SUCCESS' : done === 0 ? 'FAILED' : 'PARTIAL' };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { claimedApprovals, followUpMail, buildIcs, inviteMail, executionReport, localCalendarResult, googleCalendarResult, summariseActions, icsFold, icsEscape, icsTime };
}
