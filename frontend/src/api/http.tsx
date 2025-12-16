import React, { createContext, useContext, useMemo, useState } from "react";

type ApiCtx = {
  baseUrl: string;
  token: string | null;
  setToken: (t: string | null) => void;
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
};

const Ctx = createContext<ApiCtx | null>(null);

function apiBaseUrl(): string {
  const v = import.meta.env.VITE_API_BASE_URL as string | undefined;
  return (v || "http://localhost:8000").replace(/\/+$/, "");
}

function loadToken(): string | null {
  try {
    return localStorage.getItem("ui_token");
  } catch {
    return null;
  }
}

export function ApiProvider({ children }: { children: React.ReactNode }) {
  const baseUrl = apiBaseUrl();
  const [token, setTokenState] = useState<string | null>(() => loadToken());

  const setToken = (t: string | null) => {
    setTokenState(t);
    try {
      if (t) localStorage.setItem("ui_token", t);
      else localStorage.removeItem("ui_token");
    } catch {
      // ignore
    }
  };

  const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const headers: Record<string, string> = { ...(init?.headers as any) };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${baseUrl}${path}`, { ...init, headers });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${txt}`);
    }
    return (await res.json()) as T;
  };

  const value = useMemo(() => ({ baseUrl, token, setToken, request }), [baseUrl, token]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApi(): ApiCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("ApiProvider missing");
  return v;
}

