"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { errorMessage, type Obligation, type ObligationType, type Priority } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { PRIORITY_LABEL, TYPE_LABEL } from "@/lib/labels";
import { useCreateObligation, useEditObligation } from "@/lib/queries";
import { toLocalInputs } from "@/lib/time";

const RECURRENCE = ["", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"] as const;
const RECURRENCE_LABEL: Record<string, string> = { "": "Does not repeat", DAILY: "Every day", WEEKLY: "Every week", MONTHLY: "Every month", YEARLY: "Every year" };

export interface FormState {
  title: string;
  description: string;
  type: ObligationType;
  priority: Priority;
  date: string;
  time: string;
  name: string;
  email: string;
  recurrence: string;
}

function initial(ob: Obligation | undefined, timeZone: string): FormState {
  const due = ob?.due_at ? toLocalInputs(ob.due_at, timeZone) : { date: "", time: "" };
  return {
    title: ob?.title ?? "",
    description: ob?.description ?? "",
    type: ob?.obligation_type ?? "TASK",
    priority: ob?.priority ?? "MEDIUM",
    date: due.date,
    // a date-only deadline is stored as the end of that day; showing 23:59 would make it look like a chosen time
    time: ob?.due_precision === "DATE" ? "" : due.time,
    name: ob?.counterparty_name ?? "",
    email: ob?.counterparty_email ?? "",
    recurrence: ob?.recurrence ?? "",
  };
}

/** Only what the person changed is sent, so an edit never overwrites something the server has since updated. */
export function buildPatch(before: FormState, after: FormState): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (after.title.trim() !== before.title) patch.title = after.title.trim();
  if (after.description.trim() !== before.description) patch.description = after.description.trim();
  if (after.type !== before.type) patch.obligation_type = after.type;
  if (after.priority !== before.priority) patch.priority = after.priority;
  if (after.name.trim() !== before.name && after.name.trim()) patch.counterparty_name = after.name.trim();
  if (after.email.trim() !== before.email && after.email.trim()) patch.counterparty_email = after.email.trim();
  if (after.recurrence !== before.recurrence) {
    if (after.recurrence) patch.recurrence = after.recurrence;
    else patch.clear_recurrence = true;
  }
  if (after.date !== before.date || after.time !== before.time) {
    if (after.date) patch.due = { date: after.date, ...(after.time ? { time: after.time } : {}) };
    else patch.clear_due = true;
  }
  return patch;
}

function ObligationForm({ ob, onDone, emailLocked }: { ob?: Obligation; onDone: () => void; emailLocked: boolean }) {
  const { timeZone } = useClock();
  const toast = useToast();
  const edit = useEditObligation();
  const create = useCreateObligation();
  const start = initial(ob, timeZone);
  const [form, setForm] = useState<FormState>(start);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const saving = edit.isPending || create.isPending;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!form.title.trim()) {
      setError("Give it a short title so you can recognise it later.");
      return;
    }
    if (form.time && !form.date) {
      setError("Choose a date for that time, or clear the time.");
      return;
    }
    try {
      if (ob) {
        const patch = buildPatch(start, form);
        if (Object.keys(patch).length === 0) {
          onDone();
          return;
        }
        await edit.mutateAsync({ id: ob.id, patch });
        toast({ tone: "ok", title: "Saved", description: "Its reminders were rescheduled to match." });
      } else {
        await create.mutateAsync({
          title: form.title.trim(),
          obligation_type: form.type,
          priority: form.priority,
          ...(form.description.trim() ? { description: form.description.trim() } : {}),
          ...(form.name.trim() ? { counterparty_name: form.name.trim() } : {}),
          ...(form.email.trim() ? { counterparty_email: form.email.trim() } : {}),
          ...(form.recurrence ? { recurrence: form.recurrence } : {}),
          ...(form.date ? { due: { date: form.date, ...(form.time ? { time: form.time } : {}) } } : {}),
        });
        toast({ tone: "ok", title: "Added", description: form.date ? "You will be reminded before it is due." : "Add a deadline so it can remind you." });
      }
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <form id="obligation-form" onSubmit={submit} className="space-y-4 pb-2" noValidate>
      <Field label="What is it?" error={error && !form.title.trim() ? error : null}>
        {(p) => <Input {...p} value={form.title} onChange={(e) => set("title", e.target.value)} maxLength={300} autoFocus placeholder="Submit signed internship documents" />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Due date">{(p) => <Input {...p} type="date" value={form.date} onChange={(e) => set("date", e.target.value)} />}</Field>
        <Field label="Time" hint={form.date ? "Leave empty for the end of that day." : undefined}>
          {(p) => <Input {...p} type="time" value={form.time} onChange={(e) => set("time", e.target.value)} />}
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Kind">
          {(p) => (
            <Select {...p} value={form.type} onChange={(e) => set("type", e.target.value as ObligationType)}>
              {(Object.keys(TYPE_LABEL) as ObligationType[]).map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Priority">
          {(p) => (
            <Select {...p} value={form.priority} onChange={(e) => set("priority", e.target.value as Priority)}>
              {(["LOW", "MEDIUM", "HIGH", "URGENT"] as Priority[]).map((v) => (
                <option key={v} value={v}>
                  {PRIORITY_LABEL[v]}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Who is involved">{(p) => <Input {...p} value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={200} placeholder="University HR" />}</Field>
        <Field label="Their email" hint={emailLocked ? "The address the original email came from. Follow-ups are sent here, so it cannot be changed." : undefined}>
          {(p) => <Input {...p} type="email" value={form.email} disabled={emailLocked} onChange={(e) => set("email", e.target.value)} placeholder="name@example.com" />}
        </Field>
      </div>
      <Field label="Repeats">
        {(p) => (
          <Select {...p} value={form.recurrence} onChange={(e) => set("recurrence", e.target.value)}>
            {RECURRENCE.map((r) => (
              <option key={r} value={r}>
                {RECURRENCE_LABEL[r]}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Notes">{(p) => <Textarea {...p} value={form.description} onChange={(e) => set("description", e.target.value)} maxLength={5000} placeholder="Anything worth remembering" />}</Field>
      {error && form.title.trim() && (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      )}
      <FooterSlot saving={saving} onCancel={onDone} isEdit={!!ob} />
    </form>
  );
}

// the footer lives inside the form so Enter submits it; Modal's own footer slot is outside the form element
function FooterSlot({ saving, onCancel, isEdit }: { saving: boolean; onCancel: () => void; isEdit: boolean }) {
  return (
    <div className="-mx-6 mt-2 flex items-center justify-end gap-2 border-t border-line px-6 pt-4">
      <Button onClick={onCancel}>Cancel</Button>
      <Button type="submit" variant="primary" loading={saving}>
        {isEdit ? "Save changes" : "Add commitment"}
      </Button>
    </div>
  );
}

/** Edit an existing commitment, or (with no `ob`) add one by hand. */
export function ObligationDialog({ open, onOpenChange, ob, emailLocked }: { open: boolean; onOpenChange: (open: boolean) => void; ob?: Obligation; emailLocked?: boolean }) {
  // a commitment found in an email keeps the address that email came from; one added by hand takes whatever it is given
  const locked = emailLocked ?? (!!ob && ob.source !== "MANUAL" && !!ob.counterparty_email);
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={ob ? "Edit commitment" : "Add a commitment"}
      description={ob ? "Changing the deadline reschedules its reminders." : "For something you promised or owe that did not arrive by email."}
    >
      <ObligationForm ob={ob} onDone={() => onOpenChange(false)} emailLocked={locked} />
    </Modal>
  );
}
