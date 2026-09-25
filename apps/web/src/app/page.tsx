import { Eye, HandHelping, ScanSearch } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Brand } from "@/components/brand";
import { HeaderActions, HeroActions } from "@/components/landing/actions";
import { ProductFlow } from "@/components/landing/flow";

export const metadata: Metadata = {
  title: { absolute: "CommitmentOS - keeps track of what you owe" },
  description: "CommitmentOS finds the promises, requests and deadlines in your email, tracks each one, and reminds you until it is done.",
};

const PRINCIPLES = [
  {
    icon: ScanSearch,
    title: "The model reads. Code decides.",
    body: "A language model turns an email into a structured commitment. Deadlines, reminders, overdue rules and escalation are ordinary, tested code, so a model's guess never becomes a missed date.",
  },
  {
    icon: HandHelping,
    title: "It asks before it acts.",
    body: "Follow-up emails and calendar events are drafted for you and wait for your approval. Nothing is sent or added on your behalf until you say so.",
  },
  {
    icon: Eye,
    title: "It shows its work.",
    body: "Every commitment explains why it was detected, quotes the message it came from, and keeps a timeline that cannot be edited afterwards.",
  },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-[1120px] items-center justify-between px-4 py-5 md:px-8">
        <Link href="/" aria-label="CommitmentOS home">
          <Brand />
        </Link>
        <HeaderActions />
      </header>

      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        <section className="mx-auto w-full max-w-[1120px] px-4 pb-14 pt-10 md:px-8 md:pb-20 md:pt-16">
          <p className="eyebrow mb-4">For what you promised, owe, or need to remember</p>
          <h1 className="max-w-3xl text-[38px] font-semibold leading-[1.08] tracking-[-0.03em] text-ink md:text-[56px]">
            Don&rsquo;t remember everything. Let CommitmentOS remember what you owe.
          </h1>
          <p className="mt-6 max-w-2xl text-[17px] leading-7 text-text-2 md:text-[18px]">
            It finds the requests, promises and deadlines in your email, tracks each one to the minute, and reminds you until it is done.
          </p>
          <HeroActions className="mt-8" />
        </section>

        <section aria-labelledby="flow-heading" className="border-y border-line bg-surface-2/60">
          <div className="mx-auto w-full max-w-[1120px] px-4 py-12 md:px-8 md:py-16">
            <h2 id="flow-heading" className="mb-2 text-[22px] font-semibold tracking-[-0.015em]">
              From an email to a kept promise
            </h2>
            <p className="mb-8 max-w-2xl text-[15px] text-text-2">One message, followed all the way through.</p>
            <ProductFlow />
          </div>
        </section>

        <section aria-labelledby="principles-heading" className="mx-auto w-full max-w-[1120px] px-4 py-14 md:px-8 md:py-20">
          <h2 id="principles-heading" className="mb-10 max-w-2xl text-[26px] font-semibold leading-8 tracking-[-0.02em] md:text-[30px] md:leading-9">
            Built so you can trust what it tells you
          </h2>
          <div className="grid gap-x-10 gap-y-9 md:grid-cols-3">
            {PRINCIPLES.map(({ icon: Icon, title, body }) => (
              <div key={title}>
                <Icon aria-hidden className="mb-3 size-5 text-ink" strokeWidth={1.6} />
                <h3 className="text-[16px] font-semibold text-ink">{title}</h3>
                <p className="mt-1.5 text-[14.5px] leading-6 text-text-2">{body}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center justify-between gap-3 px-4 py-6 text-[13px] text-text-2 md:px-8">
          <Brand />
        </div>
      </footer>
    </div>
  );
}
