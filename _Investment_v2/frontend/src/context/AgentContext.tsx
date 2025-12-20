"use client";

import React, { createContext, useContext, ReactNode } from 'react';
import { useAgent } from '@/hooks/useAgent';
import { AgentEvent, TradingSession } from '@/types/agent';

interface AgentContextType {
    events: AgentEvent[];
    isConnected: boolean;
    isServerReady: boolean;
    isRunning: boolean;
    tokenCounts: {
        manager: { prompt: number; completion: number; total: number };
        quant: { prompt: number; completion: number; total: number };
    };
    connectAndRun: (prompt: string) => void;
    startCycle: (durationMinutes: number) => Promise<string | null>;
    stopCycle: () => void;
    runOnce: () => Promise<string | null>;
    disconnect: () => void;
    activeSession: TradingSession | null;
    history: TradingSession[];
    loadSession: (sessionId: string) => Promise<void>;
    resetSession: () => void;
}

const AgentContext = createContext<AgentContextType | null>(null);

export function AgentProvider({ children }: { children: ReactNode }) {
    const agentState = useAgent();

    return (
        <AgentContext.Provider value={agentState}>
            {children}
        </AgentContext.Provider>
    );
}

export function useAgentContext() {
    const context = useContext(AgentContext);
    if (!context) {
        throw new Error('useAgentContext must be used within an AgentProvider');
    }
    return context;
}
