"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Timeline, TimelineItem } from "@/components/ui/timeline";
import type { TimelineEntry } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { actorLabel, eventStyle } from "@/lib/events";
import { formatMoment } from "@/lib/time";

const COLLAPSED_PAST = 8;
const keyOf = (e: TimelineEntry) => `${e.kind}|${e.event_type}|${e.at}|${e.message}`;

/**
 * What happened and what will happen, oldest first. A step and the things it caused are joined: a reminder that came due,
 * then the notification queued and sent because of it, read as "this, therefore that". Planned steps are dashed.
 */
export function TimelineView({ entries }: { entries: TimelineEntry[] }) {
  const { now, timeZone } = useClock();
  const [showAll, setShowAll] = useState(false);
  // what was here when the page opened; anything that shows up later (a completion, a reminder sent) eases in as it mounts
  const [initial] = useState(() => new Set(entries.map(keyOf)));

  const past = useMemo(() => entries.filter((e) => e.kind !== "planned"), [entries]);
  const planned = useMemo(() => entries.filter((e) => e.kind === "planned"), [entries]);
  const hidden = showAll ? 0 : Math.max(0, past.length - COLLAPSED_PAST);
  const shown = [...past.slice(hidden), ...planned];

  return (
    <div>
      {hidden > 0 && (
        <div className="mb-4">
          <Button size="sm" variant="ghost" onClick={() => setShowAll(true)}>
            Show {hidden} earlier {hidden === 1 ? "event" : "events"}
          </Button>
        </div>
      )}
      <Timeline label="History of this commitment">
        {shown.map((entry, i) => {
          const planned = entry.kind === "planned";
          const style = eventStyle(entry.event_type, planned);
          const when = formatMoment(entry.at, timeZone, now);
          const previous = shown[i - 1];
          // an effect only hangs off a step when it directly follows one
          const effect = style.role === "effect" && !!previous;
          return (
            <TimelineItem
              key={keyOf(entry)}
              icon={style.icon}
              tone={style.tone}
              planned={planned}
              effect={effect}
              last={i === shown.length - 1}
              animateIn={!initial.has(keyOf(entry))}
              title={entry.message}
              meta={
                planned ? (
                  <span data-numeric>Planned · {when}</span>
                ) : (
                  <span data-numeric>
                    {when} · {actorLabel(entry.actor)}
                  </span>
                )
              }
            />
          );
        })}
      </Timeline>
    </div>
  );
}
