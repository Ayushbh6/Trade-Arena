import type { AuditEvent } from "./types";

export type LiveMessage =
  | { type: "hello"; run_id?: string | null; server_time?: string; auth_enabled?: boolean }
  | { type: "event"; data: AuditEvent };

export function makeLiveUrl(baseUrl: string, token: string | null, runId?: string | null): string {
  const u = new URL(baseUrl.replace(/^http/, "ws") + "/live");
  if (token) u.searchParams.set("token", token);
  if (runId) u.searchParams.set("run_id", runId);
  return u.toString();
}
