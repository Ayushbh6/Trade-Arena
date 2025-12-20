"use client";

import { useState } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Dashboard as DashboardComponent } from "@/components/dashboard/Dashboard";
import { useAgentContext } from "@/context/AgentContext";
import { Command, Loader2 } from "lucide-react";
import { RunControl } from "@/components/agent/RunControl";
import { TokenCounter } from "@/components/agent/TokenCounter";

export default function DashboardPage() {
    const { events, activeSession, isRunning, startCycle, stopCycle, runOnce, tokenCounts, isConnected, isServerReady, history } = useAgentContext();
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);

    // Determine status label and color
    const getStatus = () => {
        if (isConnected) return { label: 'Live Session', color: 'green', animate: true };
        if (isServerReady) return { label: 'System Ready', color: 'emerald', animate: false };
        return { label: 'Offline', color: 'red', animate: false };
    };

    const status = getStatus();

    return (
        <main className="h-screen w-screen bg-black text-white overflow-hidden flex flex-col font-sans selection:bg-indigo-500/30 relative">
            {/* Sidebar & Overlay */}
            <Sidebar
                isOpen={isSidebarOpen}
                onClose={() => setIsSidebarOpen(false)}
            />

            {/* Backdrop Blur Overlay */}
            <div
                className={`absolute inset-0 bg-black/40 backdrop-blur-[2px] z-50 transition-opacity duration-300 ${isSidebarOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
                onClick={() => setIsSidebarOpen(false)}
            />

            {/* Header - Sleek Glass */}
            <header className="h-14 flex-none border-b border-white/10 flex items-center justify-between px-6 bg-black/50 backdrop-blur-xl sticky top-0 z-40 relative">
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => setIsSidebarOpen(true)}
                        className="h-8 w-8 bg-white/10 rounded-xl flex items-center justify-center border border-white/5 shadow-inner hover:bg-white/20 transition-colors"
                    >
                        <Command className="h-4 w-4 text-white" />
                    </button>
                    <div>
                        <h1 className="font-medium text-sm tracking-wide text-white/90">Investment Agent <span className="text-white/40 font-normal">v2.0</span></h1>
                    </div>
                </div>

                {/* Middle - Run Control */}
                <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
                    <RunControl isRunning={isRunning} onStart={startCycle} onStop={stopCycle} onRunOnce={runOnce} />
                </div>

                <div className="flex items-center gap-3">
                    <TokenCounter managerTokens={tokenCounts.manager} quantTokens={tokenCounts.quant} />

                    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border bg-${status.color}-500/10 border-${status.color}-500/20 text-${status.color}-400`}>
                        <div className={`h-1.5 w-1.5 rounded-full bg-${status.color}-500 ${status.animate ? 'animate-pulse' : ''}`} />
                        <span className="text-[10px] font-medium tracking-wide uppercase">{status.label}</span>
                    </div>
                    {isRunning && <span className="text-xs text-indigo-400 font-medium flex items-center gap-1 animate-pulse"><Loader2 className="h-3 w-3 animate-spin" /> Processing</span>}
                </div>
            </header>

            {/* Main Content */}
            <div className="flex-1 flex overflow-hidden z-0">
                <DashboardComponent events={events} activeSession={activeSession} history={history} />
            </div>
        </main>
    );
}
