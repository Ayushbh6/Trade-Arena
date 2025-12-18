import React from 'react';
import { Cpu, Brain } from 'lucide-react';

interface TokenStats {
    prompt: number;
    completion: number;
    total: number;
}

interface TokenCounterProps {
    managerTokens: TokenStats;
    quantTokens: TokenStats;
}

export function TokenCounter({ managerTokens, quantTokens }: TokenCounterProps) {
    return (
        <div className="flex items-center gap-4 mr-4 bg-white/5 px-3 py-1.5 rounded-lg border border-white/5">
            {/* Manager Stats */}
            <div className="flex flex-col items-end min-w-[60px]">
                <div className="flex items-center gap-1.5 text-[10px] font-medium text-indigo-300 uppercase tracking-wider">
                    <Brain className="h-3 w-3" />
                    <span>Manager</span>
                </div>
                <div className="text-xs text-white/80 font-mono font-medium">
                    {managerTokens.total.toLocaleString()}
                </div>
            </div>

            <div className="h-6 w-px bg-white/10" />

            {/* Quant Stats */}
            <div className="flex flex-col items-end min-w-[60px]">
                <div className="flex items-center gap-1.5 text-[10px] font-medium text-emerald-300 uppercase tracking-wider">
                    <Cpu className="h-3 w-3" />
                    <span>Quant</span>
                </div>
                <div className="text-xs text-white/80 font-mono font-medium">
                    {quantTokens.total.toLocaleString()}
                </div>
            </div>
        </div>
    );
}
