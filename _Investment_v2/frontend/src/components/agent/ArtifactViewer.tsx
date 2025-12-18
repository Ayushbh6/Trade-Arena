import React, { useState, useEffect } from 'react';
import { AgentEvent } from '@/types/agent';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Terminal, Code, Database, Box } from 'lucide-react';

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
    <div className="h-full flex flex-col bg-[#0A0A0A] text-gray-300 font-mono text-sm relative">
      {/* Top Bar */}
      <div className="h-10 flex-none border-b border-white/5 flex items-center justify-between px-4 bg-black/40">
        <div className="flex items-center gap-2 text-xs font-medium text-neutral-500 uppercase tracking-widest">
            <Box className="h-3 w-3" />
            <span>Workspace</span>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <div className="border-b border-white/5 bg-black/20">
          <TabsList className="h-10 bg-transparent p-0 w-full justify-start rounded-none">
            <TabsTrigger 
                value="console" 
                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-emerald-500 data-[state=active]:bg-white/5 data-[state=active]:text-emerald-400 px-4 text-xs font-medium transition-all"
            >
                <Terminal className="mr-2 h-3.5 w-3.5"/> Console
            </TabsTrigger>
            <TabsTrigger 
                value="code" 
                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-white/5 data-[state=active]:text-blue-400 px-4 text-xs font-medium transition-all"
            >
                <Code className="mr-2 h-3.5 w-3.5"/> Code History
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="code" className="flex-1 p-0 m-0 overflow-hidden relative data-[state=inactive]:hidden">
          <ScrollArea className="h-full w-full">
            <div className="p-0">
              {codeEvents.length === 0 && (
                <div className="flex flex-col items-center justify-center h-64 text-neutral-600 gap-2">
                    <Code className="h-8 w-8 opacity-20" />
                    <span className="text-xs">No code executed yet</span>
                </div>
              )}
              {codeEvents.map((e, i) => (
                <div key={i} className="border-b border-white/5 last:border-0 group">
                  <div className="bg-white/5 px-4 py-1.5 text-[10px] text-neutral-500 flex justify-between items-center font-sans">
                    <span className="font-semibold text-blue-400/50">IN [{i+1}]</span>
                    <span className="opacity-0 group-hover:opacity-100 transition-opacity">Python 3.10</span>
                  </div>
                  <pre className="p-4 overflow-x-auto text-blue-100/90 leading-relaxed selection:bg-blue-500/30">
                    {e.content}
                  </pre>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="console" className="flex-1 p-0 m-0 overflow-hidden data-[state=inactive]:hidden">
          <ScrollArea className="h-full w-full">
            <div className="p-4 space-y-4">
              {observationEvents.length === 0 && (
                 <div className="flex flex-col items-center justify-center h-64 text-neutral-600 gap-2">
                    <Terminal className="h-8 w-8 opacity-20" />
                    <span className="text-xs">Waiting for output...</span>
                </div>
              )}
              {observationEvents.map((e, i) => (
                <div key={i} className="rounded-lg overflow-hidden border border-white/5 bg-white/[0.02]">
                    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-white/5 bg-white/5">
                         {e.type === 'tool_result' ? <Database className="h-3 w-3 text-cyan-500"/> : <Terminal className="h-3 w-3 text-amber-500"/>}
                         <span className="uppercase text-[10px] font-bold tracking-wider text-neutral-500">{e.type} &mdash; {e.source}</span>
                    </div>
                    <div className="p-3 text-xs text-neutral-300 whitespace-pre-wrap font-mono leading-relaxed selection:bg-neutral-700">
                        {e.content}
                    </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}