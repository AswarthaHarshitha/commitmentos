"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { SystemStatus } from "./api";
import { useMe, useSystemStatus } from "./queries";

interface ClockValue {
  /** the server's "now", so relative wording ("due tomorrow") always agrees with the deadline rules */
  now: Date;
  timeZone: string;
  status: SystemStatus | undefined;
}

const ClockContext = createContext<ClockValue | null>(null);

export function ClockProvider({ children }: { children: ReactNode }) {
  const { data: status, dataUpdatedAt } = useSystemStatus();
  const { data: me } = useMe();
  // the device's wall clock, sampled on a timer; the server's time is the last reported instant plus what has elapsed since
  const [wall, setWall] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setWall(Date.now()), 15_000);
    return () => clearInterval(id);
  }, []);

  const timeZone = me?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC";
  const value = useMemo<ClockValue>(() => {
    const now = status ? new Date(Date.parse(status.now) + Math.max(0, wall - dataUpdatedAt)) : new Date(wall);
    return { now, timeZone, status };
  }, [status, dataUpdatedAt, wall, timeZone]);
  return <ClockContext.Provider value={value}>{children}</ClockContext.Provider>;
}

export function useClock(): ClockValue {
  const ctx = useContext(ClockContext);
  if (!ctx) throw new Error("useClock must be used inside <ClockProvider>");
  return ctx;
}
