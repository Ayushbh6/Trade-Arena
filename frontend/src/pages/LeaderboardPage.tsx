import { useEffect, useMemo, useState } from "react";
import Panel from "../components/Panel";
import { useApi } from "../api/http";
import type { AgentState } from "../api/types";
import { formatAgentName } from "../utils/format";

function safeNum(x: any): number | null {
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : null;
}

export default function LeaderboardPage() {
  const { request } = useApi();
  const [agents, setAgents] = useState<AgentState[]>([]);

  useEffect(() => {
    request<{ agents: AgentState[] }>("/agents")
      .then((r) => setAgents(r.agents || []))
      .catch(() => setAgents([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = useMemo(() => {
    return agents
      .slice()
      .sort((a, b) => (safeNum(b.trust_score) ?? 0) - (safeNum(a.trust_score) ?? 0))
      .map((a, idx) => ({ ...a, rank: idx + 1 }));
  }, [agents]);

  return (
    <Panel title="Leaderboard">
      <div className="overflow-auto rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="sticky top-0 bg-[rgb(var(--bg))]">
            <tr className="border-b border-[rgb(var(--border))] text-xs uppercase tracking-widest text-[rgb(var(--muted))]">
              <th className="px-3 py-2">Rank</th>
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Budget</th>
              <th className="px-3 py-2">Trust</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent_id} className="border-b border-[rgb(var(--border))]">
                <td className="px-3 py-2 font-mono">{r.rank}</td>
                <td className="px-3 py-2 font-semibold">{formatAgentName(r.agent_id)}</td>
                <td className="px-3 py-2 text-[rgb(var(--muted))]">{r.role || "—"}</td>
                <td className="px-3 py-2 font-mono">{safeNum(r.budget_usdt)?.toFixed(2) ?? "—"}</td>
                <td className="px-3 py-2 font-mono">{safeNum(r.trust_score)?.toFixed(1) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
