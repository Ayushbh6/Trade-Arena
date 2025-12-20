import React, { useState } from 'react';
import { LayoutDashboard, Focus, X, ChevronDown, User, Bot, Home } from 'lucide-react';
import { Button } from "@/components/ui/button";
import Link from 'next/link';
import { usePathname } from 'next/navigation';

interface SidebarProps {
    isOpen: boolean;
    onClose: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
    const [isFocusOpen, setIsFocusOpen] = useState(true);
    const pathname = usePathname();

    const isActive = (path: string) => pathname === path || pathname?.startsWith(path + '/');

    return (
        <div
            className={`
                fixed top-0 left-0 h-full w-96 
                bg-black/80 backdrop-blur-2xl border-r border-white/10 
                z-[60] transition-transform duration-300 ease-in-out
                flex flex-col
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}
            `}
        >
            {/* Header */}
            <div className="h-14 flex items-center justify-between px-6 border-b border-white/10 flex-none bg-black/20">
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
            <div className="flex-1 p-4 space-y-1">
                <Link href="/dashboard" onClick={onClose}>
                    <Button
                        variant="ghost"
                        className={`w-full justify-start h-12 mb-2 ${isActive('/dashboard') ? 'bg-white/10 text-white' : 'text-white/60 hover:text-white hover:bg-white/5'}`}
                    >
                        <LayoutDashboard className="h-5 w-5 mr-3" />
                        Investment Dashboard
                    </Button>
                </Link>

                {/* Focus Group */}
                <div className="space-y-1 pt-2 border-t border-white/5">
                    <Button
                        variant="ghost"
                        onClick={() => setIsFocusOpen(!isFocusOpen)}
                        className={`w-full justify-between h-12 ${isActive('/active-agent') ? 'text-white' : 'text-white/60 hover:text-white hover:bg-white/5'}`}
                    >
                        <div className="flex items-center">
                            <Focus className="h-5 w-5 mr-3" />
                            Active Agents
                        </div>
                        <ChevronDown className={`h-4 w-4 transition-transform ${isFocusOpen ? 'rotate-180' : ''}`} />
                    </Button>

                    {/* Submenu */}
                    {isFocusOpen && (
                        <div className="pl-4 space-y-1 animate-in slide-in-from-top-1 fade-in duration-200">
                            <Link href="/active-agent/quant-trader-1" onClick={onClose}>
                                <Button
                                    variant="ghost"
                                    className={`w-full justify-start h-10 text-sm ${isActive('/active-agent/quant-trader-1') ? 'bg-indigo-500/20 text-indigo-300' : 'text-white/40 hover:text-white/80 hover:bg-white/5'}`}
                                >
                                    <Bot className="h-4 w-4 mr-3" />
                                    Quant Trader 1
                                    <span className="ml-auto text-[10px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded">LIVE</span>
                                </Button>
                            </Link>
                        </div>
                    )}
                </div>
            </div>

            {/* Footer User Profile (Mock) */}
            <div className="p-4 border-t border-white/10">
                <div className="flex items-center gap-3 p-2 rounded-lg bg-white/5">
                    <div className="h-8 w-8 rounded-full bg-indigo-500 flex items-center justify-center text-white">
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
