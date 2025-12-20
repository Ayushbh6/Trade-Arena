import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Play, Square, Timer, AlertCircle } from "lucide-react";

interface RunControlProps {
    isRunning: boolean;
    onStart: (cadenceMinutes: number, runLimit?: number | null) => void;
    onStop: () => void;
    onRunOnce: () => void;
}

export function RunControl({ isRunning, onStart, onStop, onRunOnce }: RunControlProps) {
    const [cadenceStr, setCadenceStr] = useState("10");
    const [runLimitStr, setRunLimitStr] = useState("");
    const [error, setError] = useState<string | null>(null);

    const handleStart = () => {
        const cadence = parseInt(cadenceStr);
        if (isNaN(cadence) || cadence < 2 || cadenceStr.includes('.')) {
            setError("Cadence must be an integer >= 2");
            return;
        }
        let runLimit: number | null = null;
        if (runLimitStr.trim() !== "") {
            const limit = parseInt(runLimitStr);
            if (isNaN(limit) || limit < 1 || runLimitStr.includes('.')) {
                setError("Runs must be an integer >= 1");
                return;
            }
            runLimit = limit;
        }
        setError(null);
        onStart(cadence, runLimit);
    };

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const val = e.target.value;
        setCadenceStr(val);

        if (val === "") {
            setError(null);
            return;
        }

        const num = parseInt(val);
        if (isNaN(num) || num < 2 || val.includes('.')) {
            setError("Cadence min 2 mins (integer)");
        } else {
            setError(null);
        }
    };

    const handleRunLimitChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const val = e.target.value;
        setRunLimitStr(val);

        if (val === "") {
            setError(null);
            return;
        }

        const num = parseInt(val);
        if (isNaN(num) || num < 1 || val.includes('.')) {
            setError("Runs must be integer >= 1");
        } else {
            setError(null);
        }
    };

    return (
        <div className="flex flex-col items-center gap-1">
            <div className="flex items-center gap-2 bg-white/5 rounded-lg p-1 border border-white/10">
                <div className="flex items-center gap-2 px-2 border-r border-white/10">
                    <Timer className="h-3 w-3 text-white/40" />
                    <Input
                        type="text"
                        value={cadenceStr}
                        onChange={handleChange}
                        className="h-6 w-12 bg-transparent border-0 p-0 text-xs text-center text-white focus-visible:ring-0"
                    />
                    <span className="text-[10px] text-white/40">min</span>
                </div>

                {isRunning ? (
                    <Button
                        size="sm"
                        variant="destructive"
                        onClick={onStop}
                        className="h-6 px-3 text-xs bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/20"
                    >
                        <Square className="h-3 w-3 mr-1.5 fill-current" />
                        Stop
                    </Button>
                ) : (
                    <div className="flex items-center gap-1">
                        <Button
                            size="sm"
                            onClick={onRunOnce}
                            className="h-6 px-3 text-xs bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 border border-indigo-500/20"
                        >
                            <Play className="h-3 w-3 mr-1.5 fill-current" />
                            Run Once
                        </Button>
                        <div className="w-px h-4 bg-white/10 mx-1" />
                        <Button
                            size="sm"
                            onClick={handleStart}
                            className="h-6 px-3 text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border border-emerald-500/20"
                        >
                            <Play className="h-3 w-3 mr-1.5 fill-current" />
                            Start Loop
                        </Button>
                        <div className="flex items-center gap-1 bg-white/5 rounded-md px-2 h-6 border border-white/10">
                            <Input
                                type="text"
                                value={runLimitStr}
                                onChange={handleRunLimitChange}
                                placeholder="∞"
                                className="h-5 w-10 bg-transparent border-0 p-0 text-xs text-center text-white focus-visible:ring-0 placeholder:text-white/30"
                            />
                            <span className="text-[10px] text-white/40">runs</span>
                        </div>
                    </div>
                )}
            </div>
            {error && (
                <div className="flex items-center gap-1 text-[10px] text-red-400 animate-in fade-in slide-in-from-top-1">
                    <AlertCircle className="h-2.5 w-2.5" />
                    {error}
                </div>
            )}
        </div>
    );
}
