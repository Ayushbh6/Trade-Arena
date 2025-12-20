export type AgentEventType =
  | "thought"
  | "code"
  | "observation"
  | "tool_call"
  | "tool_result"
  | "decision"
  | "error"
  | "info"
  | "system"
  | "status_update";

export type AgentSource = "manager" | "quant" | "system" | "user";

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface AgentEvent {
  type: AgentEventType;
  source: AgentSource;
  content: string;
  metadata?: Record<string, any> | null;
  timestamp?: string; // We can add this on receive
  usage?: TokenUsage;
}

export interface TradingSession {
  id: string;
  start_time: string;
  status: string;
  config: Record<string, any>;
  initial_balance: number;
  current_balance: number;
}
