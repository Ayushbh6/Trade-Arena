"use client";

import { useState, useEffect, use } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { useAgentContext } from "@/context/AgentContext";
import { Command, Loader2, Sparkles } from "lucide-react";
import { RunControl } from "@/components/agent/RunControl";
import { TokenCounter } from "@/components/agent/TokenCounter";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { AgentFeed } from "@/components/agent/AgentFeed";
import { ArtifactViewer } from "@/components/agent/ArtifactViewer";

export default function SessionPage({ params }: { params: Promise<{ agentId: string; sessionId: string }> }) {
    const { sessionId } = use(params);
    const { events, isRunning, startCycle, stopCycle, runOnce, tokenCounts, isConnected, isServerReady, activeSession, loadSession } = useAgentContext();
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);

    // Load session on mount
    useEffect(() => {
        if (sessionId) {
            loadSession(sessionId);
        }
    }, [sessionId, loadSession]);

    // Determine status label and color
    const getStatus = () => {
        if (isConnected && isRunning) return { label: 'Live Session', color: 'green', animate: true };
        if (activeSession && !isRunning) return { label: 'Historical Session', color: 'blue', animate: false };
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

            {/* Main Content - Focus View */}
            <div className="flex-1 flex overflow-hidden z-0">
                <ResizablePanelGroup orientation="horizontal" className="h-full w-full">

                    {/* Left Panel: Chat/Feed */}
                    <ResizablePanel defaultSize={50} minSize={30} className="bg-black border-r border-white/10 relative">
                        <div className="h-full flex flex-col">
                            <div className="flex-1 overflow-y-auto custom-scrollbar p-0">
                                {/* Show Blank/Idle Screen if no active session AND not running */}
                                {!activeSession && !isRunning && events.length === 0 ? (
                                    <div className="h-full flex flex-col items-center justify-center text-white/30 p-8 text-center">
                                        <div className="h-20 w-20 bg-white/5 rounded-2xl flex items-center justify-center mb-6 border border-white/5">
                                            <Sparkles className="h-10 w-10 text-white/20" />
                                        </div>
                                        <h3 className="font-medium text-lg text-white/80 mb-2">New Session</h3>
                                        <p className="text-sm max-w-xs leading-relaxed text-white/50">Start a new run or select a past session from the sidebar to view history.</p>
                                    </div>
                                ) : events.length === 0 ? (
                                    <div className="h-full flex flex-col items-center justify-center text-white/30 p-8 text-center">
                                        <div className="h-20 w-20 bg-white/5 rounded-2xl flex items-center justify-center mb-6 border border-white/5">
                                            <Sparkles className="h-10 w-10 text-white/20" />
                                        </div>
                                        <h3 className="font-medium text-lg text-white/80 mb-2">Loading Session...</h3>
                                        <p className="text-sm max-w-xs leading-relaxed text-white/50">Fetching history...</p>
                                    </div>
                                ) : (
                                    <AgentFeed events={events} />
                                )}
                            </div>
                        </div>
                    </ResizablePanel>

                    <ResizableHandle className="w-1 bg-white/5 hover:bg-indigo-500/50 transition-colors" />

                    {/* Right Panel: Artifacts/Code */}
                    <ResizablePanel defaultSize={50} minSize={30} className="bg-black/50 backdrop-blur-sm">
                        <ArtifactViewer events={events} />
                    </ResizablePanel>

                </ResizablePanelGroup>
            </div>
        </main>
    );
}
