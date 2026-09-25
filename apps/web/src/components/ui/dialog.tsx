"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import type { ReactNode } from "react";

/** A modal dialog: focus is trapped, Escape closes, focus returns to the trigger (Radix). */
export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <RadixDialog.Portal forceMount>
            <RadixDialog.Overlay asChild forceMount>
              <motion.div className="fixed inset-0 z-40 bg-ink/35" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} />
            </RadixDialog.Overlay>
            <div className="pointer-events-none fixed inset-0 z-50 grid place-items-end p-0 sm:place-items-center sm:p-4">
              <RadixDialog.Content asChild forceMount>
                <motion.div
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 8, transition: { duration: 0.14 } }}
                  transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
                  className="pointer-events-auto w-full max-w-lg rounded-t-xl border border-line-strong bg-surface shadow-pop sm:rounded-xl"
                >
                  <div className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
                    <div>
                      <RadixDialog.Title className="text-[17px] font-semibold text-ink">{title}</RadixDialog.Title>
                      {description ? (
                        <RadixDialog.Description className="mt-1 text-[14px] text-text-2">{description}</RadixDialog.Description>
                      ) : (
                        <RadixDialog.Description className="sr-only">{title}</RadixDialog.Description>
                      )}
                    </div>
                    <RadixDialog.Close aria-label="Close" className="-mr-2 -mt-1 rounded-md p-1.5 text-text-3 hover:bg-surface-2 hover:text-ink">
                      <X className="size-[18px]" />
                    </RadixDialog.Close>
                  </div>
                  {children && <div className="max-h-[70dvh] overflow-y-auto px-6 py-3">{children}</div>}
                  {footer && <div className="flex items-center justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>}
                </motion.div>
              </RadixDialog.Content>
            </div>
          </RadixDialog.Portal>
        )}
      </AnimatePresence>
    </RadixDialog.Root>
  );
}
