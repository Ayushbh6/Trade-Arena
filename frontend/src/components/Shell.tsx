import { NavLink, useNavigate } from "react-router-dom";
import Badge from "./Badge";
import { useApi } from "../api/http";
import { useEffect, useMemo, useState } from "react";

function cxNav({ isActive }: { isActive: boolean }) {
  return isActive
    ? "text-[rgb(var(--fg))] underline underline-offset-8"
    : "text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]";
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const { request, setToken, token } = useApi();
  const nav = useNavigate();
  const [health, setHealth] = useState<{ auth_enabled?: boolean; run_id?: string | null } | null>(null);
  const [dark, setDark] = useState<boolean>(() => document.documentElement.classList.contains("dark"));

  useEffect(() => {
    request<{ auth_enabled: boolean; run_id: string | null }>("/healthz")
      .then(setHealth)
      .catch(() => setHealth(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const authBadge = useMemo(() => {
    if (!health) return <Badge>API: offline</Badge>;
    if (!health.auth_enabled) return <Badge>Auth: off</Badge>;
    return token ? <Badge tone="good">Auth: on</Badge> : <Badge tone="bad">Auth: login</Badge>;
  }, [health, token]);

  const logout = () => {
    setToken(null);
    nav("/login");
  };

  return (
    <div className="min-h-screen">
      <div className="border-b border-[rgb(var(--border))] bg-[rgb(var(--bg))]">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <div className="text-lg font-semibold tracking-tight">
              AI-Native Trader <span className="text-[rgb(var(--muted))]">Co.</span>
            </div>
            <nav className="flex items-center gap-5 text-[12px] font-semibold uppercase tracking-[0.22em]">
              <NavLink to="/" className={cxNav}>
                Live
              </NavLink>
              <NavLink to="/leaderboard" className={cxNav}>
                Leaderboard
              </NavLink>
              <NavLink to="/models" className={cxNav}>
                Models
              </NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            {authBadge}
            {health?.run_id ? <Badge>run_id: {health.run_id}</Badge> : null}
            <button
              className="rounded-md border border-[rgb(var(--border))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
              onClick={() => setDark((v) => !v)}
            >
              {dark ? "Light" : "Dark"}
            </button>
            {health?.auth_enabled ? (
              token ? (
                <button
                  className="rounded-md border border-[rgb(var(--border))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
                  onClick={logout}
                >
                  Logout
                </button>
              ) : (
                <button
                  className="rounded-md border border-[rgb(var(--border))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
                  onClick={() => nav("/login")}
                >
                  Login
                </button>
              )
            ) : null}
          </div>
        </div>
      </div>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

