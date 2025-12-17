import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createChart, CrosshairMode, type IChartApi } from "lightweight-charts";
import Panel from "../components/Panel";
import KpiCard from "../components/KpiCard";
import TickerRow, { type TickerItem } from "../components/TickerRow";
import Badge from "../components/Badge";
import ControlPanel, { type StartConfig, type ControlStatus } from "../components/ControlPanel";
import EventFeed from "../components/EventFeed";
import { useApi } from "../api/http";
import type { AgentState, AuditEvent, Candle, MarketSummaryResponse, ManagerDecision, PnlHistoryResponse, TradeProposal } from "../api/types";
import { makeLiveUrl, type LiveMessage } from "../api/ws";
import { formatAgentName } from "../utils/format";

function fmtMoney(x: unknown): string {
  const n = typeof x === "number" ? x : Number(x);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function safeNum(x: any): number | null {
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : null;
}

function withQuery(path: string, params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    q.set(k, String(v));
  }
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

export default function LivePage() {
  const { request, baseUrl, token } = useApi();
  const nav = useNavigate();

  const [runId, setRunId] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentState[]>([]);
  const [proposals, setProposals] = useState<TradeProposal[]>([]);
  const [decisions, setDecisions] = useState<ManagerDecision[]>([]);
  const [pnl, setPnl] = useState<any>(null);
  const [cycles, setCycles] = useState<{ cycle_id?: string | null; timestamp?: string }[]>([]);
  const [cycleId, setCycleId] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [ticker, setTicker] = useState<TickerItem[]>([]);
  const [conn, setConn] = useState<"connected" | "disconnected" | "connecting">("connecting");
  const [marketHint, setMarketHint] = useState<string | null>(null);
  const [equityHint, setEquityHint] = useState<string | null>(null);
  const [wsHint, setWsHint] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState<"ws" | "polling">("ws");
  const [wsFailures, setWsFailures] = useState(0);
  const [showAudit, setShowAudit] = useState<boolean>(true);
  const [lastAuditTs, setLastAuditTs] = useState<string | null>(null);
  
  // Phase 12: Control panel state
  const [controlStatus, setControlStatus] = useState<ControlStatus>({
    running: false,
    run_id: null,
  });
  const [liveEvents, setLiveEvents] = useState<AuditEvent[]>([]);

  const chartRef = useRef<HTMLDivElement | null>(null);
  const chartApi = useRef<IChartApi | null>(null);
  const candleSeries = useRef<ReturnType<IChartApi["addCandlestickSeries"]> | null>(null);
  const equityRef = useRef<HTMLDivElement | null>(null);
  const equityChart = useRef<IChartApi | null>(null);
  const equitySeries = useRef<ReturnType<IChartApi["addLineSeries"]> | null>(null);

  // Keep the dashboard stable: if this run has no pnl yet, show a sensible baseline.
  const firmEquity =
    (pnl?.firm_metrics as any)?.total_equity ??
    (pnl?.firm_metrics as any)?.equity ??
    10000;

  const refreshCore = async (rid: string | null) => {
    const q = rid ? `?run_id=${encodeURIComponent(rid)}` : "";
    const [a, p, d, pn] = await Promise.all([
      request<{ agents: AgentState[] }>(`/agents`),
      request<{ proposals: TradeProposal[] }>(`/proposals${q}`),
      request<{ decisions: ManagerDecision[] }>(`/decisions${q}`),
      request<{ latest: any }>(`/pnl${q}`),
    ]);
    setAgents(a.agents || []);
    setProposals(p.proposals || []);
    setDecisions(d.decisions || []);
    // Avoid blanking the dashboard while a new run warms up (no PnL yet).
    if (pn.latest !== null && pn.latest !== undefined) setPnl(pn.latest);
  };

  const refreshEquity = async (rid: string | null) => {
    const res = await request<PnlHistoryResponse>(withQuery("/pnl/history", { run_id: rid, limit: 400 }));
    const reports = res.reports || [];
    const points: { time: number; value: number }[] = [];
    for (const r of reports) {
      const ts = (r as any).timestamp;
      if (!ts) continue;
      const t = Date.parse(ts);
      if (!Number.isFinite(t)) continue;
      const fm = (r as any).firm_metrics || {};
      const eq = safeNum(fm.total_equity ?? fm.equity);
      if (eq === null) continue;
      points.push({ time: Math.floor(t / 1000), value: eq });
    }
    if (!points.length) {
      setEquityHint("Waiting for first PnL points…");
      return;
    }
    setEquityHint(null);
    if (!equityRef.current) return;
    if (!equityChart.current) {
      equityChart.current = createChart(equityRef.current, {
        layout: { attributionLogo: false, background: { color: "transparent" }, textColor: "rgba(120,120,120,1)" },
        grid: { vertLines: { color: "rgba(0,0,0,0.06)" }, horzLines: { color: "rgba(0,0,0,0.06)" } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "rgba(0,0,0,0.12)" },
        timeScale: { borderColor: "rgba(0,0,0,0.12)" },
      });
      equitySeries.current = equityChart.current.addLineSeries({ color: "rgba(17,17,17,1)", lineWidth: 2 });
    }
    equitySeries.current?.setData(points as any);
    equityChart.current.timeScale().fitContent();
  };

  const refreshCycles = async (rid: string | null) => {
    const res = await request<{ cycles: { cycle_id?: string | null; timestamp?: string }[] }>(withQuery("/cycles", { run_id: rid }));
    const cs = res.cycles || [];
    setCycles(cs);
    const latest = cs.length ? (cs[cs.length - 1]?.cycle_id as string | null) : null;
    setCycleId((cur) => cur || latest);
  };

  const refreshCycleView = async (rid: string | null, cid: string | null) => {
    if (!cid) return;
    const [p, d, pn] = await Promise.all([
      request<{ proposals: TradeProposal[] }>(withQuery("/proposals", { run_id: rid, cycle_id: cid })),
      request<{ decisions: ManagerDecision[] }>(withQuery("/decisions", { run_id: rid, cycle_id: cid })),
      request<{ latest: any }>(withQuery("/pnl", { run_id: rid, cycle_id: cid })),
    ]);
    setProposals(p.proposals || []);
    setDecisions(d.decisions || []);
    if (pn.latest) setPnl(pn.latest);
  };

  const refreshTicker = async (rid: string | null) => {
    const ms = await request<MarketSummaryResponse>(withQuery("/market/summary", { timeframe: "5m", run_id: rid }));
    const symsAll = Object.keys(ms.symbols || {});
    // If the new run has not produced a market snapshot yet, don't wipe the existing ticker.
    if (!symsAll.length) return;
    const items: TickerItem[] = [];
    for (const s of symsAll.slice(0, 8)) {
      const price = safeNum(ms.symbols[s]?.mark_price) ?? safeNum(ms.symbols[s]?.last_candle?.close);
      // derive a quick 1-bar change by fetching 2 candles
      try {
        const cs = await request<{ candles: Candle[] }>(withQuery("/market/candles", { symbol: s, timeframe: "5m", limit: 2, run_id: rid }));
        const c = cs.candles || [];
        const prev = c.length >= 2 ? c[c.length - 2]?.close : null;
        const last = c.length >= 1 ? c[c.length - 1]?.close : null;
        const ch = prev && last ? ((last - prev) / prev) * 100 : null;
        items.push({ symbol: s, price: price ?? last, changePct: ch });
      } catch {
        items.push({ symbol: s, price, changePct: null });
      }
    }
    setTicker(items);
  };

  const refreshChart = async (rid: string | null, symbol = "BTCUSDT") => {
    const res = await request<{ candles: Candle[] }>(withQuery("/market/candles", { symbol, timeframe: "5m", limit: 300, run_id: rid }));
    const candles = res.candles || [];
    if (!candles.length) {
      setMarketHint("Waiting for first market snapshot…");
      return;
    }
    setMarketHint(null);
    if (!chartRef.current) return;
    if (!chartApi.current) {
      chartApi.current = createChart(chartRef.current, {
        layout: { attributionLogo: false, background: { color: "transparent" }, textColor: "rgba(120,120,120,1)" },
        grid: { vertLines: { color: "rgba(0,0,0,0.06)" }, horzLines: { color: "rgba(0,0,0,0.06)" } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "rgba(0,0,0,0.12)" },
        timeScale: { borderColor: "rgba(0,0,0,0.12)" },
      });
    }
    if (!candleSeries.current) {
      candleSeries.current = chartApi.current.addCandlestickSeries({
        upColor: "rgba(16,185,129,1)",
        downColor: "rgba(239,68,68,1)",
        borderVisible: false,
        wickUpColor: "rgba(16,185,129,1)",
        wickDownColor: "rgba(239,68,68,1)",
      });
    }
    candleSeries.current.setData(candles as any);
    chartApi.current.timeScale().fitContent();
  };

  // Phase 12: Control functions
  const handleStart = async (config: StartConfig) => {
    try {
      const res = await request<any>("/control/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          traders: config.traders,
          cycles: config.cycles,
          dry_run: config.dry_run,
        }),
      });
      if (res.success) {
        const rid = res.run_id as string;
        setRunId(rid);
        setLiveEvents([]);
        setControlStatus({
          running: true,
          run_id: rid,
          current_cycle: 0,
          total_cycles: config.cycles,
        });
      } else {
        alert(`Failed to start: ${res.error || "unknown error"}`);
      }
    } catch (err: any) {
      alert(`Failed to start: ${err.message}`);
    }
  };

  const handleStop = async () => {
    try {
      await request<any>("/control/stop", { method: "POST" });
      setControlStatus(prev => ({ ...prev, running: false }));
    } catch (err: any) {
      alert(`Failed to stop: ${err.message}`);
    }
  };

  // Phase 12: Poll control status
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const status = await request<ControlStatus>("/control/status");
        setControlStatus(status);
        if (status.run_id && status.run_id !== runId) {
          setRunId(status.run_id);
        }
      } catch {
        // ignore
      } finally {
        if (alive) setTimeout(poll, 2000);
      }
    };
    poll();
    return () => { alive = false; };
  }, [runId, request]);

  useEffect(() => {
    request<{ run_id: string | null; auth_enabled: boolean }>("/healthz")
      .then((h) => {
        setRunId(h.run_id || null);
        if (h.auth_enabled && !token) nav("/login");
      })
      .catch(() => setRunId(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    refreshCore(runId);
    refreshTicker(runId);
    refreshCycles(runId).catch(() => {});
    refreshChart(runId).catch(() => {});
    refreshEquity(runId).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // Backfill recent audit/events on refresh so the page doesn't look empty.
  useEffect(() => {
    let alive = true;
    const backfill = async () => {
      if (!runId) return;
      try {
        const res = await request<{ events: AuditEvent[] }>(withQuery("/audit", { run_id: runId, limit: 200 }));
        const events = res.events || [];
        if (!alive) return;
        if (events.length) {
          setAudit(events.slice(-300));
          setLiveEvents(events.slice().reverse().slice(0, 200));
          setLastAuditTs(events[events.length - 1]?.timestamp || null);
        }
      } catch {
        // ignore
      }
    };
    backfill();
    return () => {
      alive = false;
    };
  }, [runId, request]);

  useEffect(() => {
    refreshCycleView(runId, cycleId).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cycleId]);

  // Polling fallback if WS cannot connect (e.g. local dev WS stack mismatch).
  useEffect(() => {
    if (liveMode !== "polling") return;
    let alive = true;
    let since = audit.length ? audit[audit.length - 1]?.timestamp : null;
    setConn("connected");
    setWsHint("Live feed using polling fallback (WebSocket unavailable).");

    const tick = async () => {
      if (!alive) return;
      try {
        const res = await request<{ events: AuditEvent[] }>(withQuery("/audit", { run_id: runId, since_ts: since, limit: 200 }));
        const events = res.events || [];
        if (events.length) {
          since = events[events.length - 1]?.timestamp || since;
          setAudit((prev) => [...prev, ...events].slice(-300));
          setLiveEvents((prev) => [...events, ...prev].slice(0, 200));
          // Refresh key panels when anything new arrives.
          await refreshCore(runId);
          await refreshTicker(runId);
          await refreshChart(runId).catch(() => {});
          await refreshEquity(runId).catch(() => {});
        }
      } catch {
        // If polling fails, show disconnected but keep retrying.
        setConn("disconnected");
      } finally {
        if (alive) window.setTimeout(tick, 1500);
      }
    };

    window.setTimeout(tick, 500);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode, runId]);

  useEffect(() => {
    if (liveMode !== "ws") return;
    let alive = true;
    let ws: WebSocket | null = null;
    let retryMs = 500;
    let retryTimer: number | null = null;

    const connect = () => {
      if (!alive) return;
      const wsUrl = makeLiveUrl(baseUrl, token, runId);
      setWsHint(null);
      setConn("connecting");
      try {
        ws = new WebSocket(wsUrl);
      } catch {
        setConn("disconnected");
        setWsHint(`WS init failed. Check VITE_API_BASE_URL (${baseUrl}).`);
        return;
      }

      let opened = false;
      ws.onopen = () => {
        opened = true;
        retryMs = 750;
        setConn("connected");
        setWsHint(null);
        setWsFailures(0);
      };
      ws.onclose = (ev) => {
        setConn("disconnected");
        // If auth is enabled and the token is missing/invalid, server closes with 4401.
        // Redirecting via /healthz covers initial load; this message helps when token expires.
        if (ev.code === 4401) {
          setWsHint("Live feed requires login. Please sign in again.");
        } else {
          setWsHint(`Live feed (WebSocket) disconnected. API base: ${baseUrl}`);
        }
        if (!opened) {
          setWsFailures((n) => n + 1);
        }
        if (!alive) return;
        retryTimer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(10_000, Math.floor(retryMs * 1.6));
      };
      ws.onerror = () => {
        setConn("disconnected");
        // onclose will handle retry
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as LiveMessage;
        if (msg.type === "hello") {
          if ((msg as any).run_id) setRunId((msg as any).run_id);
          if ((msg as any).auth_enabled && !token) nav("/login");
        }
        if (msg.type === "event") {
          const e = (msg as any).data as AuditEvent;
          if (e) {
            setAudit((prev) => [...prev, e].slice(-300));
            setLiveEvents((prev) => [e, ...prev].slice(0, 200));
            setLastAuditTs(e.timestamp || null);
            if (e.event_type === "market_snapshot_ready") refreshTicker(runId);
          }
        }
      };
    };

    connect();

    return () => {
      alive = false;
      if (retryTimer) window.clearTimeout(retryTimer);
      try {
        ws?.close();
      } catch {
        // ignore
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, token, runId, liveMode]);

  useEffect(() => {
    // Do not auto-switch to polling: it feels inconsistent/flaky to users.
    // Keep retrying WS, and allow an explicit user switch instead.
    if (wsFailures >= 3 && liveMode === "ws") {
      setWsHint("WebSocket is struggling to connect. You can switch to polling mode.");
    }
  }, [wsFailures]);

  const activeModels = agents.length + 1; // 4 traders + 1 manager (manager has no agent_state row)
  const openPositions = useMemo(() => {
    // best-effort derived from pnl/agents; detailed positions view lives in /models
    return "—";
  }, []);

  const connectionBadge =
    conn === "connected" ? (
      liveMode === "ws" ? (
        <Badge tone="good">Live</Badge>
      ) : (
        <Badge>Polling</Badge>
      )
    ) : conn === "connecting" ? (
      <Badge>Connecting</Badge>
    ) : (
      <Badge tone="bad">Disconnected</Badge>
    );

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="text-[11px] uppercase tracking-widest text-[rgb(var(--muted))]">Total Account Value</div>
          <div className="mt-1 text-4xl font-semibold tabular-nums">${fmtMoney(firmEquity)}</div>
          <div className="mt-2 flex flex-col gap-1">
            <div className="flex items-center gap-2">
              {connectionBadge}
              {liveMode === "ws" && wsFailures >= 3 ? (
                <button
                  type="button"
                  onClick={() => setLiveMode("polling")}
                  className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
                >
                  Use polling
                </button>
              ) : liveMode === "polling" ? (
                <button
                  type="button"
                  onClick={() => {
                    setWsFailures(0);
                    setLiveMode("ws");
                  }}
                  className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
                >
                  Try WebSocket
                </button>
              ) : null}
            </div>
            {wsHint ? <div className="text-xs text-[rgb(var(--muted))]">{wsHint}</div> : null}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KpiCard label="Daily PnL" value="—" sub="(MVP)" />
          <KpiCard label="Hourly PnL" value="—" sub="(MVP)" />
          <KpiCard label="Active Models" value={`${activeModels}`} />
          <KpiCard label="Open Positions" value={`${openPositions}`} />
        </div>
      </div>

      <TickerRow items={ticker} />

      {/* Phase 12: Control Panel & Event Feed */}
      <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-3">
        <div className="h-full lg:col-span-1">
          <ControlPanel
            onStart={handleStart}
            onStop={handleStop}
            status={controlStatus}
          />
        </div>
        <div className="h-full lg:col-span-2">
          <EventFeed events={liveEvents} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel title="Session Insight" className="lg:col-span-2">
          <div className="flex items-center justify-between">
            <div className="text-2xl font-semibold tracking-tight">AI-Native Trader Company</div>
            <div className="flex items-center gap-2">
              {cycleId ? <Badge>cycle: {cycleId}</Badge> : null}
              {runId ? <Badge>run_id: {runId}</Badge> : null}
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Cycle Explorer</div>
            <select
              className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-2 py-1 text-xs"
              value={cycleId || ""}
              onChange={(e) => setCycleId(e.target.value || null)}
            >
              <option value="">(latest)</option>
              {cycles
                .slice()
                .reverse()
                .slice(0, 30)
                .map((c) => (
                  <option key={c.cycle_id || c.timestamp} value={c.cycle_id || ""}>
                    {c.cycle_id || c.timestamp}
                  </option>
                ))}
            </select>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <div className="text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Market (BTCUSDT 5m)</div>
              <div className="relative mt-2 h-56 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
                <div className="h-full w-full" ref={chartRef} />
                {marketHint ? (
                  <div className="pointer-events-none absolute inset-0 grid place-items-center px-6 text-center text-xs text-[rgb(var(--muted))]">
                    {marketHint}
                  </div>
                ) : null}
              </div>
              <div className="mt-4 text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Firm Equity Curve</div>
              <div className="relative mt-2 h-40 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
                <div className="h-full w-full" ref={equityRef} />
                {equityHint ? (
                  <div className="pointer-events-none absolute inset-0 grid place-items-center px-6 text-center text-xs text-[rgb(var(--muted))]">
                    {equityHint}
                  </div>
                ) : null}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Latest Manager Notes</div>
              <div className="mt-2 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3 text-sm">
                {decisions?.[0]?.notes || "—"}
              </div>
              <div className="mt-4 text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Latest Proposals</div>
              <div className="mt-2 space-y-2">
                {proposals.slice(0, 4).map((p) => (
                  <div key={p._id || `${p.agent_id}-${p.timestamp}`} className="rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3">
                    <div className="flex items-center justify-between">
                      <div className="font-semibold">{formatAgentName(p.agent_id)}</div>
                      <div className="text-xs text-[rgb(var(--muted))]">{p.timestamp ? new Date(p.timestamp).toLocaleTimeString() : ""}</div>
                    </div>
                    <div className="mt-1 text-xs text-[rgb(var(--muted))]">
                      trades: <span className="font-mono">{Array.isArray(p.trades) ? p.trades.length : 0}</span>
                    </div>
                    {p.notes ? <div className="mt-2 text-sm">{p.notes}</div> : null}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>

        <Panel
          title="Leading Models"
          right={<span className="text-xs text-[rgb(var(--muted))]">trust / ROI from latest PnL</span>}
          className="lg:col-span-1"
        >
          <div className="space-y-2">
            {agents
              .slice()
              .sort((a, b) => (safeNum(b.trust_score) ?? 0) - (safeNum(a.trust_score) ?? 0))
              .slice(0, 8)
              .map((a) => (
                <div key={a.agent_id} className="flex items-center justify-between rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3">
                  <div>
                    <div className="font-semibold">{formatAgentName(a.agent_id)}</div>
                    <div className="text-xs text-[rgb(var(--muted))]">{a.role || "agent"}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-semibold tabular-nums">{safeNum(a.trust_score)?.toFixed(1) ?? "—"}</div>
                    <div className="text-xs text-[rgb(var(--muted))]">trust</div>
                  </div>
                </div>
              ))}
          </div>
        </Panel>
      </div>

      <div className={`grid grid-cols-1 gap-4 ${showAudit ? "lg:grid-cols-2" : ""}`}>
        {showAudit ? (
          <Panel title="Audit (tail)">
            <div className="max-h-72 overflow-auto rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
              <table className="w-full border-collapse text-left text-xs">
                <thead className="sticky top-0 bg-[rgb(var(--bg))]">
                  <tr className="border-b border-[rgb(var(--border))] text-[rgb(var(--muted))]">
                    <th className="px-3 py-2">Time</th>
                    <th className="px-3 py-2">Event</th>
                    <th className="px-3 py-2">Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.slice(-120).map((e) => (
                    <tr key={e._id || e.timestamp} className="border-b border-[rgb(var(--border))]">
                      <td className="px-3 py-2 font-mono">{new Date(e.timestamp).toLocaleTimeString()}</td>
                      <td className="px-3 py-2 font-mono">{e.event_type}</td>
                      <td className="px-3 py-2">{e.agent_id || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        ) : null}

        <Panel
          title="Decisions (latest)"
          right={
            <button
              type="button"
              onClick={() => setShowAudit((v) => !v)}
              className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
              aria-pressed={!showAudit}
            >
              {showAudit ? "Hide audit" : "Show audit"}
            </button>
          }
        >
          <div className="space-y-2">
            {decisions.slice(0, 6).map((d) => (
              <div key={d._id || `${d.timestamp}`} className="rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3">
                <div className="flex items-center justify-between">
                  <div className="font-semibold">{formatAgentName(d.manager_id || "manager")}</div>
                  <div className="text-xs text-[rgb(var(--muted))]">{d.timestamp ? new Date(d.timestamp).toLocaleString() : ""}</div>
                </div>
                <div className="mt-2 text-sm text-[rgb(var(--muted))]">
                  decisions: <span className="font-mono">{Array.isArray(d.decisions) ? d.decisions.length : 0}</span>
                </div>
                {d.notes ? <div className="mt-2 text-sm">{d.notes}</div> : null}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
