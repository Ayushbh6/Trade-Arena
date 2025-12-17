import type { AuditEvent } from "../api/types";
import { formatAgentName } from "../utils/format";

interface EventFeedProps {
  events: AuditEvent[];
}

export default function EventFeed({ events }: EventFeedProps) {
  return (
    <section className="flex h-[420px] flex-col rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--panel))] shadow-crisp sm:h-[480px] lg:h-[520px]">
      <header className="border-b border-[rgb(var(--border))] px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">Live Activity</div>
          <div className="text-xs text-[rgb(var(--muted))]">stream</div>
        </div>
        <div className="mt-1 text-xs text-[rgb(var(--muted))]">Real-time proposals, manager decisions, execution, and P&amp;L.</div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-2">
        {events.length === 0 ? (
          <div className="grid h-full place-items-center text-center text-sm text-[rgb(var(--muted))]">
            No activity yet. Start a run to see live updates.
          </div>
        ) : (
          events.map((event) => (
            <EventItem key={event._id || `${event.timestamp}:${event.event_type}`} event={event} />
          ))
        )}
      </div>
    </section>
  );
}

function EventItem({ event }: { event: AuditEvent }) {
  const time = new Date(event.timestamp).toLocaleTimeString();
  const meta = classify(event.event_type);
  const actor = formatAgentName(event.agent_id || meta.defaultActor);
  const title = meta.title || humanize(event.event_type);
  const snippet = extractSnippet(event);

  return (
    <div className={`relative overflow-hidden rounded-lg border bg-[rgb(var(--bg))] p-3 ${meta.borderClass}`}>
      <div className={`absolute left-0 top-0 h-full w-1 ${meta.accentClass}`} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[rgb(var(--fg))]">
            {actor} <span className="text-[rgb(var(--muted))]">·</span> {title}
          </div>
          <div className="mt-1 text-xs font-mono text-[rgb(var(--muted))]">{event.event_type}</div>
        </div>
        <div className="shrink-0 text-xs font-mono text-[rgb(var(--muted))]">{time}</div>
      </div>
      {snippet ? (
        <div className="mt-2 break-words text-xs text-[rgb(var(--muted))] line-clamp-3">
          {snippet}
        </div>
      ) : null}
    </div>
  );
}

function classify(eventType: string): {
  defaultActor: string;
  title?: string;
  borderClass: string;
  accentClass: string;
} {
  const t = String(eventType || "");
  if (t.includes("trader_") || t.includes("proposal")) {
    return {
      defaultActor: "trader",
      borderClass: "border-blue-200/70",
      accentClass: "bg-blue-500/70",
    };
  }
  if (t.includes("manager")) {
    return {
      defaultActor: "manager",
      borderClass: "border-amber-200/70",
      accentClass: "bg-amber-500/70",
    };
  }
  if (t.includes("execution") || t.includes("order") || t.includes("fill")) {
    return {
      defaultActor: "execution",
      borderClass: "border-emerald-200/70",
      accentClass: "bg-emerald-500/70",
    };
  }
  if (t.includes("error") || t.includes("fail")) {
    return {
      defaultActor: "system",
      borderClass: "border-red-200/70",
      accentClass: "bg-red-500/70",
    };
  }
  return {
    defaultActor: "system",
    borderClass: "border-[rgb(var(--border))]",
    accentClass: "bg-[rgb(var(--border))]",
  };
}

function humanize(s: string): string {
  return String(s || "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function extractSnippet(event: AuditEvent): string | null {
  const payload: any = (event as any).payload || {};
  const notes = typeof payload.notes === "string" ? payload.notes : null;
  if (notes) return notes;
  const error = typeof payload.error === "string" ? payload.error : null;
  if (error) return error;
  if (payload.decision && typeof payload.decision.notes === "string") return payload.decision.notes;
  if (payload.report) return "Execution report available.";
  if (payload.proposals && Array.isArray(payload.proposals) && payload.proposals.length) {
    const p0 = payload.proposals[0] || {};
    if (typeof p0.notes === "string") return p0.notes;
  }
  return null;
}
