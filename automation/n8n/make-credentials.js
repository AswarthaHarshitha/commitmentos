// Builds the credential objects n8n imports at start-up, from environment variables.
// Secrets therefore live only in .env (git-ignored) and, encrypted, inside n8n's database - never in workflow JSON.
// IDs are fixed so workflows can reference credentials by id.
const ids = require('./lib/credential-ids');
const e = process.env;
const need = (name) => {
  if (!e[name]) { console.error(`make-credentials: ${name} is not set`); process.exit(2); }
  return e[name];
};

const credentials = [
  {
    ...ids.API_INBOUND,
    data: { name: "X-Webhook-Secret", value: need("N8N_INBOUND_SECRET") },
  },
  {
    ...ids.WEBHOOK_AUTH,
    data: { name: "X-CommitmentOS-Key", value: need("N8N_OUTBOUND_SECRET") },
  },
  {
    ...ids.SMTP,
    data: {
      user: e.SMTP_USER || "",
      password: e.SMTP_PASSWORD || "",
      host: e.SMTP_HOST || "mailpit",
      port: Number(e.SMTP_PORT || 1025),
      secure: e.SMTP_SECURE === "true",
      disableStartTls: e.SMTP_SECURE !== "true" && !(e.SMTP_USER),
      hostName: "",
    },
  },
];
if (e.TELEGRAM_BOT_TOKEN) {
  credentials.push({
    ...ids.TELEGRAM,
    data: { accessToken: e.TELEGRAM_BOT_TOKEN, baseUrl: "https://api.telegram.org" },
  });
}
process.stdout.write(JSON.stringify(credentials));
