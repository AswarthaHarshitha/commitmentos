"use client";

import { CircleCheck, CircleHelp, CircleX, Inbox, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import { errorMessage, type ImportOutcome } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { invalidateAfterChange, useImportEmail, useImportOutcome } from "@/lib/queries";
import { toLocalInputs, zonedTimeToUtc } from "@/lib/time";
import { useQueryClient } from "@tanstack/react-query";

/** Where the text goes, said plainly: pasting an email sends it to whichever language model is configured. */
function readerNote(provider: string | undefined, model: string | null | undefined, configured: boolean | undefined): { text: string; usable: boolean } {
  if (!configured) return { text: "No language model is configured on this server, so an email cannot be read. Add the commitment by hand instead.", usable: false };
  if (provider === "gemini") return { text: `The text is sent to Google's Gemini service (${model ?? "default model"}) to be read.`, usable: true };
  return { text: `The text is sent to the language model server you configured${model ? ` (${model})` : ""} to be read.`, usable: true };
}

const GIVE_UP_AFTER_MS = 150_000;

function Outcome({ outcome, onAgain, onClose }: { outcome: ImportOutcome; onAgain: () => void; onClose: () => void }) {
  const found = (icon: ReactNode, title: string, body?: ReactNode, action?: ReactNode) => (
    <div className="flex gap-3.5 py-2">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-[15px] font-medium text-ink">{title}</p>
        {body && <p className="mt-1 text-[14px] leading-6 text-text-2">{body}</p>}
        {action && <div className="mt-4 flex flex-wrap gap-2">{action}</div>}
      </div>
    </div>
  );
  const linkButton = "inline-flex h-9 items-center rounded-md border border-line-strong bg-surface px-3.5 text-[14px] font-medium text-ink hover:bg-surface-2";
  const again = <Button onClick={onAgain}>Import another</Button>;

  if (outcome.status === "failed") {
    return found(<CircleX aria-hidden className="size-5 text-danger" strokeWidth={1.7} />, "This email could not be read", outcome.detail ?? "Something went wrong.", <Button onClick={onAgain}>Try again</Button>);
  }
  switch (outcome.disposition) {
    case "OBLIGATION_CREATED":
    case "MANUAL":
      return found(
        <CircleCheck aria-hidden className="size-5 text-ok" strokeWidth={1.7} />,
        `Added: ${outcome.title ?? "a commitment"}`,
        "It is in your inbox until you have looked at it, and reminders are scheduled from its deadline.",
        <>
          {outcome.obligation_id && (
            <Link href={`/obligations/${outcome.obligation_id}`} onClick={onClose} className={linkButton}>
              Open it
            </Link>
          )}
          {again}
        </>,
      );
    case "NEEDS_REVIEW":
      return found(
        <CircleHelp aria-hidden className="size-5 text-warn" strokeWidth={1.7} />,
        `Found something to check: ${outcome.title ?? "a possible commitment"}`,
        "CommitmentOS was not sure enough to track it by itself. It is waiting in your inbox for you to accept, edit or dismiss.",
        <>
          <Link href="/inbox" onClick={onClose} className={linkButton}>
            Review it
          </Link>
          {again}
        </>,
      );
    case "CANDIDATE":
      return found(
        <CircleHelp aria-hidden className="size-5 text-warn" strokeWidth={1.7} />,
        `Might be a commitment: ${outcome.title ?? "a possible commitment"}`,
        "The reading was uncertain, so nothing was created. It is in your inbox if you want to track it.",
        <>
          <Link href="/inbox" onClick={onClose} className={linkButton}>
            Take a look
          </Link>
          {again}
        </>,
      );
    case "DUPLICATE":
      return found(
        <Inbox aria-hidden className="size-5 text-text-2" strokeWidth={1.7} />,
        "This is already being tracked",
        "It matches a commitment you already have, so nothing new was added.",
        <>
          {outcome.obligation_id && (
            <Link href={`/obligations/${outcome.obligation_id}`} onClick={onClose} className={linkButton}>
              Open it
            </Link>
          )}
          {again}
        </>,
      );
    default:
      return found(<CircleX aria-hidden className="size-5 text-text-3" strokeWidth={1.7} />, "No commitment found in this email", "Nothing in it asks for anything or sets a deadline, so nothing was added.", again);
  }
}

function ImportForm({ onClose }: { onClose: () => void }) {
  const { now, timeZone, status } = useClock();
  const client = useQueryClient();
  const send = useImportEmail();
  const arrival = toLocalInputs(now.toISOString(), timeZone);
  const [sender, setSender] = useState("");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [date, setDate] = useState(arrival.date);
  const [time, setTime] = useState(arrival.time);
  const [arrivalChanged, setArrivalChanged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; since: string; startedAt: number } | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  const note = readerNote(status?.llm_provider, status?.llm_model, status?.llm_configured);

  const outcome = useImportOutcome(job?.id ?? null, job?.since ?? null);
  const result = outcome.data && outcome.data.status !== "processing" ? outcome.data : null;

  useEffect(() => {
    if (result) invalidateAfterChange(client);
  }, [result, client]);
  useEffect(() => {
    if (!job || result) return;
    const wait = Math.max(0, job.startedAt + GIVE_UP_AFTER_MS - Date.now());
    const id = setTimeout(() => setGaveUp(true), wait);
    return () => clearTimeout(id);
  }, [job, result]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!text.trim()) {
      setError("Paste the text of the email first.");
      return;
    }
    if (arrivalChanged && !date) {
      setError("Choose the day it arrived, or leave it as it is.");
      return;
    }
    try {
      const queued = await send.mutateAsync({
        body: text,
        ...(subject.trim() ? { subject: subject.trim() } : {}),
        ...(sender.trim() ? { sender: sender.trim() } : {}),
        ...(arrivalChanged ? { received_at: zonedTimeToUtc(date, time || "00:00", timeZone).toISOString() } : {}),
      });
      setGaveUp(false);
      setJob({ id: queued.external_id, since: queued.requested_at ?? new Date().toISOString(), startedAt: Date.now() });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const again = () => {
    setJob(null);
    setGaveUp(false);
  };

  if (job && result) return <Outcome outcome={result} onAgain={() => { again(); setText(""); setSubject(""); setSender(""); }} onClose={onClose} />;
  if (job) {
    return (
      <div role="status" aria-live="polite" className="py-4">
        <div className="flex items-start gap-3.5">
          <LoaderCircle aria-hidden className="mt-0.5 size-5 shrink-0 animate-spin text-text-2" strokeWidth={1.7} />
          <div>
            <p className="text-[15px] font-medium text-ink">{gaveUp ? "Still reading it" : "Reading the email"}</p>
            <p className="mt-1 text-[14px] leading-6 text-text-2">
              {gaveUp
                ? "This is taking longer than usual. It will keep going in the background; the result will show up in your inbox, or as a failed run under Automations."
                : "Finding what it asks of you and when it is due. This usually takes a few seconds."}
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end">
          <Button onClick={onClose}>{gaveUp ? "Close" : "Keep working in the background"}</Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4 pb-2" noValidate>
      <Field label="The email" error={error && !text.trim() ? error : null} hint="Paste the message as you see it, including the greeting and signature.">
        {(p) => <Textarea {...p} value={text} rows={9} maxLength={100000} autoFocus onChange={(e) => setText(e.target.value)} placeholder="Paste the text of the email here" />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="From" hint="Optional. A name, an address, or both.">
          {(p) => <Input {...p} value={sender} maxLength={400} onChange={(e) => setSender(e.target.value)} placeholder="Name <name@example.com>" />}
        </Field>
        <Field label="Subject" hint="Optional.">
          {(p) => <Input {...p} value={subject} maxLength={500} onChange={(e) => setSubject(e.target.value)} />}
        </Field>
      </div>
      <fieldset>
        <legend className="mb-1.5 text-[13px] font-medium text-ink">When it arrived</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <Input aria-label="Date it arrived" type="date" value={date} onChange={(e) => { setDate(e.target.value); setArrivalChanged(true); }} />
          <Input aria-label="Time it arrived" type="time" value={time} onChange={(e) => { setTime(e.target.value); setArrivalChanged(true); }} />
        </div>
        <p className="mt-1.5 text-[13px] text-text-2">Words like &ldquo;tomorrow&rdquo; or &ldquo;by Friday&rdquo; are read from this moment. Leave it as it is for an email that just arrived.</p>
      </fieldset>
      <p className="rounded-md border border-line bg-surface-2/60 px-3 py-2 text-[13px] leading-5 text-text-2">{note.text}</p>
      {error && text.trim() && (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      )}
      <div className="-mx-6 mt-2 flex items-center justify-end gap-2 border-t border-line px-6 pt-4">
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" loading={send.isPending} disabled={!note.usable}>
          Read this email
        </Button>
      </div>
    </form>
  );
}

export function ImportEmailDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Import an email" description="Paste an email that asks something of you. It is read the same way as one arriving in your mailbox.">
      <ImportForm onClose={() => onOpenChange(false)} />
    </Modal>
  );
}
