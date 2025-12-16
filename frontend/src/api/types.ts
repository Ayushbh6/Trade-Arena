export type AgentState = {
  agent_id: string;
  role?: string;
  budget_usdt?: number;
  trust_score?: number;
};

export type ToolInfo = { name: string; label: string };

export type ModelInfo = {
  agent_id: string;
  role: string;
  llm_model_full?: string | null;
  llm_model_name?: string | null;
  tools: ToolInfo[] | { access: "all"; max_tool_calls?: number };
};

export type TradeProposal = {
  _id?: string;
  run_id?: string;
  cycle_id?: string | null;
  timestamp?: string;
  agent_id: string;
  trades: unknown[];
  notes?: string;
};

export type ManagerDecision = {
  _id?: string;
  run_id?: string;
  cycle_id?: string | null;
  timestamp?: string;
  manager_id?: string;
  decisions?: unknown[];
  notes?: string;
};

export type PnlReport = Record<string, unknown> & {
  firm_metrics?: Record<string, unknown>;
  agent_metrics?: Record<string, unknown>;
};

export type PnlHistoryResponse = { run_id: string | null; reports: PnlReport[] };

export type AuditEvent = {
  _id?: string;
  timestamp: string;
  event_type: string;
  agent_id?: string;
  payload: Record<string, unknown>;
};

export type MarketSummaryResponse = {
  run_id: string | null;
  snapshot: { _id?: string; timestamp?: string } | null;
  symbols: Record<
    string,
    {
      mark_price?: number | null;
      funding_rate?: number | null;
      open_interest?: number | null;
      top_of_book?: { bid?: number; ask?: number; spread?: number };
      last_candle?: any;
    }
  >;
};

export type Candle = { time: number; open: number; high: number; low: number; close: number; volume?: number };
