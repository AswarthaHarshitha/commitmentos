"use client";

import { Check, Pencil, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { TypeIcon } from "@/components/commitment-card";
import { ObligationDialog } from "@/components/edit-obligation-dialog";
import { SourceQuote, WhyDetected } from "@/components/understanding";
import { Button } from "@/components/ui/button";
import { DeadlineIndicator } from "@/components/ui/deadline-indicator";
import { Disclosure } from "@/components/ui/disclosure";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useDecisions } from "@/lib/actions";
import { errorMessage, type Candidate, type Obligation } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { contextLine } from "@/lib/context";
import { useCandidateDecision, useObligation, useObligationAction } from "@/lib/queries";
import { describeDeadline, timeAgo } from "@/lib/time";

/** "Possible commitment · Confidence 68% · Review required": how sure the detector was, and why a person is being asked. */
function Verdict({ confidence, reason }: { confidence: number; reason: string }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-[12.5px] text-text-2">
      <span>Possible commitment</span>
      <span aria-hidden>·</span>
      <span>
        Confidence{" "}
        <span data-numeric className="font-medium text-ink">
          {Math.round(confidence * 100)}%
        </span>
      </span>
      <span aria-hidden>·</span>
      <span className="font-medium text-warn">{reason}</span>
    </p>
  );
}

function ObligationWhy({ id }: { id: string }) {
  const { now, timeZone } = useClock();
  const [asked, setAsked] = useState(false);
  const { data, error } = useObligation(id, { enabled: asked });
  const source = data?.sources[0];
  return (
    <Disclosure label="Why this was detected" onToggle={(open) => open && setAsked(true)}>
      {!data ? (
        error ? (
          <p className="text-[13px] text-danger">Could not load the explanation.</p>
        ) : (
          <div role="status" aria-label="Loading the explanation" className="space-y-2 py-1">
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-3/5" />
          </div>
        )
      ) : (
        <div className="space-y-3 rounded-md border border-line bg-surface p-4">
          <WhyDetected
            explanation={data.understanding.explanation}
            deadlineText={data.understanding.deadline_text}
            deadlineExplanation={data.understanding.deadline_explanation}
            notes={data.understanding.confidence_notes}
            ambiguity={data.understanding.ambiguity}
          />
          {source && (
            <SourceQuote
              now={now}
              timeZone={timeZone}
              source={{ sender: source.sender_name || source.sender_email, subject: source.subject, excerpt: source.excerpt, receivedAt: source.received_at }}
            />
          )}
        </div>
      )}
    </Disclosure>
  );
}

/** A detection that needs a person: accept it, fix it, or dismiss it. */
export function ReviewItem({ ob }: { ob: Obligation }) {
  const { now, timeZone } = useClock();
  const { accept, dismiss, busyId } = useDecisions();
  const [editing, setEditing] = useState(false);
  const due = describeDeadline({ dueAt: ob.due_at, precision: ob.due_precision, timeZone, now });
  const busy = busyId === ob.id;

  return (
    <li className="px-4 py-5 sm:px-5" data-testid="review-item">
      <Verdict confidence={ob.confidence} reason={!ob.due_at ? "No clear deadline" : ob.ambiguity ? "Deadline needs checking" : "Review required"} />
      <h3 className="mt-1.5 text-[16px] font-medium leading-6 text-ink">
        <Link href={`/obligations/${ob.id}`} className="decoration-line-strong underline-offset-4 hover:underline">
          {ob.title}
        </Link>
      </h3>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[13.5px] text-text-2">
        <TypeIcon type={ob.obligation_type} />
        <span>{contextLine(ob)}</span>
        <span aria-hidden>·</span>
        <span>{timeAgo(ob.created_at, now, timeZone)}</span>
      </p>
      <div className="mt-2.5">
        <DeadlineIndicator info={due} />
        {ob.ambiguity && <p className="mt-1 text-[13px] text-warn">{ob.ambiguity}</p>}
      </div>
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" icon={<Check aria-hidden className="size-4" strokeWidth={2} />} loading={busy} onClick={() => accept(ob)}>
          Accept
        </Button>
        <Button size="sm" icon={<Pencil aria-hidden className="size-3.5" strokeWidth={1.8} />} onClick={() => setEditing(true)} disabled={busy}>
          Edit
        </Button>
        <Button size="sm" variant="ghost" icon={<X aria-hidden className="size-4" strokeWidth={1.8} />} onClick={() => dismiss(ob)} disabled={busy}>
          Dismiss
        </Button>
      </div>
      <ObligationWhy id={ob.id} />
      <ObligationDialog open={editing} onOpenChange={setEditing} ob={ob} />
    </li>
  );
}

/** A low-confidence detection the system did not create on its own. "Track it" creates the commitment and accepts it in one go. */
export function CandidateItem({ candidate }: { candidate: Candidate }) {
  const { now, timeZone } = useClock();
  const toast = useToast();
  const decide = useCandidateDecision();
  const accept = useObligationAction("approve");
  const [busy, setBusy] = useState<"track" | "discard" | null>(null);
  const due = describeDeadline({ dueAt: candidate.due_at, precision: null, timeZone, now });

  async function track() {
    setBusy("track");
    try {
      const created = await decide.mutateAsync({ id: candidate.id, decision: "promote" });
      // promoting only creates the commitment for review; the person already said yes, so accept it too
      if (created && typeof created === "object" && "id" in created) await accept.mutateAsync({ id: String(created.id) }).catch(() => undefined);
      toast({ tone: "ok", title: "Tracking it", description: "You will be reminded before it is due." });
    } catch (error) {
      toast({ tone: "danger", title: "Could not add it", description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }
  async function discard() {
    setBusy("discard");
    try {
      await decide.mutateAsync({ id: candidate.id, decision: "discard" });
      toast({ title: "Not a commitment", description: "It has been set aside." });
    } catch (error) {
      toast({ tone: "danger", title: "Could not discard it", description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="px-4 py-5 sm:px-5" data-testid="candidate-item">
      <Verdict confidence={candidate.confidence} reason="Not sure this is a commitment" />
      <h3 className="mt-1.5 text-[16px] font-medium leading-6 text-ink">{candidate.title}</h3>
      <p className="mt-0.5 text-[13.5px] text-text-2">
        {candidate.sender_email ? `From ${candidate.sender_email}` : "From a message"} · {timeAgo(candidate.created_at, now, timeZone)}
      </p>
      {candidate.due_at && (
        <div className="mt-2.5">
          <DeadlineIndicator info={due} />
        </div>
      )}
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" icon={<Check aria-hidden className="size-4" strokeWidth={2} />} loading={busy === "track"} disabled={busy !== null} onClick={track}>
          Track it
        </Button>
        <Button size="sm" variant="ghost" icon={<X aria-hidden className="size-4" strokeWidth={1.8} />} loading={busy === "discard"} disabled={busy !== null} onClick={discard}>
          Not a commitment
        </Button>
      </div>
      <Disclosure label="Why this was flagged">
        <div className="space-y-3 rounded-md border border-line bg-surface p-4">
          <WhyDetected explanation={candidate.explanation} deadlineText={candidate.deadline_text} notes={[candidate.reason]} />
          <SourceQuote now={now} timeZone={timeZone} source={{ sender: candidate.sender_email, subject: candidate.subject, excerpt: candidate.excerpt, receivedAt: candidate.received_at }} />
        </div>
      </Disclosure>
    </li>
  );
}
