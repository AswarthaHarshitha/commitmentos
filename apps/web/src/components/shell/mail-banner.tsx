"use client";

import { MailWarning } from "lucide-react";
import Link from "next/link";
import { useClock } from "@/lib/clock";

/**
 * While email goes to the bundled local inbox, nothing reaches a real mailbox. That is easy to miss - every step reports success -
 * so it is said at the top of every page until real email delivery is set up.
 */
export function MailBanner() {
  const { status } = useClock();
  if (status?.email_delivery !== "local_test_inbox") return null;
  const inbox = `${window.location.protocol}//${window.location.hostname}:8025`;
  return (
    <div role="note" className="mb-6 flex items-start gap-3 rounded-lg border border-warn-line bg-warn-bg px-4 py-3 text-[14px] leading-5 text-warn">
      <MailWarning aria-hidden className="mt-0.5 size-[18px] shrink-0" strokeWidth={1.7} />
      <p>
        <span className="font-medium">Email is not being delivered.</span> Reminders and follow-ups are caught by a{" "}
        <a href={inbox} target="_blank" rel="noreferrer noopener" className="font-medium underline underline-offset-4">
          local test inbox
        </a>{" "}
        on this computer, so nothing reaches a real mailbox yet.{" "}
        <Link href="/settings#email" className="font-medium underline underline-offset-4">
          Set up email delivery
        </Link>
      </p>
    </div>
  );
}
