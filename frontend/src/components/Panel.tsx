import clsx from "clsx";
import type { ReactNode } from "react";

export default function Panel({
  title,
  right,
  children,
  className,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--panel))] shadow-crisp", className)}>
      <header className="flex items-center justify-between border-b border-[rgb(var(--border))] px-4 py-3">
        <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">{title}</div>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
