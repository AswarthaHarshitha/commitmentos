"use client";

import { motion, type Variants } from "framer-motion";
import { BellRing, Mail, ScanText, Timer } from "lucide-react";
import type { ReactNode } from "react";
import { CompletionCheck } from "@/components/ui/completion-check";

const list: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.16, delayChildren: 0.05 } } };
const item: Variants = { hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0, transition: { duration: 0.42, ease: [0.2, 0.7, 0.2, 1] } } };

const STEPS: { title: string; body: string; icon: ReactNode }[] = [
  { title: "Email arrives", body: "From your Gmail, or an email you paste in.", icon: <Mail aria-hidden className="size-[18px]" strokeWidth={1.6} /> },
  {
    title: "Commitment detected",
    body: "A language model reads it and proposes the commitment, quoting the exact words it relied on.",
    icon: <ScanText aria-hidden className="size-[18px]" strokeWidth={1.6} />,
  },
  {
    title: "Deadline tracked",
    body: "Code, not the model, turns “by Friday 5 PM” into a moment in your own time zone.",
    icon: <Timer aria-hidden className="size-[18px]" strokeWidth={1.6} />,
  },
  {
    title: "Reminder",
    body: "You are reminded ahead of the deadline, step by step, and told the moment it is missed.",
    icon: <BellRing aria-hidden className="size-[18px]" strokeWidth={1.6} />,
  },
  {
    title: "Completed",
    body: "Mark it done and the reminders stop. The timeline keeps every step.",
    icon: <CompletionCheck checked disabled label="Completed" onToggle={() => {}} />,
  },
];

/** The whole idea in one glance, written out: a message becomes a commitment with a deadline, is reminded about, and is closed. */
export function ProductFlow() {
  return (
    <motion.ol
      aria-label="How a message becomes a completed commitment"
      variants={list}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-60px" }}
      className="grid gap-3 md:grid-cols-2 lg:grid-cols-5"
    >
      {STEPS.map((step, i) => (
        <motion.li key={step.title} variants={item} className="flex flex-col rounded-lg border border-line bg-surface p-5 shadow-card">
          <div className="flex items-center justify-between text-text-2">
            <span className="grid size-6 place-items-center">{step.icon}</span>
            <span data-numeric aria-hidden className="text-[12.5px] text-text-3">
              {i + 1} / {STEPS.length}
            </span>
          </div>
          <h3 className="mt-4 text-[15px] font-semibold text-ink">{step.title}</h3>
          <p className="mt-1.5 text-[14px] leading-5 text-text-2">{step.body}</p>
        </motion.li>
      ))}
    </motion.ol>
  );
}
