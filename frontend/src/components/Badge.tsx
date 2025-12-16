import clsx from "clsx";
import type { ReactNode } from "react";

export default function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "bad";
}) {
  const cls =
    tone === "good"
      ? "border-emerald-300 text-emerald-700 bg-emerald-50 dark:border-emerald-800 dark:text-emerald-200 dark:bg-emerald-950"
      : tone === "bad"
        ? "border-red-300 text-red-700 bg-red-50 dark:border-red-800 dark:text-red-200 dark:bg-red-950"
        : "border-neutral-300 text-neutral-700 bg-neutral-50 dark:border-neutral-800 dark:text-neutral-200 dark:bg-neutral-950";
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs", cls)}>
      {children}
    </span>
  );
}
