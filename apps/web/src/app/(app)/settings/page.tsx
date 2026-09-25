"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/field";
import { Toggle } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import { errorMessage, type User } from "@/lib/api";
import { parseHours } from "@/lib/hours";
import { useClock } from "@/lib/clock";
import { useAuth, useMe, useUpdateProfile } from "@/lib/queries";

function timeZones(current: string): string[] {
  const supported = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return supported.includes(current) ? supported : [current, ...supported];
}

function Profile({ me }: { me: User }) {
  const toast = useToast();
  const update = useUpdateProfile();
  const [name, setName] = useState(me.display_name);
  const [zone, setZone] = useState(me.timezone);
  const zones = useMemo(() => timeZones(me.timezone), [me.timezone]);
  const dirty = name.trim() !== me.display_name || zone !== me.timezone;

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await update.mutateAsync({ display_name: name.trim(), timezone: zone });
      toast({ tone: "ok", title: "Profile saved", description: zone !== me.timezone ? "Deadlines written without a time zone will now be read in the new one." : undefined });
    } catch (error) {
      toast({ tone: "danger", title: "Could not save", description: errorMessage(error) });
    }
  }
  return (
    <Card>
      <CardHeader title="Profile" description="How CommitmentOS greets you, and which clock your deadlines follow." />
      <form onSubmit={save} className="space-y-4 px-5 py-5">
        <Field label="Your name">{(p) => <Input {...p} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoComplete="name" />}</Field>
        <Field label="Email" hint="Reminders are sent here. It cannot be changed.">{(p) => <Input {...p} value={me.email} readOnly disabled />}</Field>
        <Field label="Time zone" hint='Used to read "tomorrow" or "5pm" in a message, and to show every time on this site.'>
          {(p) => (
            <Select {...p} value={zone} onChange={(e) => setZone(e.target.value)}>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={!dirty} loading={update.isPending}>
            Save profile
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Notifications({ me }: { me: User }) {
  const toast = useToast();
  const update = useUpdateProfile();
  const prefs = me.preferences;
  const [email, setEmail] = useState(prefs.notify_email);
  const [telegram, setTelegram] = useState(prefs.notify_telegram);
  const [chat, setChat] = useState(prefs.telegram_chat_id ?? "");
  const dirty = email !== prefs.notify_email || telegram !== prefs.notify_telegram || chat.trim() !== (prefs.telegram_chat_id ?? "");

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await update.mutateAsync({ preferences: { ...prefs, notify_email: email, notify_telegram: telegram, telegram_chat_id: chat.trim() || null } });
      toast({ tone: "ok", title: "Notification settings saved" });
    } catch (error) {
      toast({ tone: "danger", title: "Could not save", description: errorMessage(error) });
    }
  }
  return (
    <Card>
      <CardHeader title="Notifications" description="Where reminders are delivered. They always appear in the app." />
      <form onSubmit={save} className="space-y-5 px-5 py-5">
        <Toggle checked={email} onCheckedChange={setEmail} label="Email" hint={`Reminders go to ${me.email}.`} />
        <Toggle checked={telegram} onCheckedChange={setTelegram} label="Telegram" hint="Needs a Telegram bot to be connected on the server." />
        {telegram && (
          <Field label="Telegram chat ID" hint="The number of the chat the bot should message.">
            {(p) => <Input {...p} value={chat} maxLength={64} inputMode="numeric" onChange={(e) => setChat(e.target.value)} />}
          </Field>
        )}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={!dirty} loading={update.isPending}>
            Save notifications
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Reminders({ me }: { me: User }) {
  const toast = useToast();
  const update = useUpdateProfile();
  const prefs = me.preferences;
  const [offsets, setOffsets] = useState((prefs.reminder_offsets_hours ?? []).join(", "));
  const [escalate, setEscalate] = useState(prefs.escalate_after_hours ? String(prefs.escalate_after_hours) : "");
  const [dayEnd, setDayEnd] = useState(prefs.business_day_end ?? "");
  const [order, setOrder] = useState<string>(prefs.date_order ?? "");
  const [error, setError] = useState<string | null>(null);
  const stored = { offsets: (prefs.reminder_offsets_hours ?? []).join(","), escalate: prefs.escalate_after_hours ? String(prefs.escalate_after_hours) : "", dayEnd: prefs.business_day_end ?? "", order: prefs.date_order ?? "" };
  // "24, 6" and "24,6" are the same setting; anything that does not parse is a change the person is still typing
  const typedOffsets = (() => {
    try {
      return (parseHours(offsets) ?? []).join(",");
    } catch {
      return offsets;
    }
  })();
  const dirty = typedOffsets !== stored.offsets || escalate.trim() !== stored.escalate || dayEnd !== stored.dayEnd || order !== stored.order;

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    let hours: number[] | null;
    try {
      hours = parseHours(offsets);
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    const escalateHours = escalate.trim() ? Number(escalate) : null;
    if (escalateHours !== null && (!Number.isInteger(escalateHours) || escalateHours < 1 || escalateHours > 720)) {
      setError("Escalation is a whole number of hours between 1 and 720.");
      return;
    }
    try {
      await update.mutateAsync({
        preferences: { ...prefs, reminder_offsets_hours: hours, escalate_after_hours: escalateHours, business_day_end: dayEnd || null, date_order: (order as "MDY" | "DMY") || null },
      });
      toast({ tone: "ok", title: "Reminder settings saved", description: "Reminders for open commitments will be rescheduled." });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Card>
      <CardHeader title="Reminders" description="When CommitmentOS nudges you. Leave a field empty to use the default." />
      <form onSubmit={save} className="space-y-4 px-5 py-5" noValidate>
        <Field label="Remind me this many hours before a deadline" hint="Default: 24, 6. Separate with commas.">
          {(p) => <Input {...p} value={offsets} inputMode="numeric" placeholder="24, 6" onChange={(e) => setOffsets(e.target.value)} />}
        </Field>
        <Field label="Escalate when overdue by (hours)" hint="Default: 24. After this, an overdue commitment is marked as escalated.">
          {(p) => <Input {...p} value={escalate} inputMode="numeric" placeholder="24" onChange={(e) => setEscalate(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label='"End of day" means' hint="Default: 17:00.">
            {(p) => <Input {...p} type="time" value={dayEnd} onChange={(e) => setDayEnd(e.target.value)} />}
          </Field>
          <Field label="Dates like 03/04 are" hint="Used when a message is not clear.">
            {(p) => (
              <Select {...p} value={order} onChange={(e) => setOrder(e.target.value)}>
                <option value="">Default (month first)</option>
                <option value="MDY">Month, then day</option>
                <option value="DMY">Day, then month</option>
              </Select>
            )}
          </Field>
        </div>
        {error && (
          <p role="alert" className="text-[13px] text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={!dirty} loading={update.isPending}>
            Save reminders
          </Button>
        </div>
      </form>
    </Card>
  );
}

const SMTP_EXAMPLE = `SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_SECURE=true
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<an app password, not your normal one>
NOTIFY_FROM_EMAIL=you@gmail.com`;

function EmailDelivery({ local, known }: { local: boolean; known: boolean }) {
  if (!known) return null;
  return (
    <Card id="email" className={local ? "scroll-mt-20 border-warn-line" : "scroll-mt-20"}>
      <CardHeader
        title="Email delivery"
        description={local ? "Right now no email leaves this computer." : "Reminders and follow-ups are sent through your mail account."}
      />
      <div className="space-y-4 px-5 py-5 text-[14px] leading-6 text-text-2">
        {local ? (
          <>
            <p>
              Reminders and follow-ups are handed to a local test inbox that only catches them, so they never reach a real mailbox, even though each step reports that it went through. To send real email, give CommitmentOS an
              account to send from.
            </p>
            <ol className="list-decimal space-y-2 pl-5 marker:text-text-3">
              <li>
                Put the account&rsquo;s details in the <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[12.5px] text-ink">.env</code> file next to the project. For Gmail, that is an app password (Google Account, Security, 2-Step Verification, App passwords):
                <pre className="mt-2 overflow-x-auto rounded-md border border-line bg-surface-2 p-3 font-mono text-[12.5px] leading-5 text-ink">{SMTP_EXAMPLE}</pre>
              </li>
              <li>
                Apply it: <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[12.5px] text-ink">docker compose up -d api &amp;&amp; ./scripts/n8n-reload.sh</code>
              </li>
              <li>This card and the notice at the top of every page disappear once mail is going out through a real server.</li>
            </ol>
            <p className="text-[13px] text-text-3">The password stays in that file on your machine. It is never typed into this app, and never stored in the project.</p>
          </>
        ) : (
          <p>Follow-ups go to the address the original email came from, and reminders to your own address.</p>
        )}
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const { data: me } = useMe();
  const { status } = useClock();
  const { logout } = useAuth();
  const router = useRouter();
  if (!me) return null;
  // key: a save replaces the user object, and the forms should start again from what the server now holds
  const version = `${me.timezone}|${me.display_name}|${JSON.stringify(me.preferences)}`;

  return (
    <>
      <PageHeader title="Settings" description="Your profile, how you are notified, and how early CommitmentOS reminds you." />
      <div className="grid max-w-3xl gap-6">
        <Profile key={`p-${version}`} me={me} />
        <Notifications key={`n-${version}`} me={me} />
        <EmailDelivery local={status?.email_delivery === "local_test_inbox"} known={status !== undefined} />

        <Reminders key={`r-${version}`} me={me} />

        <Card>
          <CardHeader title="Session" />
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
            <p className="text-[14px] text-text-2">
              Signed in as <span className="font-medium text-ink">{me.email}</span>
            </p>
            <Button
              icon={<LogOut aria-hidden className="size-4" strokeWidth={1.7} />}
              loading={logout.isPending}
              onClick={() => logout.mutate(undefined, { onSuccess: () => router.replace("/login") })}
            >
              Sign out
            </Button>
          </div>
        </Card>
      </div>
    </>
  );
}
