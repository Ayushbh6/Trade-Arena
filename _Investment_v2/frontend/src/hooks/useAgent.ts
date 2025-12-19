import { useState, useRef, useCallback, useEffect } from 'react';
import { AgentEvent, TradingSession } from '@/types/agent';

export function useAgent() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false); // WebSocket connection
  const [isServerReady, setIsServerReady] = useState(false); // HTTP Health check
  const [isRunning, setIsRunning] = useState(false);
  const [tokenCounts, setTokenCounts] = useState({
    manager: { prompt: 0, completion: 0, total: 0 },
    quant: { prompt: 0, completion: 0, total: 0 }
  });
  const [activeSession, setActiveSession] = useState<TradingSession | null>(null);

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
          // Also fetch active session
          const sessionRes = await fetch(`http://${baseUrl}/session/active`);
          const sessionData = await sessionRes.json();
          if (sessionData && sessionData.id) {
            setActiveSession(sessionData);
          } else {
            setActiveSession(null);
          }
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
        const data = JSON.parse(event.data) as AgentEvent;
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
          setIsRunning(false);
        }

        // Handle stop event
        if (data.type === 'system' && data.content.includes("stopped")) {
          setIsRunning(false);
        }
      } catch (e) {
        console.error('Error parsing event:', e);
      }
    };

    ws.onclose = () => {
      console.log('Disconnected from Agent Stream');
      setIsConnected(false);
      setIsRunning(false);
    };

    ws.onerror = (error) => {
      console.error('WebSocket Error:', error);
      setIsConnected(false);
      setIsRunning(false);
    };
  }, []);

  const connectAndRun = useCallback((prompt: string) => {
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

  }, [connect]);

  const startCycle = useCallback(async (durationMinutes: number) => {
    setIsRunning(true);
    connect();
    const baseUrl = getBaseUrl();
    try {
      await fetch(`http://${baseUrl}/agent/start?duration_minutes=${durationMinutes}`, { method: 'POST' });
    } catch (e) {
      console.error("Failed to start agent", e);
      setIsRunning(false);
    }
  }, [connect]);

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
    // Set isRunning to true to trigger the "Processing" UI state
    setIsRunning(true);
    connect();
    const baseUrl = getBaseUrl();
    try {
      const res = await fetch(`http://${baseUrl}/agent/run-once`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'error') {
        console.error(data.message);
        // Maybe show a toast/alert? For now just log
        setIsRunning(false);
      }
    } catch (e) {
      console.error("Failed to trigger run-once", e);
      setIsRunning(false);
    }
  }, [connect]);

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
    activeSession
  };
}
