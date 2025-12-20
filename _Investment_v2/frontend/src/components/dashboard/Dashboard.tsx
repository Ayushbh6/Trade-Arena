import React, { useMemo, useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts';
import { Activity, TrendingUp, TrendingDown, DollarSign, Wallet, ArrowUpRight, ArrowDownRight, Bell } from 'lucide-react';
import { AgentEvent, TradingSession } from '@/types/agent';

interface DashboardProps {
    events: AgentEvent[];
    activeSession: TradingSession | null;
}

// Mock Data for Charts (Simulating "TradingView" style)
const generateData = (startPrice: number, points: number, volatility: number) => {
    let current = startPrice;
    const data = [];
    for (let i = 0; i < points; i++) {
        const change = (Math.random() - 0.5) * volatility;
        current += change;
        data.push({
            time: `${i}:00`,
            price: current,
        });
    }
    return data;
};

const btcData = generateData(94000, 24, 500);
const ethData = generateData(2800, 24, 30);
const solData = generateData(145, 24, 2);

export function Dashboard({ events, activeSession }: DashboardProps) {
    const [isMounted, setIsMounted] = useState(false);

    useEffect(() => {
        setIsMounted(true);
    }, []);

    // Extract dynamic data from events
    const portfolioEvent = [...events].reverse().find(e => e.type === 'tool_result' && e.metadata?.tool === 'get_portfolio_state');

    const portfolioData = useMemo(() => {
        if (!portfolioEvent) return null;
        try {
            return JSON.parse(portfolioEvent.content);
        } catch (e) {
            return null;
        }
    }, [portfolioEvent]);

    const balance = portfolioData?.USDT_Free ?? 10000;
    const initialBalance = activeSession?.initial_balance ?? 10000;
    const pnl = balance - initialBalance;
    const pnlPct = ((pnl / initialBalance) * 100).toFixed(2);

    const activePositions = portfolioData?.Positions?.length ?? 0;
    const positionNames = portfolioData?.Positions?.map((p: string) => p.split(':')[0]).join(', ') || 'No active trades';

    // Filter critical events for the Activity Stream
    const criticalEvents = useMemo(() => {
        return events.filter(e => {
            // Include Decisions
            if (e.type === 'decision') return true;
            // Include System Done
            if (e.type === 'system' && e.metadata?.status === 'done') return true;
            // Include Key Manager Tool Calls
            if (e.source === 'manager' && e.type === 'tool_call') return true;
            // Include Key Quant Actions
            if (e.source === 'quant' && (e.type === 'tool_call' || e.type === 'tool_result')) return true;
            // Include Manager Info (Starting, etc)
            if (e.source === 'manager' && e.type === 'info' && e.content.includes("Starting")) return true;

            return false;
        }).slice(-20).reverse(); // Keeping more history since they are smaller now
    }, [events]);

    return (
        <div className="h-full w-full p-6 space-y-6 overflow-y-auto custom-scrollbar bg-black/40">

            {/* Header */}
            <div>
                <h2 className="text-xl font-medium tracking-wide text-white/90">Investment Dashboard</h2>
                <p className="text-sm text-white/40">Live market overview and agent performance</p>
            </div>

            {/* KPI Cards Row */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <Card title="Total Balance" value={`$${balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} change={`${pnl >= 0 ? '+' : ''}${pnlPct}%`} positive={pnl >= 0} icon={Wallet} />
                <Card title="Session PnL" value={`${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(2)}`} change={`${pnl >= 0 ? '+' : ''}${pnlPct}%`} positive={pnl >= 0} icon={TrendingUp} />
                <Card title="Active Positions" value={activePositions.toString()} subtext={positionNames} icon={Activity} />
                <Card title="Agent Win Rate" value="72%" change="+1.4%" positive={true} icon={TrendingDown} />
            </div>

            {/* Main Charts Area */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[400px]">

                {/* Main Market Chart (BTC) */}
                <div className="lg:col-span-2 bg-white/5 border border-white/10 rounded-xl p-4 flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <div className="h-8 w-8 rounded-full bg-orange-500/20 flex items-center justify-center text-orange-400 font-bold text-xs">₿</div>
                            <div>
                                <h3 className="text-sm font-medium text-white">BTC/USDT</h3>
                                <span className="text-xs text-white/40">Bitcoin Perpetual</span>
                            </div>
                        </div>
                        <div className="text-right">
                            <div className="text-sm font-medium text-white">$94,230.50</div>
                            <div className="text-xs text-emerald-400">+2.4%</div>
                        </div>
                    </div>
                    <div className="flex-1 min-h-0">
                        {isMounted && (
                            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                                <AreaChart data={btcData}>
                                    <defs>
                                        <linearGradient id="colorBtc" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#f97316" stopOpacity={0.2} />
                                            <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
                                        </linearGradient>
                                    </defs>
                                    <XAxis dataKey="time" hide />
                                    <YAxis domain={['auto', 'auto']} hide />
                                    <Tooltip
                                        contentStyle={{ backgroundColor: 'rgba(0, 0, 0, 0.8)', backdropFilter: 'blur(8px)', borderColor: 'rgba(255, 255, 255, 0.1)', borderRadius: '12px', fontSize: '11px', boxShadow: '0 4px 12px rgba(0,0,0,0.5)' }}
                                        itemStyle={{ color: '#fff' }}
                                        formatter={(value: any) => [`$${parseFloat(value).toFixed(2)}`, 'Price']}
                                        labelStyle={{ color: 'rgba(255,255,255,0.4)', marginBottom: '4px' }}
                                    />
                                    <Area
                                        type="monotone"
                                        dataKey="price"
                                        stroke="#f97316"
                                        fillOpacity={1}
                                        fill="url(#colorBtc)"
                                        strokeWidth={3}
                                        animationDuration={1500}
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        )}
                    </div>
                </div>

                {/* Activity Feed - FIXED HEIGHT */}
                <div className="h-full bg-white/5 border border-white/10 rounded-xl p-4 flex flex-col overflow-hidden">
                    <div className="flex items-center gap-2 mb-4 flex-none">
                        <Bell className="h-4 w-4 text-indigo-400" />
                        <h3 className="text-sm font-medium text-white">Live Activity</h3>
                    </div>
                    <div className="flex-1 overflow-y-auto space-y-2 custom-scrollbar pr-2">
                        {criticalEvents.length === 0 ? (
                            <div className="text-center text-white/30 text-xs py-10">No recent trading activity</div>
                        ) : (
                            criticalEvents.map((e, i) => (
                                <div key={i} className="flex gap-3 items-center p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-colors">
                                    <div className={`flex-none h-1.5 w-1.5 rounded-full ${getActionColor(e)}`} />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-baseline justify-between gap-2">
                                            <div className="text-xs text-white/90 font-medium truncate">
                                                {formatEventTitle(e)}
                                            </div>
                                            <div className="text-[10px] text-white/30 whitespace-nowrap flex-none">
                                                {e.timestamp ? new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                                            </div>
                                        </div>
                                        <div className="text-[11px] text-white/50 truncate">
                                            {formatEventContent(e)}
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>

            {/* Secondary Coins Row */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 h-[180px]">
                <SmallChart symbol="ETH" name="Ethereum" price="$2,840.10" change="+1.2%" data={ethData} color="#818cf8" isMounted={isMounted} />
                <SmallChart symbol="SOL" name="Solana" price="$145.20" change="-0.5%" data={solData} color="#a855f7" isMounted={isMounted} />
            </div>

        </div>
    );
}

// --- Helper Components ---

function Card({ title, value, change, subtext, positive, icon: Icon }: any) {
    return (
        <div className="bg-white/5 border border-white/10 rounded-xl p-4 flex flex-col justify-between hover:bg-white/10 transition-colors group">
            <div className="flex justify-between items-start">
                <span className="text-xs text-white/40 font-medium uppercase tracking-wider">{title}</span>
                <div className="h-8 w-8 bg-white/5 rounded-lg flex items-center justify-center text-white/40 group-hover:text-white transition-colors">
                    <Icon className="h-4 w-4" />
                </div>
            </div>
            <div className="mt-2">
                <div className="text-2xl font-medium text-white tracking-tight">{value}</div>
                <div className="flex items-center gap-2 mt-1">
                    {change && (
                        <span className={`text-xs font-medium flex items-center ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
                            {positive ? <ArrowUpRight className="h-3 w-3 mr-0.5" /> : <ArrowDownRight className="h-3 w-3 mr-0.5" />}
                            {change}
                        </span>
                    )}
                    {subtext && <span className="text-xs text-white/30">{subtext}</span>}
                </div>
            </div>
        </div>
    );
}

function SmallChart({ symbol, name, price, change, data, color, isMounted }: any) {
    return (
        <div className="bg-white/5 border border-white/10 rounded-xl p-4 flex items-center justify-between hover:bg-white/10 transition-colors">
            <div className="space-y-1">
                <div className="flex items-center gap-2">
                    <span className="font-bold text-sm text-white">{symbol}</span>
                    <span className="text-xs text-white/40">{name}</span>
                </div>
                <div className="text-lg font-medium text-white">{price}</div>
                <div className={`text-xs font-medium ${change.startsWith('+') ? 'text-emerald-400' : 'text-red-400'}`}>
                    {change}
                </div>
            </div>
            <div className="h-16 w-32">
                {isMounted && (
                    <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
                        <LineChart data={data}>
                            <Line type="monotone" dataKey="price" stroke={color} strokeWidth={2} dot={false} />
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    )
}

// --- formatters ---

function formatEventTitle(e: AgentEvent) {
    if (e.source === 'quant') return 'Quant Analyst';
    if (e.type === 'decision') return 'Manager Decision';
    if (e.type === 'tool_call') return `Using Tool: ${e.metadata?.tool || 'System'}`;
    return 'System Update';
}

function formatEventContent(e: AgentEvent) {
    const text = e.content;

    // 1. Manager Decisions: Extract the recommendation
    if (e.type === 'decision') {
        // Look for "Decision:" or "Action:" or specific patterns
        const match = text.match(/\*\*(Recommend|Decision|Strategy).*?\*\*[:\s]*(.*?)(?=\.|\n|$)/i);
        if (match && match[2]) return match[2].trim();

        // Fallback: If it's a short text, show it, otherwise truncate
        return text.length > 50 ? text.substring(0, 50) + "..." : text;
    }

    // 2. Tool Calls
    if (e.type === 'tool_call') {
        return `Calling ${e.metadata?.tool} with params...`;
    }

    // 3. Tool Results
    if (e.type === 'tool_result') {
        if (e.metadata?.tool === 'execute_python') return 'Code execution complete.';
        return 'Result received.';
    }

    // 4. Quant Messages
    if (e.source === 'quant') {
        if (text.includes("thought")) return "Analyzing market data...";
        if (text.includes("python")) return "Generating analysis code...";
    }

    return text.length > 60 ? text.substring(0, 60) + "..." : text;
}

function getActionColor(e: AgentEvent) {
    if (e.type === 'decision') return 'bg-indigo-500';
    if (e.type === 'error') return 'bg-red-500';
    if (e.source === 'quant') return 'bg-cyan-500';
    if (e.content.includes("complete")) return 'bg-emerald-500';
    return 'bg-white/40';
}
