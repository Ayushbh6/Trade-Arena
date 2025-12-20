import React, { useMemo } from 'react';
import { LayoutDashboard, X, User, Bot, Home, ArrowLeft, Plus } from 'lucide-react';
import { Button } from "@/components/ui/button";
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAgentContext } from '@/context/AgentContext';

interface SidebarProps {
    isOpen: boolean;
    onClose: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
    const pathname = usePathname();
    const { history, resetSession } = useAgentContext();

    const isAgentView = pathname?.startsWith('/active-agent/');

    // Group history by date (Only used in Agent View)
    const groupedHistory = useMemo(() => {
        if (!isAgentView) return [];
        
        const groups: { dateLabel: string; sessions: any[] }[] = [];
        let lastDate = '';

        history.forEach((session: any) => {
            if (!session.start_time) return;
            const date = new Date(session.start_time);
            
            // Determine label (Today, Yesterday, or Date)
            let label = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const today = new Date();
            const yesterday = new Date();
            yesterday.setDate(yesterday.getDate() - 1);

            if (date.toDateString() === today.toDateString()) label = 'Today';
            else if (date.toDateString() === yesterday.toDateString()) label = 'Yesterday';

            if (label !== lastDate) {
                groups.push({ dateLabel: label, sessions: [] });
                lastDate = label;
            }
            groups[groups.length - 1].sessions.push(session);
        });
        return groups;
    }, [history, isAgentView]);

