import { useSyncExternalStore } from "react";

/**
 * A commitment that was just completed stays in its list for a moment, drawn as done, before the list refreshes and closes the gap.
 * Refreshing at once would remove the row the instant it was ticked, and the completion would never be seen.
 */
const settling = new Set<string>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

export const SETTLE_MS = 1100;

export function startSettling(id: string, ms: number, then: () => void): void {
  settling.add(id);
  emit();
  setTimeout(() => {
    settling.delete(id);
    emit();
    then();
  }, ms);
}

export function stopSettling(id: string): void {
  if (settling.delete(id)) emit();
}

export function useSettling(id: string): boolean {
  return useSyncExternalStore(subscribe, () => settling.has(id), () => false);
}
