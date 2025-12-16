import Badge from "./Badge";

export type TickerItem = { symbol: string; price?: number | null; changePct?: number | null };

function fmt(n?: number | null, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export default function TickerRow({ items }: { items: TickerItem[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((it) => {
        const ch = it.changePct ?? null;
        const tone = ch === null ? "neutral" : ch >= 0 ? "good" : "bad";
        const pct = ch === null ? "—" : `${ch >= 0 ? "+" : ""}${fmt(ch, 2)}%`;
        return (
          <Badge key={it.symbol} tone={tone as any}>
            <span className="font-mono">{it.symbol}</span>
            <span className="opacity-70">${fmt(it.price, 2)}</span>
            <span>{pct}</span>
          </Badge>
        );
      })}
    </div>
  );
}

