export function formatAgentName(agentId?: string | null): string {
  const id = (agentId || "").trim();
  if (!id) return "—";

  const lower = id.toLowerCase();
  if (lower === "manager" || lower.startsWith("manager_")) return "Manager";

  const m1 = /^tech_trader_(\d+)$/i.exec(id);
  if (m1) return `Technical Agent ${m1[1]}`;
  if (/^macro_trader_\d+$/i.test(id)) return "Macro Agent";
  if (/^structure_trader_\d+$/i.test(id)) return "Structural Agent";

  return id
    .replace(/[_-]+/g, " ")
    .replace(/\b([a-z])/g, (s) => s.toUpperCase())
    .trim();
}
