import type { ReactNode } from "react";

export function PageHeader({ title, description, actions, eyebrow }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: string }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 md:mb-8">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <h1 className="text-[26px] font-semibold leading-8 tracking-[-0.02em] md:text-[30px] md:leading-9">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-[15px] text-text-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function SectionLabel({ children, count, tone = "neutral" }: { children: ReactNode; count?: number; tone?: "neutral" | "danger" }) {
  return (
    <h2 className="flex items-center gap-2 px-1 pb-2 pt-1 text-[13px] font-semibold text-ink">
      <span className={tone === "danger" ? "text-danger" : undefined}>{children}</span>
      {count !== undefined && (
        <span data-numeric className="rounded-full bg-surface-2 px-1.5 text-[12px] font-medium leading-5 text-text-2 ring-1 ring-line">
          {count}
        </span>
      )}
    </h2>
  );
}
