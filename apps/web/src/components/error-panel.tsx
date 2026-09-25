"use client";

import { TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { errorMessage } from "@/lib/api";

/** A failed load says what happened in plain words and offers the one thing that can help. */
export function ErrorPanel({ error, onRetry, title = "Couldn't load this" }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <Card role="alert" className="flex items-start gap-3.5 border-danger-line bg-danger-bg/40 p-5">
      <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-danger" strokeWidth={1.7} />
      <div className="min-w-0 flex-1">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        <p className="mt-0.5 text-[14px] text-text-2">{errorMessage(error)}</p>
        {onRetry && (
          <Button size="sm" className="mt-3" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    </Card>
  );
}
