"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface ToastInput {
  title: string;
  description?: string;
  tone?: "neutral" | "ok" | "danger";
  action?: { label: string; onClick: () => void };
  /** ms; 0 keeps it until dismissed */
  duration?: number;
}
interface ToastItem extends ToastInput {
  id: number;
}

const ToastContext = createContext<{ toast: (input: ToastInput) => void } | null>(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx.toast;
}

const icons = { neutral: Info, ok: CheckCircle2, danger: CircleAlert };
const iconTone = { neutral: "text-text-2", ok: "text-ok", danger: "text-danger" };

function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const [paused, setPaused] = useState(false);
  const Icon = icons[item.tone ?? "neutral"];
  useEffect(() => {
    if (paused || item.duration === 0) return;
    const timer = setTimeout(() => onDismiss(item.id), item.duration ?? (item.action ? 7000 : 4500));
    return () => clearTimeout(timer);
  }, [paused, item, onDismiss]);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 6, transition: { duration: 0.15 } }}
      transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
      role={item.tone === "danger" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border border-line-strong bg-surface p-3.5 shadow-pop"
    >
      <Icon aria-hidden className={cn("mt-0.5 size-[18px] shrink-0", iconTone[item.tone ?? "neutral"])} strokeWidth={1.8} />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-medium text-ink">{item.title}</p>
        {item.description && <p className="mt-0.5 text-[13px] text-text-2">{item.description}</p>}
      </div>
      {item.action && (
        <button
          type="button"
          onClick={() => {
            item.action?.onClick();
            onDismiss(item.id);
          }}
          className="shrink-0 rounded-md px-2 py-1 text-[13px] font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
        >
          {item.action.label}
        </button>
      )}
      <button type="button" aria-label="Dismiss" onClick={() => onDismiss(item.id)} className="-mr-1 shrink-0 rounded-md p-1 text-text-3 hover:bg-surface-2 hover:text-ink">
        <X className="size-4" />
      </button>
    </motion.div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const dismiss = useCallback((id: number) => setItems((list) => list.filter((t) => t.id !== id)), []);
  const toast = useCallback((input: ToastInput) => {
    counter.current += 1;
    setItems((list) => [...list.slice(-2), { ...input, id: counter.current }]);
  }, []);
  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex flex-col items-center gap-2 px-4 md:bottom-6 md:items-end md:px-6">
        <AnimatePresence initial={false}>
          {items.map((item) => (
            <Toast key={item.id} item={item} onDismiss={dismiss} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
