import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

/** The frame shared by sign-in and registration: a heading, a sentence of context, and the form. */
export function AuthCard({ title, description, children, footer }: { title: string; description: string; children: ReactNode; footer: ReactNode }) {
  return (
    <>
      <h1 className="text-[28px] font-semibold leading-9 tracking-[-0.022em]">{title}</h1>
      <p className="mt-1.5 text-[15px] text-text-2">{description}</p>
      <Card className="mt-7 p-6">{children}</Card>
      <p className="mt-6 text-center text-[14px] text-text-2">{footer}</p>
    </>
  );
}
