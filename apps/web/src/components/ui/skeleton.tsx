import { cn } from "@/lib/cn";

/** Placeholder blocks breathe (a soft fade) instead of sweeping a gradient across the page. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-breathe rounded-md bg-line/80", className)} />;
}

/** Rows shaped like the content that is coming, with a short line saying what is being fetched (also what a screen reader announces). */
export function SkeletonRows({ rows = 4, message = "Loading" }: { rows?: number; message?: string }) {
  return (
    <div role="status" aria-label={message}>
      <div className="divide-y divide-line">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-start gap-3 px-5 py-4">
            <Skeleton className="mt-0.5 size-5 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-2/5" />
              <Skeleton className="h-3.5 w-3/5" />
              <Skeleton className="h-3.5 w-1/4" />
            </div>
          </div>
        ))}
      </div>
      {message !== "Loading" && <p className="border-t border-line px-5 py-3 text-[13px] text-text-3">{message}</p>}
    </div>
  );
}
