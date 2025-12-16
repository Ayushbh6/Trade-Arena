import clsx from "clsx";

export default function KpiCard({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "good" | "bad";
}) {
  const border =
    tone === "good" ? "border-emerald-300 dark:border-emerald-900" : tone === "bad" ? "border-red-300 dark:border-red-900" : "border-[rgb(var(--border))]";
  return (
    <div className={clsx("rounded-lg border bg-[rgb(var(--panel))] p-3 shadow-crisp", border)}>
      <div className="text-[11px] uppercase tracking-widest text-[rgb(var(--muted))]">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub ? <div className="mt-1 text-xs text-[rgb(var(--muted))]">{sub}</div> : null}
    </div>
  );
}

