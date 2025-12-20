import { useState, useRef, useCallback, useEffect } from 'react';
import { AgentEvent, TradingSession } from '@/types/agent';
import { toast } from 'sonner';

const STORAGE_KEYS = {
  EVENTS: 'investment_agent_events',
  SESSION: 'investment_agent_session',
  TOKENS: 'investment_agent_tokens'
};

export function useAgent() {
  // Initialize state from LocalStorage if available (client-side only check inside effect or lazy init)
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false); // WebSocket connection
  const [isServerReady, setIsServerReady] = useState(false); // HTTP Health check
  const [isRunning, setIsRunning] = useState(false);
  const [tokenCounts, setTokenCounts] = useState({
    manager: { prompt: 0, completion: 0, total: 0 },
    quant: { prompt: 0, completion: 0, total: 0 }
  });
  const [activeSession, setActiveSession] = useState<TradingSession | null>(null);
  const [history, setHistory] = useState<TradingSession[]>([]);

  // Load from storage on mount
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const savedEvents = localStorage.getItem(STORAGE_KEYS.EVENTS);
      const savedSession = localStorage.getItem(STORAGE_KEYS.SESSION);
      const savedTokens = localStorage.getItem(STORAGE_KEYS.TOKENS);

      if (savedEvents) setEvents(JSON.parse(savedEvents));
      if (savedSession) setActiveSession(JSON.parse(savedSession));
      if (savedTokens) setTokenCounts(JSON.parse(savedTokens));
    }
  }, []);

  // Sync state to storage
  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEYS.EVENTS, JSON.stringify(events));
    }
  }, [events]);

  useEffect(() => {
    if (typeof window !== 'undefined' && activeSession) {
      localStorage.setItem(STORAGE_KEYS.SESSION, JSON.stringify(activeSession));
    }
  }, [activeSession]);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEYS.TOKENS, JSON.stringify(tokenCounts));
    }
  }, [tokenCounts]);

  // Clear storage helper
  const clearStorage = useCallback(() => {
    setEvents([]);
    setTokenCounts({ manager: { prompt: 0, completion: 0, total: 0 }, quant: { prompt: 0, completion: 0, total: 0 } });
    setActiveSession(null);
    if (typeof window !== 'undefined') {
      localStorage.removeItem(STORAGE_KEYS.EVENTS);
      localStorage.removeItem(STORAGE_KEYS.SESSION);
      localStorage.removeItem(STORAGE_KEYS.TOKENS);
    }
  }, []);

  const wsRef = useRef<WebSocket | null>(null);

  // Helper to get API base URL dynamically
  const getBaseUrl = () => {
    if (typeof window === 'undefined') return '127.0.0.1:8000';
    const host = window.location.hostname;
    if (host === 'localhost') return '127.0.0.1:8000';
    return `${host}:8000`;
  };

  // Check server health on mount
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const baseUrl = getBaseUrl();
        const res = await fetch(`http://${baseUrl}/health`);
        if (res.ok) {
          setIsServerReady(true);
        }

        // Also check status
        const statusRes = await fetch(`http://${baseUrl}/agent/status`);
        if (statusRes.ok) {
          const status = await statusRes.json();
          setIsRunning(status.is_running);
        }

      } catch (e) {
        console.error("Server offline", e);
        setIsServerReady(false);
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, []);

  // Fetch history helper
  const fetchHistory = useCallback(async () => {
    try {
      const baseUrl = getBaseUrl();
      const res = await fetch(`http://${baseUrl}/history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data);
      }
    } catch (e) {
      console.error("Failed to fetch history", e);
    }
  }, []);

  // Fetch history on mount
  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const connect = useCallback(() => {
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) return;

    const baseUrl = getBaseUrl();
    const ws = new WebSocket(`ws://${baseUrl}/ws/chat`);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('Connected to Agent Stream');
      setIsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const rawData = JSON.parse(event.data); // Use rawData first

        // Handle Status Updates first
        if (rawData.type === 'status_update') {
          const content = rawData.content;
          if (content && typeof content.is_running === 'boolean') {
            const wasRunning = isRunning;
            const nowRunning = content.is_running;

            setIsRunning(nowRunning);

            // If we just stopped running (finished), refresh history to show the new run
            if (wasRunning && !nowRunning) {
              fetchHistory();
            }
          }
          return; // Don't add to event log
        }

        const data = rawData as AgentEvent;
        // Add local timestamp
        const eventWithTime = { ...data, timestamp: new Date().toISOString() };

        setEvents((prev) => [...prev, eventWithTime]);

        if (data.usage) {
          setTokenCounts(prev => {
            const source = data.source;
            if (source !== 'manager' && source !== 'quant') return prev;

            const target = source as 'manager' | 'quant';

            return {
              ...prev,
              [target]: {
                prompt: prev[target].prompt + (data.usage?.prompt_tokens || 0),
                completion: prev[target].completion + (data.usage?.completion_tokens || 0),
                total: prev[target].total + (data.usage?.total_tokens || 0)
              }
            };
          });
        }

        if (data.type === 'system' && data.metadata?.status === 'done') {
          // setIsRunning(false); // Rely on status_update mostly now, but keep for safety/redundancy or remove. 
          // Actually, let's keep it but rely on the explicit status_update for the toggle.
        }

        // Handle stop event
        if (data.type === 'system' && data.content.includes("stopped")) {
          // setIsRunning(false); 
        }
      } catch (e) {
        console.error('Error parsing event:', e);
      }
    };

    ws.onclose = () => {
      console.log('Disconnected from Agent Stream');
      setIsConnected(false);
      setIsRunning(false); // Default to false if disconnected
    };

    ws.onerror = (error) => {
      console.error('WebSocket Error:', error);
      setIsConnected(false);
      setIsRunning(false);
    };
  }, [fetchHistory, isRunning]);

  const connectAndRun = useCallback((prompt: string) => {
    // START NEW RUN: Clear old state
    clearStorage();

    // Reset state but keep the user prompt visible immediately
    const userEvent: AgentEvent = {
      type: "info",
      source: "user",
      content: prompt,
      timestamp: new Date().toISOString()
    };

    setEvents((prev) => [...prev, userEvent]);
    setIsRunning(true);

    connect();

    // Wait for connection then send
    const waitForOpen = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ prompt }));
        clearInterval(waitForOpen);
      }
    }, 100);

  }, [connect, clearStorage]);

  const startCycle = useCallback(async (durationMinutes: number) => {
    // START NEW RUN: Clear old state
    clearStorage();

    setIsRunning(true);
    connect();
    const baseUrl = getBaseUrl();
    try {
      await fetch(`http://${baseUrl}/agent/start?duration_minutes=${durationMinutes}`, { method: 'POST' });
    } catch (e) {
      console.error("Failed to start agent", e);
      setIsRunning(false);
    }
  }, [connect, clearStorage]);

  const stopCycle = useCallback(async () => {
    const baseUrl = getBaseUrl();
    try {
      await fetch(`http://${baseUrl}/agent/stop`, { method: 'POST' });
      setIsRunning(false);
    } catch (e) {
      console.error("Failed to stop agent", e);
    }
  }, []);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
    }
  }, []);

  const runOnce = useCallback(async () => {
    // Optimistic: Set running to true to show immediate feedback (button changes state)
    setIsRunning(true);
    connect();
    const baseUrl = getBaseUrl();
    try {
      const res = await fetch(`http://${baseUrl}/agent/run-once`, { method: 'POST' });
      const data = await res.json();

      if (data.status === 'error') {
        // Backend rejected request (e.g. Agent Busy)
        console.error(data.message);
        toast.error(data.message); // Show Red Toast

        // Ensure UI stays in sync with potential server activity or revert if truly failed
        setIsRunning(true);
      } else {
        // Success: NOW it is safe to clear storage for the new run
        clearStorage();
        toast.success("Agent run started");
      }
    } catch (e) {
      console.error("Failed to trigger run-once", e);
      toast.error("Failed to reach server");
      setIsRunning(false);
    }
  }, [connect, clearStorage]);

  const loadSession = useCallback(async (sessionId: string) => {
    try {
      const baseUrl = getBaseUrl();
      const res = await fetch(`http://${baseUrl}/session/${sessionId}`);
      if (res.ok) {
        const data = await res.json();
        // data.cycles is a list of cycles, each has "events"
        const allEvents: AgentEvent[] = [];

        // Sort cycles just in case
        const sortedCycles = data.cycles.sort((a: any, b: any) => a.cycle_number - b.cycle_number);

        sortedCycles.forEach((cycle: any) => {
          if (cycle.events && Array.isArray(cycle.events)) {
            // Polyfill timestamp if missing to avoid "Invalid Date"
            // Use cycle time or current time or session start as fallback, but ideally sequential
            const cycleEvents = cycle.events.map((e: AgentEvent, i: number) => ({
              ...e,
              timestamp: e.timestamp || new Date().toISOString() // Fallback if DB didn't have it
            }));
            allEvents.push(...cycleEvents);
          }
        });

        setEvents(allEvents);

        // Find session meta from history
        const sessionMeta = history.find(s => s.id === sessionId);
        if (sessionMeta) setActiveSession(sessionMeta);

        // If loaded from history, we shouldn't be "running" usually, but just in case
        setIsRunning(false);

        toast.success("Loaded past session");
      }
    } catch (e) {
      console.error("Failed to load session", e);
      toast.error("Failed to load session");
    }
  }, [history]);

  return {
    events,
    isConnected,
    isServerReady,
    isRunning,
    tokenCounts,
    connectAndRun,
    startCycle,
    stopCycle,
    runOnce,
    disconnect,
    activeSession,
    history,
    loadSession,
    resetSession: clearStorage
  };
}
