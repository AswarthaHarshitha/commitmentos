"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { useToast } from "@/components/ui/toast";
import { errorMessage, type Obligation } from "./api";
import { useObligationAction } from "./queries";
import { SETTLE_MS, stopSettling } from "./settling";

/** Complete a commitment (with an Undo that reopens it) and report the outcome the way a person would say it. */
export function useCompleteWithUndo() {
  const toast = useToast();
  const complete = useObligationAction("complete", { settleMs: SETTLE_MS });
  const reopen = useObligationAction("reopen");
  const [busyId, setBusyId] = useState<string | null>(null);

  const run = useCallback(
    async (ob: Pick<Obligation, "id" | "title">) => {
      setBusyId(ob.id);
      try {
        await complete.mutateAsync({ id: ob.id });
        toast({
          tone: "ok",
          title: "Marked complete",
          description: "Its reminders have stopped.",
          action: {
            label: "Undo",
            onClick: () => {
              stopSettling(ob.id);
              reopen.mutate({ id: ob.id }, { onError: (e) => toast({ tone: "danger", title: "Could not undo", description: errorMessage(e) }) });
            },
          },
        });
        return true;
      } catch (error) {
        toast({ tone: "danger", title: "Could not complete it", description: errorMessage(error) });
        return false;
      } finally {
        setBusyId(null);
      }
    },
    [complete, reopen, toast],
  );
  return { complete: run, busyId };
}

export function useOpenObligation() {
  const router = useRouter();
  return useCallback((id: string) => router.push(`/obligations/${id}`), [router]);
}

/** Accept and dismiss for detections in the inbox, with the outcome said the way a person would say it. */
export function useDecisions() {
  const toast = useToast();
  const accept = useObligationAction("approve");
  const dismiss = useObligationAction("dismiss");
  const reopen = useObligationAction("reopen");
  const [busyId, setBusyId] = useState<string | null>(null);

  const guard = useCallback(async (id: string, work: () => Promise<void>, failure: string) => {
    setBusyId(id);
    try {
      await work();
      return true;
    } catch (error) {
      toast({ tone: "danger", title: failure, description: errorMessage(error) });
      return false;
    } finally {
      setBusyId(null);
    }
  }, [toast]);

  return {
    busyId,
    accept: (ob: Pick<Obligation, "id" | "title">) =>
      guard(ob.id, async () => {
        await accept.mutateAsync({ id: ob.id });
        toast({ tone: "ok", title: "Tracking it", description: "You will be reminded before it is due." });
      }, "Could not accept it"),
    dismiss: (ob: Pick<Obligation, "id" | "title">) =>
      guard(ob.id, async () => {
        await dismiss.mutateAsync({ id: ob.id });
        toast({
          title: "Dismissed",
          description: "It will not be tracked.",
          action: { label: "Undo", onClick: () => reopen.mutate({ id: ob.id }, { onError: (e) => toast({ tone: "danger", title: "Could not undo", description: errorMessage(e) }) }) },
        });
      }, "Could not dismiss it"),
  };
}
