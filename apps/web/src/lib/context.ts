import type { Obligation } from "./api";
import { SOURCE_LABEL } from "./labels";

/** "Requested by University HR", "You promised Sam", "Waiting on Marcus Lee": who this commitment involves, in one line. */
export function contextLine(ob: Pick<Obligation, "owner" | "obligation_type" | "counterparty_name" | "counterparty_email" | "source">): string {
  const who = ob.counterparty_name || ob.counterparty_email;
  if (ob.owner !== "me") return `Waiting on ${ob.owner}`;
  if (ob.obligation_type === "PERSONAL_COMMITMENT") return who ? `You promised ${who}` : "You promised this";
  if (ob.source === "MANUAL") return "Added by you";
  if (who) return `Requested by ${who}`;
  return `From ${SOURCE_LABEL[ob.source] ?? "a message"}`;
}

/** A plain-language next step for the cards; the detail page shows the server's own suggestion. */
export function nextStep(ob: Pick<Obligation, "status" | "owner" | "requires_confirmation" | "counterparty_name" | "counterparty_email">): string | null {
  const who = ob.counterparty_name || ob.counterparty_email;
  switch (ob.status) {
    case "NEEDS_REVIEW":
      return "Review it, then accept or dismiss";
    case "OVERDUE":
    case "ESCALATED":
      return ob.owner !== "me" ? `Follow up${who ? ` with ${who}` : ""}` : "Complete it, or move the deadline";
    case "ACTION_REQUIRED":
      return ob.requires_confirmation ? "Reply to confirm" : "This needs your action";
    default:
      return null;
  }
}
