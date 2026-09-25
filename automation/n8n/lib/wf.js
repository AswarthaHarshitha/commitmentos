'use strict';
// A tiny builder for n8n workflow JSON. Workflows are *generated* (build.js) so that ids, credentials, retry
// policy and API wiring are consistent everywhere, and so the Code-node logic can live in real files with real tests.
const crypto = require('node:crypto');
const CRED = require('./credential-ids');

const API_BASE = process.env.COMMITMENTOS_API_URL || 'http://api:8000';

// deterministic uuid-shaped ids: regenerating a workflow never changes a node's identity (clean diffs, stable webhook urls)
function uid(...parts) {
  const h = crypto.createHash('sha1').update(parts.join('|')).digest('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20, 32)}`;
}

const credRef = (c) => ({ id: c.id, name: c.name });
const isExpr = (v) => typeof v === 'string' && v.includes('{{');
const asExpr = (v) => (isExpr(v) ? `=${v}` : v);

class Workflow {
  constructor({ id, key, name, description = '', errorWorkflowId = null }) {
    Object.assign(this, { id, key, name, description, errorWorkflowId, nodes: [], connections: {} });
  }

  /** add a node at grid position [column, row] */
  add(node, [col, row]) {
    if (this.nodes.some((n) => n.name === node.name)) throw new Error(`duplicate node name "${node.name}" in ${this.key}`);
    const placed = { id: uid(this.key, node.name), position: [240 + col * 280, 200 + row * 170], ...node };
    this.nodes.push(placed);
    return placed;
  }

  /** connect `from` -> `to`; `output` picks the branch (IF: 0 = true, 1 = false; error output = 1 on nodes that have one) */
  link(from, to, output = 0) {
    const outs = (this.connections[from.name] ||= { main: [] }).main;
    while (outs.length <= output) outs.push([]);
    outs[output].push({ node: to.name, type: 'main', index: 0 });
    return to;
  }

  chain(...nodes) {
    nodes.reduce((a, b) => this.link(a, b));
    return nodes[nodes.length - 1];
  }

  toJSON() {
    return {
      id: this.id,
      name: this.name,
      active: true,
      nodes: this.nodes,
      connections: this.connections,
      settings: {
        executionOrder: 'v1',
        saveDataErrorExecution: 'all',
        saveExecutionProgress: false,
        ...(this.errorWorkflowId ? { errorWorkflow: this.errorWorkflowId } : {}),
      },
      pinData: {},
      meta: { templateCredsSetupCompleted: true, builtFrom: 'automation/n8n/build.js', description: this.description },
      versionId: uid(this.key, 'version'),
    };
  }
}

const node = (name, type, typeVersion, parameters = {}, extra = {}) => ({ name, type, typeVersion, parameters, ...extra });

// ------------------------------------------------------------------------------------------------ triggers
const webhookTrigger = (name, path, { responseMode = 'lastNode' } = {}) =>
  node(
    name,
    'n8n-nodes-base.webhook',
    2.1,
    { httpMethod: 'POST', path, authentication: 'headerAuth', responseMode, options: {} },
    { credentials: { httpHeaderAuth: credRef(CRED.WEBHOOK_AUTH) }, webhookId: uid('webhook', path) },
  );

const scheduleTrigger = (name, { minutes }) =>
  node(name, 'n8n-nodes-base.scheduleTrigger', 1.2, { rule: { interval: [{ field: 'minutes', minutesInterval: minutes }] } });

const subWorkflowTrigger = (name) => node(name, 'n8n-nodes-base.executeWorkflowTrigger', 1.1, { inputSource: 'passthrough' });

const errorTrigger = (name) => node(name, 'n8n-nodes-base.errorTrigger', 1, {});

// ------------------------------------------------------------------------------------------------ logic
const code = (name, source, { each = false, notes, errorOutput = false } = {}) =>
  node(
    name,
    'n8n-nodes-base.code',
    2,
    { mode: each ? 'runOnceForEachItem' : 'runOnceForAllItems', language: 'javaScript', jsCode: source },
    { ...(notes ? { notes, notesInFlow: true } : {}), ...(errorOutput ? { onError: 'continueErrorOutput' } : {}) },
  );

const condition = (leftValue, operator, rightValue) => ({
  id: uid('cond', leftValue, operator.operation, String(rightValue)),
  leftValue: asExpr(leftValue),
  rightValue,
  operator,
});
const OPS = {
  isTrue: { type: 'boolean', operation: 'true', singleValue: true },
  isFalse: { type: 'boolean', operation: 'false', singleValue: true },
  equals: { type: 'string', operation: 'equals' },
  gt: { type: 'number', operation: 'gt' },
};
const conditions = (list) => ({ options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 2 }, conditions: list, combinator: 'and' });

