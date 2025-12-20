import React, { useState, useEffect } from 'react';
import { AgentEvent } from '@/types/agent';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Terminal, Code, Database, Box, AlertCircle, CheckCircle2, Copy } from 'lucide-react';

interface ArtifactViewerProps {
  events: AgentEvent[];
}

export function ArtifactViewer({ events }: ArtifactViewerProps) {
  const codeEvents = events.filter(e => e.type === 'code');
  const observationEvents = events.filter(e => e.type === 'observation' || e.type === 'tool_result');
  
  const [activeTab, setActiveTab] = useState("console");

  useEffect(() => {
    const lastEvent = events[events.length - 1];
    if (!lastEvent) return;
    
    if (lastEvent.type === 'code') setActiveTab("code");
    if (lastEvent.type === 'observation' || lastEvent.type === 'tool_result') setActiveTab("console");
  }, [events]);

  return (
    <div className="h-full flex flex-col bg-[#0A0A0A] text-gray-300 font-mono text-sm relative border-l border-white/10">
      {/* Top Bar */}
      <div className="h-14 flex-none border-b border-white/10 flex items-center justify-between px-6 bg-black/40 backdrop-blur-md">
        <div className="flex items-center gap-2 text-xs font-bold text-neutral-400 uppercase tracking-widest">
            <Box className="h-4 w-4 text-indigo-500" />
            <span>Workspace Artifacts</span>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <div className="border-b border-white/5 bg-black/20">
          <TabsList className="h-10 bg-transparent p-0 w-full justify-start rounded-none">
            <TabsTrigger 
                value="console" 
                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-emerald-500 data-[state=active]:bg-white/5 data-[state=active]:text-emerald-400 px-6 text-xs font-bold uppercase tracking-wider transition-all"
            >
                <Terminal className="mr-2 h-3.5 w-3.5"/> Console Output
            </TabsTrigger>
            <TabsTrigger 
                value="code" 
                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-white/5 data-[state=active]:text-blue-400 px-6 text-xs font-bold uppercase tracking-wider transition-all"
            >
                <Code className="mr-2 h-3.5 w-3.5"/> Code History
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="code" className="flex-1 p-0 m-0 overflow-hidden relative data-[state=inactive]:hidden">
          <ScrollArea className="h-full w-full">
            <div className="p-0">
              {codeEvents.length === 0 && (
                <div className="flex flex-col items-center justify-center h-64 text-neutral-600 gap-3">
                    <div className="h-12 w-12 rounded-full bg-white/5 flex items-center justify-center">
                        <Code className="h-6 w-6 opacity-40" />
                    </div>
                    <span className="text-xs font-medium uppercase tracking-wider">No code executed yet</span>
                </div>
              )}
              {codeEvents.map((e, i) => (
                <CodeItem key={i} event={e} index={i} />
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="console" className="flex-1 p-0 m-0 overflow-hidden data-[state=inactive]:hidden">
          <ScrollArea className="h-full w-full">
            <div className="p-4 space-y-4">
              {observationEvents.length === 0 && (
                 <div className="flex flex-col items-center justify-center h-64 text-neutral-600 gap-3">
                    <div className="h-12 w-12 rounded-full bg-white/5 flex items-center justify-center">
                        <Terminal className="h-6 w-6 opacity-40" />
                    </div>
                    <span className="text-xs font-medium uppercase tracking-wider">Waiting for output...</span>
                </div>
              )}
              {observationEvents.map((e, i) => (
                <ConsoleItem key={i} event={e} />
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function CodeItem({ event, index }: { event: AgentEvent, index: number }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        navigator.clipboard.writeText(event.content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="border-b border-white/5 last:border-0 group">
            <div className="bg-white/[0.02] px-4 py-2 text-[10px] text-neutral-500 flex justify-between items-center font-sans border-b border-white/5">
                <div className="flex items-center gap-2">
                    <span className="font-bold text-blue-400/70">IN [{index + 1}]</span>
                    <span className="text-neutral-600">•</span>
                    <span className="opacity-50 group-hover:opacity-100 transition-opacity">Python 3.10</span>
                </div>
                <button 
                    onClick={handleCopy}
                    className="hover:text-white transition-colors"
                >
                    {copied ? <CheckCircle2 className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
                </button>
            </div>
            <pre className="p-4 overflow-x-auto text-blue-100/90 leading-relaxed selection:bg-blue-500/30 text-xs font-mono">
                {event.content}
            </pre>
        </div>
    );
}

function ConsoleItem({ event }: { event: AgentEvent }) {
    const isTool = event.type === 'tool_result';
    const toolName = event.metadata?.tool;
    
    // Parse content
    let content = event.content;
    let isJson = false;
    let jsonContent = null;

    if (isTool) {
        try {
            jsonContent = JSON.parse(content);
            isJson = true;
        } catch (e) {}
    }

    // Split STDOUT/STDERR for observations
    let stdout = "";
    let stderr = "";
    
    if (!isTool) {
        const parts = content.split(/(STDOUT:|STDERR:)/);
        let currentSection = "other";
        parts.forEach(part => {
            if (part === "STDOUT:") currentSection = "stdout";
            else if (part === "STDERR:") currentSection = "stderr";
            else {
                if (currentSection === "stdout") stdout += part;
                else if (currentSection === "stderr") stderr += part;
                else if (currentSection === "other") stdout += part; // Default to stdout if no prefix
            }
        });
    }

    return (
        <div className="rounded-lg overflow-hidden border border-white/5 bg-white/[0.02]">
            <div className={`flex items-center gap-2 px-3 py-2 border-b border-white/5 ${isTool ? 'bg-cyan-950/10' : 'bg-neutral-900/50'}`}>
                {isTool ? <Database className="h-3.5 w-3.5 text-cyan-500"/> : <Terminal className="h-3.5 w-3.5 text-amber-500"/>}
                <span className="uppercase text-[10px] font-bold tracking-wider text-neutral-400">
                    {isTool ? `Result: ${toolName || 'Unknown'}` : `System Output`}
                </span>
            </div>
            
            <div className="p-0 text-xs text-neutral-300 font-mono leading-relaxed selection:bg-neutral-700">
                {isTool ? (
                    <div className="p-3 overflow-x-auto">
                        {isJson ? (
                            <pre className="text-cyan-100/80">{JSON.stringify(jsonContent, null, 2)}</pre>
                        ) : (
                            <pre className="whitespace-pre-wrap text-cyan-100/80">{content}</pre>
                        )}
                    </div>
                ) : (
                    <>
                        {stdout.trim() && (
                            <div className="p-3 border-b border-white/5 last:border-0">
                                <pre className="whitespace-pre-wrap text-neutral-300">{stdout.trim()}</pre>
                            </div>
                        )}
                        {stderr.trim() && (
                            <div className="p-3 bg-red-900/10 border-t border-red-500/10">
                                <div className="flex items-center gap-2 text-red-400 mb-1 text-[10px] font-bold uppercase tracking-wider">
                                    <AlertCircle className="h-3 w-3" /> Stderr
                                </div>
                                <pre className="whitespace-pre-wrap text-red-200/80">{stderr.trim()}</pre>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}