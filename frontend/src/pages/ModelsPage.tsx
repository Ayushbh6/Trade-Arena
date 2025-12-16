import { useEffect, useMemo, useState } from "react";
import Panel from "../components/Panel";
import { useApi } from "../api/http";
import Badge from "../components/Badge";
import type { AgentState, ModelInfo, ToolInfo } from "../api/types";
import { formatAgentName } from "../utils/format";

function safeNum(x: any): number | null {
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : null;
}

export default function ModelsPage() {
  const { request } = useApi();
  const [agents, setAgents] = useState<AgentState[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);

  useEffect(() => {
    Promise.all([request<{ agents: AgentState[] }>("/agents"), request<{ models: ModelInfo[] }>("/models")])
      .then(([a, m]) => {
        setAgents(a.agents || []);
        setModels(m.models || []);
        const first = m.models?.[0]?.agent_id || a.agents?.[0]?.agent_id || null;
        if (!selected && first) setSelected(first);
      })
      .catch(() => {
        setAgents([]);
        setModels([]);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    if (selected === "manager") {
      setPositions([]);
      setOrders([]);
      return;
    }
    Promise.all([
      request<{ positions: any[] }>(`/positions?agent_id=${encodeURIComponent(selected)}`),
      request<{ orders: any[] }>(`/orders?agent_id=${encodeURIComponent(selected)}&limit=100`),
    ])
      .then(([p, o]) => {
        setPositions(p.positions || []);
        setOrders(o.orders || []);
      })
      .catch(() => {
        setPositions([]);
        setOrders([]);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const selectedState = useMemo(() => agents.find((a) => a.agent_id === selected) || null, [agents, selected]);
  const selectedModel = useMemo(() => models.find((m) => m.agent_id === selected) || null, [models, selected]);

  const toolBadges = useMemo(() => {
    if (!selectedModel) return null;
    if (!selectedModel.tools) return null;
    if (!Array.isArray(selectedModel.tools)) {
      const max = (selectedModel.tools as any).max_tool_calls;
      return (
        <div className="mt-2 flex flex-wrap gap-2">
          <Badge>All Tools</Badge>
          {typeof max === "number" ? <Badge>{max} calls</Badge> : null}
        </div>
      );
    }
    const tools = selectedModel.tools as ToolInfo[];
    return (
      <div className="mt-2 flex flex-wrap gap-2">
        {tools.map((t) => (
          <Badge key={t.name}>{t.label}</Badge>
        ))}
      </div>
    );
  }, [selectedModel]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Panel title="Models" className="lg:col-span-1">
        <div className="space-y-2">
          {(models.length ? models : (agents as any)).map((a: any) => (
            <button
              key={a.agent_id}
              onClick={() => setSelected(a.agent_id)}
              className={`w-full rounded-lg border px-3 py-2 text-left ${
                selected === a.agent_id ? "border-[rgb(var(--accent))] bg-[rgb(var(--bg))]" : "border-[rgb(var(--border))] bg-[rgb(var(--panel))]"
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="font-semibold">{formatAgentName(a.agent_id)}</div>
                <div className="text-xs text-[rgb(var(--muted))]">{a.role ? String(a.role).toUpperCase() : "AGENT"}</div>
              </div>
              {"llm_model_name" in a && a.llm_model_name ? <div className="mt-1 text-xs text-[rgb(var(--muted))]">{a.llm_model_name}</div> : null}
              <div className="mt-1 text-xs text-[rgb(var(--muted))]">
                trust <span className="font-mono">{safeNum(a.trust_score)?.toFixed(1) ?? "—"}</span> · budget{" "}
                <span className="font-mono">{safeNum(a.budget_usdt)?.toFixed(2) ?? "—"}</span>
              </div>
            </button>
          ))}
        </div>
      </Panel>

      <Panel
        title="Positions"
        className="lg:col-span-1"
        right={
          selectedModel?.llm_model_name ? (
            <span className="text-xs text-[rgb(var(--muted))]">{selectedModel.llm_model_name}</span>
          ) : selectedState ? (
            <span className="text-xs text-[rgb(var(--muted))]">{formatAgentName(selectedState.agent_id)}</span>
          ) : null
        }
      >
        {selectedModel ? (
          <div className="mb-3 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3 text-sm">
            <div className="flex items-center justify-between">
              <div className="font-semibold">{formatAgentName(selectedModel.agent_id)}</div>
              <div className="text-xs text-[rgb(var(--muted))]">{selectedModel.role ? String(selectedModel.role).toUpperCase() : ""}</div>
            </div>
            {selectedModel.llm_model_name ? <div className="mt-1 text-xs text-[rgb(var(--muted))]">Model: {selectedModel.llm_model_name}</div> : null}
            {toolBadges}
          </div>
        ) : null}
        <div className="overflow-auto rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 bg-[rgb(var(--bg))]">
              <tr className="border-b border-[rgb(var(--border))] text-xs uppercase tracking-widest text-[rgb(var(--muted))]">
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Qty</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p._id || `${p.symbol}`} className="border-b border-[rgb(var(--border))]">
                  <td className="px-3 py-2 font-mono">{p.symbol}</td>
                  <td className="px-3 py-2 font-mono">{safeNum(p.qty)?.toFixed(6) ?? "—"}</td>
                </tr>
              ))}
              {positions.length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-sm text-[rgb(var(--muted))]" colSpan={2}>
                    {selected === "manager" ? "Manager has no positions." : "No positions."}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Orders (latest)" className="lg:col-span-1">
        <div className="max-h-[520px] overflow-auto rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 bg-[rgb(var(--bg))]">
              <tr className="border-b border-[rgb(var(--border))] text-[rgb(var(--muted))]">
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Side</th>
                <th className="px-3 py-2">Qty</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o._id || `${o.symbol}-${o.timestamp}`} className="border-b border-[rgb(var(--border))]">
                  <td className="px-3 py-2 font-mono">{o.timestamp ? new Date(o.timestamp).toLocaleTimeString() : "—"}</td>
                  <td className="px-3 py-2 font-mono">{o.symbol}</td>
                  <td className="px-3 py-2 font-mono">{o.side}</td>
                  <td className="px-3 py-2 font-mono">{safeNum(o.qty)?.toFixed(6) ?? "—"}</td>
                  <td className="px-3 py-2 font-mono">{o.status || "—"}</td>
                </tr>
              ))}
              {orders.length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-sm text-[rgb(var(--muted))]" colSpan={5}>
                    {selected === "manager" ? "Manager does not place orders directly." : "No orders."}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
