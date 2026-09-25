"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { errorMessage, type Approval } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { useEditApproval } from "@/lib/queries";
import { toLocalInputs, zonedTimeToUtc } from "@/lib/time";

const text = (payload: Approval["payload"], key: string) => (typeof payload[key] === "string" ? (payload[key] as string) : "");

function ApprovalForm({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const { timeZone } = useClock();
  const toast = useToast();
  const edit = useEditApproval();
  const p = approval.payload;
  const calendar = approval.action_type === "CREATE_CALENDAR_EVENT";
  const zone = text(p, "timezone") || timeZone;
  const start = text(p, "start_at") ? toLocalInputs(text(p, "start_at"), zone) : { date: "", time: "" };
  const end = text(p, "end_at") ? toLocalInputs(text(p, "end_at"), zone) : { date: "", time: "" };

  const [subject, setSubject] = useState(text(p, "subject"));
  const [body, setBody] = useState(text(p, "body"));
  const [startDate, setStartDate] = useState(start.date);
  const [startTime, setStartTime] = useState(start.time);
  const [endDate, setEndDate] = useState(end.date);
  const [endTime, setEndTime] = useState(end.time);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const patch: Record<string, unknown> = {};
    if (calendar) {
      if (!startDate || !startTime || !endDate || !endTime) return setError("Choose a start and an end.");
      const startAt = zonedTimeToUtc(startDate, startTime, zone);
      const endAt = zonedTimeToUtc(endDate, endTime, zone);
      if (endAt <= startAt) return setError("The event has to end after it starts.");
      if (startAt.toISOString() !== new Date(text(p, "start_at")).toISOString()) patch.start_at = startAt.toISOString();
      if (endAt.toISOString() !== new Date(text(p, "end_at")).toISOString()) patch.end_at = endAt.toISOString();
    } else {
      if (!subject.trim() || !body.trim()) return setError("A follow-up needs a subject and a message.");
      if (subject.trim() !== text(p, "subject")) patch.subject = subject.trim();
      if (body !== text(p, "body")) patch.body = body;
    }
    if (Object.keys(patch).length === 0) return onDone();
    try {
      await edit.mutateAsync({ id: approval.id, patch });
      toast({ tone: "ok", title: "Draft updated", description: "Nothing happens until you approve it." });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 pb-2" noValidate>
      {calendar ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Starts on">{(f) => <Input {...f} type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />}</Field>
            <Field label="Start time">{(f) => <Input {...f} type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />}</Field>
            <Field label="Ends on">{(f) => <Input {...f} type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />}</Field>
            <Field label="End time">{(f) => <Input {...f} type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />}</Field>
          </div>
          <p className="text-[13px] text-text-2">Times are in {zone.replace("_", " ")}.</p>
        </>
      ) : (
        <>
          <Field label="To" hint="A follow-up always goes to the address the original email came from. It cannot be changed here.">
            {(f) => <Input {...f} type="email" value={text(p, "to")} readOnly disabled />}
          </Field>
          <Field label="Subject">{(f) => <Input {...f} value={subject} maxLength={300} onChange={(e) => setSubject(e.target.value)} />}</Field>
          <Field label="Message">{(f) => <Textarea {...f} value={body} maxLength={8000} rows={8} onChange={(e) => setBody(e.target.value)} />}</Field>
        </>
      )}
      {error && (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      )}
      <div className="-mx-6 mt-2 flex items-center justify-end gap-2 border-t border-line px-6 pt-4">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" loading={edit.isPending}>
          Save draft
        </Button>
      </div>
    </form>
  );
}

export function EditApprovalDialog({ approval, onOpenChange }: { approval: Approval | null; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal open={approval !== null} onOpenChange={onOpenChange} title="Edit before approving" description="What you approve is exactly what will be sent or added.">
      {approval && <ApprovalForm approval={approval} onDone={() => onOpenChange(false)} />}
    </Modal>
  );
}
