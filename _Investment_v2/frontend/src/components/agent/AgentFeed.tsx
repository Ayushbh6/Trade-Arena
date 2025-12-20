import { AgentEvent } from "@/types/agent";
import { Bot, Code2, Terminal, CheckCircle2, AlertCircle, ChevronRight, ChevronDown, Activity, BrainCircuit, User } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Helper to format timestamp safely
function formatTime(timestamp?: string): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';
  return date.toLocaleTimeString();
}

interface AgentFeedProps {
  events: AgentEvent[];
}

export function AgentFeed({ events }: AgentFeedProps) {
  return (
    <div className="flex flex-col gap-6 p-6 pb-20">
      {events.map((event, i) => (
        <FeedItem key={i} event={event} />
      ))}
    </div>
  );
}

function FeedItem({ event }: { event: AgentEvent }) {
  const isQuant = event.source === "quant";
  const isUser = event.source === "user";

  if (event.type === "tool_call") {
    return <ToolCallItem event={event} />;
  }

  if (event.type === "tool_result") {
    return <ToolResultItem event={event} />;
  }

  // User Message
  if (isUser) {
    return (
      <div className="flex gap-4 flex-row-reverse mb-4">
        <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-white text-black">
          <User className="h-4 w-4" />
        </div>
        <div className="flex-1 space-y-2 text-right">
          <div className="flex items-center justify-end gap-2 text-xs uppercase tracking-wider font-semibold opacity-50">
            {formatTime(event.timestamp) && <><span>{formatTime(event.timestamp)}</span><span>•</span></>}
            <span>You</span>
          </div>
          <div className="text-sm leading-relaxed text-white font-medium bg-neutral-900/50 p-3 rounded-2xl rounded-tr-sm border border-white/10 inline-block">
            {event.content}
          </div>
        </div>
      </div>
    );
  }

  // Standard Agent Message (Thought, Decision, Info)
  return (
    <div className={`flex gap-4 ${isQuant ? "pl-12" : ""}`}>
      <div className={`mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 ${isQuant
          ? "bg-purple-500/10 text-purple-400 border border-purple-500/20"
          : "bg-indigo-500/10 text-indigo-400 border border-indigo-500/20"
        }`}>
        {isQuant ? <BrainCircuit className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>

      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider font-semibold opacity-50">
          <span>{event.source}</span>
          {formatTime(event.timestamp) && <><span>•</span><span>{formatTime(event.timestamp)}</span></>}
        </div>

        <div className={`text-sm leading-relaxed text-neutral-300 prose prose-invert prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0 max-w-none`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {event.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

function ToolCallItem({ event }: { event: AgentEvent }) {
  const [expanded, setExpanded] = useState(false);
  const toolName = event.metadata?.tool || "Unknown Tool";
  const args = event.metadata?.args || {};

  return (
    <div className="flex gap-4 pl-4 group">
      {/* Thread Line visual */}
      <div className="w-8 flex flex-col items-center">
        <div className="h-full w-px bg-white/5 group-hover:bg-white/10 transition-colors" />
      </div>

      <div className="flex-1 py-2">
        <div
          onClick={() => setExpanded(!expanded)}
          className="bg-neutral-900/50 border border-white/5 rounded-lg overflow-hidden hover:border-white/10 transition-all cursor-pointer"
        >
          <div className="flex items-center gap-3 p-3">
            <div className="h-6 w-6 rounded bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
              <Terminal className="h-3 w-3 text-blue-400" />
            </div>
            <div className="flex-1 flex items-center gap-2">
              <span className="text-xs font-mono text-blue-300">Tool Call:</span>
              <span className="text-sm font-medium text-white/90">{toolName}</span>
            </div>
            <div className="text-white/20">
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </div>
          </div>

          {expanded && (
            <div className="px-3 pb-3 pt-0">
              <div className="bg-black/50 rounded p-2 text-xs font-mono text-neutral-400 overflow-x-auto">
                {JSON.stringify(args, null, 2)}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ToolResultItem({ event }: { event: AgentEvent }) {
  const [expanded, setExpanded] = useState(false);
  const toolName = event.metadata?.tool || "Tool";
  // Heuristic: Short result (<100 chars) shown inline, long result hidden
  const content = event.content;
  const isShort = content.length < 100;
  const isError = content.toLowerCase().includes("error");

  return (
    <div className="flex gap-4 pl-4 group">
      {/* Thread Line visual */}
      <div className="w-8 flex flex-col items-center">
        <div className="h-full w-px bg-white/5 group-hover:bg-white/10 transition-colors" />
      </div>

      <div className="flex-1 py-1">
        <div className={`
              border rounded-lg overflow-hidden transition-all
              ${isError ? 'bg-red-900/10 border-red-500/20' : 'bg-green-900/10 border-green-500/20'}
          `}>
          <div
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-3 p-2 cursor-pointer"
          >
            <div className={`h-5 w-5 rounded-full flex items-center justify-center border ${isError ? 'bg-red-500 text-white border-red-400' : 'bg-green-500 text-black border-green-400'}`}>
              {isError ? <AlertCircle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
            </div>
            <div className="flex-1">
              <span className="text-xs font-medium opacity-80 mr-2">{toolName} Result</span>
              {isShort && !expanded && (
                <span className="text-xs opacity-50 font-mono">{content}</span>
              )}
            </div>
            {!isShort && (
              <div className="text-white/20">
                {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </div>
            )}
          </div>

          {expanded && (
            <div className="px-3 pb-3 pt-0">
              <div className="bg-black/30 rounded p-2 text-xs font-mono text-neutral-300 overflow-x-auto whitespace-pre-wrap max-h-60 overflow-y-auto custom-scrollbar">
                {content}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}