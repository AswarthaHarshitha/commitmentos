import { Mail, Quote } from "lucide-react";
import type { ReactNode } from "react";
import { formatMoment } from "@/lib/time";

export interface SourceView {
  sender?: string | null;
  subject?: string | null;
  excerpt?: string | null;
  receivedAt?: string | null;
}

/** The message a commitment came from: who sent it, when, and the words that were relied on - shown verbatim. */
export function SourceQuote({ source, now, timeZone }: { source: SourceView; now: Date; timeZone: string }) {
  if (!source.sender && !source.subject && !source.excerpt) return null;
  return (
    <figure className="rounded-md border border-line bg-surface-2/60 p-4">
      <figcaption className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-text-2">
        <Mail aria-hidden className="size-4 text-text-3" strokeWidth={1.6} />
        {source.sender && <span className="font-medium text-ink">{source.sender}</span>}
        {source.receivedAt && <span data-numeric>· {formatMoment(source.receivedAt, timeZone, now)}</span>}
      </figcaption>
      {source.subject && <p className="mb-1.5 text-[14px] font-medium text-ink">{source.subject}</p>}
      {source.excerpt && (
        <blockquote className="relative pl-6 text-[14px] leading-6 text-text">
          <Quote aria-hidden className="absolute left-0 top-1 size-4 text-line-strong" strokeWidth={1.6} />
          <span className="whitespace-pre-line">{source.excerpt}</span>
        </blockquote>
      )}
    </figure>
  );
}

/** "Why this was detected", in sentences: what the message said, how the deadline was read, and how sure the detector is. */
export function WhyDetected({
  explanation,
  deadlineText,
  deadlineExplanation,
  notes,
  ambiguity,
  children,
}: {
  explanation?: string | null;
  deadlineText?: string | null;
  deadlineExplanation?: string | null;
  notes?: string[];
  ambiguity?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-3 text-[14px] leading-6 text-text">
      {explanation && <p>{explanation}</p>}
      {deadlineExplanation ? (
        <p>
          <span className="text-text-2">Deadline: </span>
          {deadlineExplanation}
        </p>
      ) : (
        deadlineText && (
          <p>
            <span className="text-text-2">Deadline wording: </span>
            <q className="font-medium text-ink">{deadlineText}</q>
          </p>
        )
      )}
      {ambiguity && <p className="rounded-md border border-warn-line bg-warn-bg px-3 py-2 text-warn">{ambiguity}</p>}
      {notes && notes.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-text-2 marker:text-line-strong">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {children}
    </div>
  );
}
