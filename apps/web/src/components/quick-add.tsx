"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ObligationDialog } from "@/components/edit-obligation-dialog";
import { ImportEmailDialog } from "@/components/import-email-dialog";

interface QuickAdd {
  addCommitment: () => void;
  importEmail: () => void;
}

const QuickAddContext = createContext<QuickAdd | null>(null);

/** The two ways to bring something in by hand, reachable from anywhere in the app (top bar, empty states). */
export function QuickAddProvider({ children }: { children: ReactNode }) {
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const addCommitment = useCallback(() => setAdding(true), []);
  const importEmail = useCallback(() => setImporting(true), []);
  const value = useMemo(() => ({ addCommitment, importEmail }), [addCommitment, importEmail]);
  return (
    <QuickAddContext.Provider value={value}>
      {children}
      <ObligationDialog open={adding} onOpenChange={setAdding} />
      <ImportEmailDialog open={importing} onOpenChange={setImporting} />
    </QuickAddContext.Provider>
  );
}

export function useQuickAdd(): QuickAdd {
  const ctx = useContext(QuickAddContext);
  if (!ctx) throw new Error("useQuickAdd must be used inside <QuickAddProvider>");
  return ctx;
}
