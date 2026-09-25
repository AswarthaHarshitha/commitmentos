'use strict';
// Fixed ids so workflow JSON can reference credentials that make-credentials.js creates from environment variables.
// (Secrets never appear here - only ids and display names.)
module.exports = {
  API_INBOUND: { id: 'cosApiInbound0001', type: 'httpHeaderAuth', name: 'CommitmentOS API (n8n -> API secret)' },
  WEBHOOK_AUTH: { id: 'cosWebhookAuth0002', type: 'httpHeaderAuth', name: 'CommitmentOS webhooks (API -> n8n secret)' },
  SMTP: { id: 'cosSmtp000000003', type: 'smtp', name: 'SMTP (Mailpit in development)' },
  TELEGRAM: { id: 'cosTelegram00004', type: 'telegramApi', name: 'Telegram bot' },
};
