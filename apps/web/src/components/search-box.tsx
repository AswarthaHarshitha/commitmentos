"use client";

import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/field";

export function SearchBox({ value, onChange, label = "Search commitments", placeholder = "Search by title or detail" }: { value: string; onChange: (v: string) => void; label?: string; placeholder?: string }) {
  return (
    <div role="search" className="relative w-full sm:w-72">
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-3" strokeWidth={1.7} />
      <Input type="search" aria-label={label} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} className="pl-9 pr-8 [&::-webkit-search-cancel-button]:hidden" />
      {value && (
        <button type="button" aria-label="Clear search" onClick={() => onChange("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-text-3 hover:bg-surface-2 hover:text-ink">
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
