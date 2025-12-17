import { useState } from "react";
import Badge from "./Badge";

interface ControlPanelProps {
  onStart: (config: StartConfig) => void;
  onStop: () => void;
  status: ControlStatus;
}

export interface StartConfig {
  traders: string[];
  cycles: number;
  dry_run: boolean;
}

export interface ControlStatus {
  running: boolean;
  run_id: string | null;
  current_cycle?: number;
  total_cycles?: number;
  status?: string;
}

const TRADERS = [
  { id: "tech_trader_1", name: "Technical Agent 1" },
  { id: "tech_trader_2", name: "Technical Agent 2" },
  { id: "macro_trader_1", name: "Macro Agent" },
  { id: "structure_trader_1", name: "Structural Agent" },
];

const CYCLE_PRESETS = [
  { label: "Once", value: 1 },
  { label: "Full Day (~240)", value: 240 },
  { label: "Custom", value: -1 },
];

export default function ControlPanel({ onStart, onStop, status }: ControlPanelProps) {
  const [selectedTraders, setSelectedTraders] = useState<string[]>(TRADERS.map(t => t.id));
  const [cycleMode, setCycleMode] = useState<number>(1); // 1=once, 240=full day, -1=custom
  const [customCycles, setCustomCycles] = useState<number>(10);
  const [dryRun, setDryRun] = useState<boolean>(true);

  const toggleTrader = (traderId: string) => {
    setSelectedTraders(prev => 
      prev.includes(traderId)
        ? prev.filter(t => t !== traderId)
        : [...prev, traderId]
    );
  };

  const toggleAll = () => {
    setSelectedTraders(prev => 
      prev.length === TRADERS.length ? [] : TRADERS.map(t => t.id)
    );
  };

  const handleStart = () => {
    const cycles = cycleMode === -1 ? customCycles : cycleMode;
    onStart({
      traders: selectedTraders,
      cycles,
      dry_run: dryRun,
    });
  };

  const statusBadge = status.running ? (
    <Badge tone="good">
      Running - Cycle {status.current_cycle || 0}/{status.total_cycles || "?"}
    </Badge>
  ) : status.status === "completed" ? (
    <Badge>Completed</Badge>
  ) : (
    <Badge tone="neutral">Idle</Badge>
  );

  return (
    <section className="flex h-full flex-col rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--panel))] shadow-crisp">
      <header className="flex items-center justify-between border-b border-[rgb(var(--border))] px-4 py-3">
        <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">Trading Control</div>
        {statusBadge}
      </header>
      <div className="flex flex-1 flex-col p-4">

        {/* Trader Selection */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <label className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">Traders</label>
            <button
              type="button"
              onClick={toggleAll}
              disabled={status.running}
              className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-2 py-1 text-xs text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))] disabled:opacity-50"
            >
              {selectedTraders.length === TRADERS.length ? "Deselect all" : "Select all"}
            </button>
          </div>
        <div className="grid grid-cols-2 gap-2">
          {TRADERS.map(trader => (
            <label
              key={trader.id}
              className="flex cursor-pointer items-center gap-2 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-2 text-sm hover:border-[rgb(var(--fg))]"
            >
              <input
                type="checkbox"
                checked={selectedTraders.includes(trader.id)}
                onChange={() => toggleTrader(trader.id)}
                disabled={status.running}
                className="h-4 w-4"
              />
              <span className={selectedTraders.includes(trader.id) ? "" : "text-gray-400"}>
                {trader.name}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* Cycle Mode */}
      <div className="mt-4">
        <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">Run Mode</label>
        <div className="flex gap-2 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-1">
          {CYCLE_PRESETS.map(preset => (
            <button
              key={preset.label}
              type="button"
              onClick={() => setCycleMode(preset.value)}
              disabled={status.running}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
                cycleMode === preset.value
                  ? "bg-[rgb(var(--fg))] text-[rgb(var(--bg))]"
                  : "text-[rgb(var(--muted))] hover:text-[rgb(var(--fg))]"
              } disabled:opacity-50`}
            >
              {preset.label}
            </button>
          ))}
        </div>
        {cycleMode === -1 && (
          <input
            type="number"
            value={customCycles}
            onChange={(e) => setCustomCycles(Math.max(1, parseInt(e.target.value) || 1))}
            disabled={status.running}
            className="mt-2 w-full rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] px-3 py-2 text-sm"
            placeholder="Number of cycles"
            min="1"
            max="10000"
          />
        )}
      </div>

      {/* Dry Run Toggle */}
      <div className="mt-4">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={status.running}
            className="h-4 w-4"
          />
          <span className="text-[rgb(var(--muted))]">Dry run (no testnet execution)</span>
        </label>
      </div>

      {/* Start/Stop Button */}
      <div className="mt-5">
        {status.running ? (
          <button
            type="button"
            onClick={onStop}
            className="w-full rounded-lg bg-red-600 px-4 py-2 font-semibold text-white hover:bg-red-700 transition-colors"
          >
            STOP
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStart}
            disabled={selectedTraders.length === 0}
            className="w-full rounded-lg bg-green-600 px-4 py-2 font-semibold text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
          >
            START
          </button>
        )}
      </div>

      {/* Run ID Display */}
      {status.run_id && (
        <div className="mt-4 rounded-lg border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[rgb(var(--muted))]">Run ID</div>
          <div className="mt-1 font-mono text-xs">{status.run_id}</div>
        </div>
      )}
      </div>
    </section>
  );
}
