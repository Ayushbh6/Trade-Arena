import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../api/http";

export default function LoginPage() {
  const { request, setToken } = useApi();
  const nav = useNavigate();
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [username, setUsername] = useState("user001");
  const [password, setPassword] = useState("trader@123");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    request<{ auth_enabled: boolean }>("/healthz")
      .then((h) => setAuthEnabled(Boolean(h.auth_enabled)))
      .catch(() => setAuthEnabled(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await request<{ token: string }>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      setToken(res.token);
      nav("/");
    } catch (err: any) {
      setError(err?.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  if (authEnabled === false) {
    return (
      <div className="mx-auto max-w-md py-16">
        <div className="rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--panel))] p-6">
          <div className="text-lg font-semibold">Auth is disabled</div>
          <div className="mt-2 text-sm text-[rgb(var(--muted))]">This API does not require login. Continue to the dashboard.</div>
          <button
            className="mt-5 w-full rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-3 py-2 text-sm hover:bg-[rgb(var(--panel))]"
            onClick={() => nav("/")}
          >
            Continue
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md py-16">
      <div className="rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--panel))] p-6 shadow-crisp">
        <div className="text-lg font-semibold">Login</div>
        <div className="mt-2 text-sm text-[rgb(var(--muted))]">Token-based access for the dashboard.</div>
        <form className="mt-5 space-y-3" onSubmit={onSubmit}>
          <div>
            <div className="text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Username</div>
            <input
              className="mt-1 w-full rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-3 py-2 text-sm outline-none"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <div className="text-xs uppercase tracking-widest text-[rgb(var(--muted))]">Password</div>
            <input
              className="mt-1 w-full rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-3 py-2 text-sm outline-none"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </div>
          {error ? <div className="text-sm text-red-600">{error}</div> : null}
          <button
            className="w-full rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--fg))] px-3 py-2 text-sm font-semibold text-[rgb(var(--bg))] hover:opacity-90 disabled:opacity-60"
            disabled={loading}
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