/** IF node: output 0 = condition true, output 1 = false */
const ifTrue = (name, leftValue) => node(name, 'n8n-nodes-base.if', 2.2, { conditions: conditions([condition(leftValue, OPS.isTrue, '')]), options: {} });

/** Switch on a string field; one named output per value, plus an optional fallback as the last output */
const switchOn = (name, leftValue, values, { fallback = false } = {}) =>
  node(name, 'n8n-nodes-base.switch', 3.2, {
    rules: {
      values: values.map((v) => ({
        conditions: conditions([condition(leftValue, OPS.equals, v)]),
        renameOutput: true,
        outputKey: v,
      })),
    },
    options: fallback ? { fallbackOutput: 'extra', renameFallbackOutput: 'other' } : {},
  });

const runSubWorkflow = (name, workflowId, { wait = true, bestEffort = false } = {}) =>
  node(
    name,
    'n8n-nodes-base.executeWorkflow',
    1.2,
    { source: 'database', workflowId: { __rl: true, value: workflowId, mode: 'id' }, mode: 'once', options: { waitForSubWorkflow: wait } },
    bestEffort ? { onError: 'continueRegularOutput' } : {},
  );

// ------------------------------------------------------------------------------------------------ I/O
/**
 * HTTP call to the CommitmentOS API, authenticated with the shared secret credential.
 *  body:        a JS *expression* producing the request object, e.g. "$json.request"
 *  retries:     bounded automatic retries (n8n retry-on-fail) with a pause between them
 *  errorOutput: after the retries, route the failure to output 1 instead of failing the execution
 *  bestEffort:  after the retries, carry on with the flow (for telemetry that must never break the real work)
 */
const api = (name, method, path, { body, retries = 3, waitMs = 2000, errorOutput = false, bestEffort = false, timeoutMs = 30000 } = {}) =>
  node(
    name,
    'n8n-nodes-base.httpRequest',
    4.2,
    {
      method,
      url: asExpr(`${API_BASE}${path}`),
      authentication: 'genericCredentialType',
      genericAuthType: 'httpHeaderAuth',
      ...(body !== undefined ? { sendBody: true, specifyBody: 'json', jsonBody: `={{ JSON.stringify(${body}) }}` } : {}),
      options: { timeout: timeoutMs },
    },
    {
      credentials: { httpHeaderAuth: credRef(CRED.API_INBOUND) },
      ...(retries > 1 ? { retryOnFail: true, maxTries: retries, waitBetweenTries: waitMs } : {}),
      ...(errorOutput ? { onError: 'continueErrorOutput' } : bestEffort ? { onError: 'continueRegularOutput' } : {}),
    },
  );

/** post an event to POST /api/webhooks/n8n */
const report = (name, body, opts = {}) => api(name, 'POST', '/api/webhooks/n8n', { body, ...opts });

const sendEmail = (name, { from, to, subject, text, html, replyTo, attachments }, { errorOutput = true, retries = 2 } = {}) =>
  node(
    name,
    'n8n-nodes-base.emailSend',
    2.1,
    {
      resource: 'email',
      operation: 'send',
      fromEmail: asExpr(from),
      toEmail: asExpr(to),
      subject: asExpr(subject),
      emailFormat: html ? 'both' : 'text',
      text: asExpr(text),
      ...(html ? { html: asExpr(html) } : {}),
      options: { appendAttribution: false, ...(replyTo ? { replyTo: asExpr(replyTo) } : {}), ...(attachments ? { attachments } : {}) },
    },
    {
      credentials: { smtp: credRef(CRED.SMTP) },
      retryOnFail: retries > 1,
      maxTries: retries,
      waitBetweenTries: 2000,
      ...(errorOutput ? { onError: 'continueErrorOutput' } : {}),
    },
  );

const sendTelegram = (name, { chatId, text }) =>
  node(
    name,
    'n8n-nodes-base.telegram',
    1.2,
    { resource: 'message', operation: 'sendMessage', chatId: asExpr(chatId), text: asExpr(text), additionalFields: { appendAttribution: false } },
    { credentials: { telegramApi: credRef(CRED.TELEGRAM) }, onError: 'continueErrorOutput', retryOnFail: true, maxTries: 2, waitBetweenTries: 2000 },
  );

module.exports = {
  API_BASE, CRED, uid, Workflow, node,
  webhookTrigger, scheduleTrigger, subWorkflowTrigger, errorTrigger,
  code, ifTrue, switchOn, runSubWorkflow,
  api, report, sendEmail, sendTelegram,
};