    return (
        <div
            className={`
                fixed top-0 left-0 h-full w-96 
                bg-neutral-950 border-r border-white/10 
                z-[60] transition-transform duration-300 ease-in-out
                flex flex-col
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}
            `}
        >
            {/* Header */}
            <div className="h-14 flex items-center justify-between px-6 border-b border-white/10 flex-none bg-neutral-950">
                <Link href="/" className="flex items-center gap-2" onClick={onClose}>
                    <Home className="h-4 w-4 text-white/50" />
                    <span className="font-medium text-sm text-white/90 tracking-wide">Investment Agent</span>
                </Link>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    className="h-8 w-8 text-white/50 hover:text-white hover:bg-white/10"
                >
                    <X className="h-4 w-4" />
                </Button>
            </div>

            {/* Menu Items */}
            <div className="flex-1 flex flex-col min-h-0">
                
                {/* 1. Global Dashboard Link (Fixed at top of menu) */}
                <div className="p-4 flex-none">
                    <Link href="/dashboard" onClick={onClose}>
                        <Button
                            variant="ghost"
                            className={`w-full justify-start h-10 ${pathname === '/dashboard' ? 'bg-white/10 text-white' : 'text-white/60 hover:text-white hover:bg-white/5'}`}
                        >
                            {isAgentView ? <ArrowLeft className="h-4 w-4 mr-3" /> : <LayoutDashboard className="h-4 w-4 mr-3" />}
                            Investment Dashboard
                        </Button>
                    </Link>
                </div>

                {/* 2. Scrollable Content Area */}
                <div className="flex-1 overflow-y-auto custom-scrollbar px-4 pb-4">
                    {!isAgentView ? (
                        /* GLOBAL VIEW: Show List of Agents */
                        <div className="space-y-1 animate-in fade-in duration-300">
                            <div className="px-3 py-2 text-xs font-semibold text-white/30 uppercase tracking-wider">
                                Active Agents
                            </div>
                            <Link href="/active-agent/quant-trader-1" onClick={onClose}>
                                <Button
                                    variant="ghost"
                                    onClick={() => {
                                        resetSession();
                                        onClose();
                                    }}
                                    className="w-full justify-start h-12 text-sm text-white/70 hover:text-white hover:bg-white/5 border border-white/5"
                                >
                                    <Bot className="h-5 w-5 mr-3 text-indigo-400" />
                                    Quant Trader 1
                                    <span className="ml-auto text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.5 rounded">LIVE</span>
                                </Button>
                            </Link>
                            {/* Placeholder for future agents */}
                            <Button disabled className="w-full justify-start h-12 text-sm text-white/20">
                                <Bot className="h-5 w-5 mr-3 opacity-50" />
                                Quant Trader 2
                                <span className="ml-auto text-[10px] text-white/20 px-1.5">SOON</span>
                            </Button>
                        </div>
                    ) : (
                        /* AGENT VIEW: Show Context & History */
                        <div className="space-y-1 animate-in slide-in-from-right-4 fade-in duration-300">
                             {/* Active Agent Context Header */}
                             <div className="mb-2">
                                <div className="flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/10">
                                    <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white">
                                        <Bot className="h-5 w-5" />
                                    </div>
                                    <div>
                                        <div className="text-sm font-medium text-white">Quant Trader 1</div>
                                        <div className="text-[10px] text-white/40">Active Trading Agent</div>
                                    </div>
                                </div>
                            </div>

                            {/* New Run Button (Positioned at top of list) */}
                            <div className="py-2 mb-4 border-b border-white/5">
                                <Link href="/active-agent/quant-trader-1" onClick={() => { resetSession(); onClose(); }}>
                                    <Button className="w-full bg-white/5 hover:bg-white/10 text-white border border-white/10 h-10 text-xs justify-start px-3">
                                        <Plus className="h-4 w-4 mr-2 text-indigo-400" />
                                        Start New Run
                                    </Button>
                                </Link>
                            </div>

                            {/* History List */}
                            <div className="space-y-4 pl-1">
                                {groupedHistory.map((group, groupIndex) => (
                                    <div key={group.dateLabel || `group-${groupIndex}`} className="space-y-1">
                                        <div className="px-3 text-[10px] text-white/30 font-semibold uppercase tracking-wider">
                                            {group.dateLabel}
                                        </div>
                                        {group.sessions.map((session: any) => {
                                            const isAuto = session.cycle_count > 1 || session.config?.mode === 'autonomous';
                                            const isCurrent = pathname.includes(session.id);
                                            
                                            return (
                                                <Link
                                                    key={session.id}
                                                    href={`/active-agent/quant-trader-1/session/${session.id}`}
                                                    onClick={onClose}
                                                    className="block w-full"
                                                >
                                                    <Button
                                                        variant="ghost"
                                                        className={`w-full justify-start h-9 text-xs truncate group ${isCurrent ? 'bg-white/10 text-white border border-white/10' : 'text-white/40 hover:text-white/80 hover:bg-white/5'}`}
                                                    >
                                                        <div className={`w-1.5 h-1.5 rounded-full mr-3 flex-none ${isAuto ? 'bg-indigo-500' : 'bg-white/10 group-hover:bg-white/30'}`} />
                                                        <div className="flex flex-col items-start truncate min-w-0 flex-1">
                                                            <span className="truncate w-full text-left">{new Date(session.start_time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} Session</span>
                                                        </div>
                                                        {isAuto && <span className="ml-2 text-[9px] opacity-40 flex-none">{session.cycle_count}c</span>}
                                                    </Button>
                                                </Link>
                                            );
                                        })}
                                    </div>
                                ))}
                                {history.length === 0 && (
                                    <div className="px-4 py-8 text-center text-xs text-white/20 italic">
                                        No past runs found.
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Footer User Profile */}
            <div className="p-4 border-t border-white/10 bg-neutral-950 flex-none">
                <div className="flex items-center gap-3 p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-colors cursor-pointer border border-white/5">
                    <div className="h-8 w-8 rounded-full bg-neutral-800 flex items-center justify-center text-white/80">
                        <User className="h-4 w-4" />
                    </div>
                    <div>
                        <div className="text-xs font-medium text-white">Guest User</div>
                        <div className="text-[10px] text-white/40">Basic Plan</div>
                    </div>
                </div>
            </div>
        </div>
    );
}
