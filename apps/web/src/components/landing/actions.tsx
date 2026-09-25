"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useMe } from "@/lib/queries";
import { cn } from "@/lib/cn";

const primary = "inline-flex h-11 items-center justify-center gap-2 rounded-md bg-ink px-5 text-[15px] font-medium text-surface transition-colors hover:bg-[#3a362f] active:translate-y-px";
const secondary = "inline-flex h-11 items-center justify-center rounded-md border border-line-strong bg-surface px-5 text-[15px] font-medium text-ink transition-colors hover:bg-surface-2 active:translate-y-px";

/** The calls to action, aware of whether someone is already signed in (the landing page itself makes no other request). */
export function HeroActions({ className }: { className?: string }) {
  const { data: me } = useMe();
  return (
    <div className={cn("flex flex-wrap items-center gap-3", className)}>
      {me ? (
        <Link href="/overview" className={primary}>
          Open your overview <ArrowRight aria-hidden className="size-4" />
        </Link>
      ) : (
        <>
          <Link href="/register" className={primary}>
            Create an account
          </Link>
          <Link href="/login" className={secondary}>
            Sign in
          </Link>
        </>
      )}
    </div>
  );
}

export function HeaderActions() {
  const { data: me } = useMe();
  return (
    <nav aria-label="Account" className="flex items-center gap-1.5">
      {me ? (
        <Link href="/overview" className="inline-flex h-9 items-center rounded-md bg-ink px-3.5 text-[14px] font-medium text-surface hover:bg-[#3a362f]">
          Open overview
        </Link>
      ) : (
        <>
          <Link href="/login" className="inline-flex h-9 items-center rounded-md px-3.5 text-[14px] font-medium text-text-2 hover:bg-surface-2 hover:text-ink">
            Sign in
          </Link>
          <Link href="/register" className="inline-flex h-9 items-center rounded-md bg-ink px-3.5 text-[14px] font-medium text-surface hover:bg-[#3a362f]">
            Create account
          </Link>
        </>
      )}
    </nav>
  );
}
