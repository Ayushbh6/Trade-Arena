import { AgentEvent } from "@/types/agent";
import { 
  Bot, 
  Code2, 
  Terminal, 
  CheckCircle2, 
  AlertCircle, 
  ChevronRight, 
  ChevronDown, 
  BrainCircuit, 
  User, 
  Cpu,
  Flag,
  Activity,
  Cloud,
  History
} from "lucide-react";
import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --- Helpers ---

function formatTime(timestamp?: string): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function humanizeToolName(name: string): string {
  return name
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// --- Main Component ---

interface AgentFeedProps {
  events: AgentEvent[];
}

export function AgentFeed({ events }: AgentFeedProps) {
  return (
    <div className="flex flex-col gap-6 p-6 pb-20 max-w-4xl mx-auto w-full">
      {events.map((event, i) => (
        <FeedItem key={i} event={event} />
      ))}
    </div>
  );
}

function FeedItem({ event }: { event: AgentEvent }) {
  const isUser = event.source === "user";

  if (isUser) {
    return <UserMessage event={event} />;
  }

  switch (event.type) {
    case "thought":
      return <ThoughtBlock event={event} />;
    case "code":
      return <CodeBlock event={event} />;
    case "observation":
      return <ObservationBlock event={event} />;
    case "tool_call":
      return <ToolCallBlock event={event} />;
    case "tool_result":
      return <ToolResultBlock event={event} />;
    case "decision":
      return <DecisionBlock event={event} />;
    case "error":
      return <ErrorBlock event={event} />;
    case "memory":
      return <MemoryCloud event={event} />;
    case "info":
      // Manager/System info messages are important context, render prominently
      return <InfoMessage event={event} />;
    case "system":
    default:
      return <SystemMessage event={event} />;
  }
}

// --- Sub-Components ---

function UserMessage({ event }: { event: AgentEvent }) {
  return (
    <div className="flex gap-4 flex-row-reverse group">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-white text-black shadow-lg shadow-white/10">
        <User className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-1 text-right max-w-[80%]">
        <div className="flex items-center justify-end gap-2 text-[10px] uppercase tracking-wider font-bold text-neutral-500">
          {formatTime(event.timestamp) && <span>{formatTime(event.timestamp)}</span>}
          <span>•</span>
          <span>You</span>
        </div>
        <div className="text-sm leading-relaxed text-white font-medium bg-neutral-800/80 p-4 rounded-2xl rounded-tr-sm border border-white/10 inline-block text-left shadow-sm">
          {event.content}
        </div>
      </div>
    </div>
  );
}

function ThoughtBlock({ event }: { event: AgentEvent }) {
  const [isMinimized, setIsMinimized] = useState(false);
  
  // Pre-process content to ensure numbered lists have newlines and are treated as markdown lists
  // We look for " 2. " or " 3. " and ensure they start on a new line.
  const formattedContent = event.content
    .replace(/(\d+\.) /g, '\n$1 ')
    .trim();

  return (
    <div className="flex gap-4 group">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-purple-500/10 text-purple-400 border border-purple-500/20 shadow-[0_0_15px_-3px_rgba(168,85,247,0.15)]">
        <BrainCircuit className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-2 min-w-0">
        <HeaderRow 
          source="Quant Researcher" 
          timestamp={event.timestamp} 
          color="text-purple-400" 
          onToggle={() => setIsMinimized(!isMinimized)}
          isMinimized={isMinimized}
        />
        
        {!isMinimized && (
          <div className="bg-neutral-900/30 border border-white/5 rounded-xl p-5 relative overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
            <div className="absolute top-0 left-0 w-1 h-full bg-purple-500/20" />
            <div className="prose prose-invert prose-sm max-w-none prose-p:leading-relaxed prose-li:marker:text-purple-500/50 whitespace-pre-line">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {formattedContent}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CodeBlock({ event }: { event: AgentEvent }) {
  const [copied, setCopied] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(event.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex gap-4 group">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-blue-500/10 text-blue-400 border border-blue-500/20 shadow-[0_0_15px_-3px_rgba(59,130,246,0.15)]">
        <Code2 className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-2 min-w-0">
        <HeaderRow 
          source="Quant Researcher" 
          timestamp={event.timestamp} 
          color="text-blue-400" 
          suffix="Executing Code" 
          onToggle={() => setIsMinimized(!isMinimized)}
          isMinimized={isMinimized}
        />
        
        {!isMinimized && (
          <div className="rounded-xl overflow-hidden border border-blue-500/20 bg-[#0d1117] shadow-lg animate-in fade-in slide-in-from-top-1 duration-200">
            <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/5">
              <div className="flex items-center gap-2">
                <div className="flex gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-red-500/20 border border-red-500/50" />
                  <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/20 border border-yellow-500/50" />
                  <div className="w-2.5 h-2.5 rounded-full bg-green-500/20 border border-green-500/50" />
                </div>
                <span className="text-xs font-mono text-blue-300/50 ml-2">script.py</span>
              </div>
              <button 
                onClick={handleCopy}
                className="text-[10px] font-medium uppercase tracking-wider text-neutral-500 hover:text-white transition-colors"
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <div className="p-4 overflow-x-auto custom-scrollbar">
              <pre className="text-sm font-mono text-blue-100/90 leading-relaxed">
                <code>{event.content}</code>
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ObservationBlock({ event }: { event: AgentEvent }) {
  const [isMinimized, setIsMinimized] = useState(false);
  // Simple parser to split STDOUT and STDERR
  const content = event.content;
  const hasStdout = content.includes("STDOUT:");
  const hasStderr = content.includes("STDERR:");
  
  let stdout = "";
  let stderr = "";
  let other = "";

  if (hasStdout || hasStderr) {
    const parts = content.split(/(STDOUT:|STDERR:)/);
    let currentSection = "other";
    
    parts.forEach(part => {
      if (part === "STDOUT:") currentSection = "stdout";
      else if (part === "STDERR:") currentSection = "stderr";
      else {
        if (currentSection === "stdout") stdout += part;
        else if (currentSection === "stderr") stderr += part;
        else other += part;
      }
    });
  } else {
    other = content;
  }

  return (
    <div className="flex gap-4 group">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-neutral-800 text-neutral-400 border border-white/10">
        <Terminal className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-2 min-w-0">
        <HeaderRow 
          source="System" 
          timestamp={event.timestamp} 
          color="text-neutral-400" 
          suffix="Output" 
          onToggle={() => setIsMinimized(!isMinimized)}
          isMinimized={isMinimized}
        />
        
        {!isMinimized && (
          <div className="space-y-2 animate-in fade-in slide-in-from-top-1 duration-200">
            {(stdout.trim() || other.trim()) && (
              <div className="rounded-lg border border-white/10 bg-black/40 overflow-hidden">
                <div className="px-3 py-1.5 bg-white/5 border-b border-white/5 text-[10px] font-bold text-neutral-500 uppercase tracking-wider">
                  Standard Output
                </div>
                <pre className="p-3 text-xs font-mono text-neutral-300 whitespace-pre-wrap max-h-60 overflow-y-auto custom-scrollbar">
                  {stdout.trim() || other.trim()}
                </pre>
              </div>
            )}
            
            {stderr.trim() && (
              <div className="rounded-lg border border-red-500/20 bg-red-900/5 overflow-hidden">
                <div className="px-3 py-1.5 bg-red-500/10 border-b border-red-500/10 text-[10px] font-bold text-red-400 uppercase tracking-wider flex items-center gap-2">
                  <AlertCircle className="h-3 w-3" /> Standard Error
                </div>
                <pre className="p-3 text-xs font-mono text-red-300/90 whitespace-pre-wrap">
                  {stderr.trim()}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCallBlock({ event }: { event: AgentEvent }) {
  const [expanded, setExpanded] = useState(false);
  const toolName = event.metadata?.tool || "Unknown Tool";
  const args = event.metadata?.args || {};
  const niceName = humanizeToolName(toolName);

  return (
    <div className="flex gap-4 pl-4 group relative">
      {/* Thread Line */}
      <div className="absolute left-[2.4rem] top-8 bottom-[-2rem] w-px bg-white/5 group-last:hidden" />

      <div className="mt-1 h-6 w-6 rounded-md flex items-center justify-center flex-shrink-0 bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 z-10">
        <Cpu className="h-3 w-3" />
      </div>
      
      <div className="flex-1 min-w-0">
        <div 
          onClick={() => setExpanded(!expanded)}
          className="inline-flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 bg-neutral-900/50 border border-white/5 rounded-lg px-3 py-2 hover:border-cyan-500/30 transition-colors cursor-pointer max-w-full"
        >
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-cyan-100">{niceName}</span>
            <span className="text-[10px] font-mono text-cyan-500/50 bg-cyan-500/5 px-1.5 py-0.5 rounded border border-cyan-500/10">
              {toolName}
            </span>
          </div>
          
          <div className="flex items-center gap-2 text-white/20 ml-auto">
            <span className="text-[10px] uppercase tracking-wider font-medium hidden sm:block">Arguments</span>
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </div>
        </div>

        {expanded && (
          <div className="mt-2 ml-1">
            <div className="bg-black/40 rounded-lg border border-white/5 p-3 overflow-x-auto">
              <pre className="text-xs font-mono text-cyan-200/70">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ToolResultBlock({ event }: { event: AgentEvent }) {
  const [expanded, setExpanded] = useState(false);
  const toolName = event.metadata?.tool || "Tool";
  const content = event.content;
  
  // Try to parse JSON
  let jsonContent = null;
  try {
    jsonContent = JSON.parse(content);
  } catch (e) {}

  const isError = content.toLowerCase().includes("error");
  const summary = content.length > 150 ? content.slice(0, 150) + "..." : content;

  return (
    <div className="flex gap-4 pl-4 group relative">
      {/* Thread Line */}
      <div className="absolute left-[2.4rem] top-[-2rem] bottom-0 w-px bg-white/5 group-last:hidden" />

      <div className={`mt-1 h-6 w-6 rounded-md flex items-center justify-center flex-shrink-0 border z-10 ${
        isError ? 'bg-red-500/10 text-red-400 border-red-500/20' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
      }`}>
        {isError ? <AlertCircle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
      </div>

      <div className="flex-1 min-w-0 pb-4">
        <div className={`rounded-lg border overflow-hidden ${
          isError ? 'border-red-500/10 bg-red-900/5' : 'border-emerald-500/10 bg-emerald-900/5'
        }`}>
          <div 
            onClick={() => setExpanded(!expanded)}
            className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-white/5 transition-colors"
          >
            <div className="flex items-center gap-2 overflow-hidden">
              <span className={`text-xs font-bold uppercase tracking-wider ${isError ? 'text-red-400' : 'text-emerald-400'}`}>
                Result
              </span>
              {!expanded && (
                <span className="text-xs text-neutral-500 truncate font-mono ml-2">
                  {jsonContent ? "{ JSON Data }" : summary.replace(/\n/g, ' ')}
                </span>
              )}
            </div>
            <div className="text-white/20 flex-shrink-0">
              {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            </div>
          </div>

          {expanded && (
            <div className="border-t border-white/5 bg-black/20 p-3 overflow-x-auto">
              <pre className={`text-xs font-mono whitespace-pre-wrap ${isError ? 'text-red-200/80' : 'text-emerald-200/80'}`}>
                {jsonContent ? JSON.stringify(jsonContent, null, 2) : content}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DecisionBlock({ event }: { event: AgentEvent }) {
  const content = event.content;
  // Extract action if possible (e.g. "BUY", "SELL", "HOLD")
  const actionMatch = content.match(/(BUY|SELL|HOLD|LIQUIDATE)/i);
  const action = actionMatch ? actionMatch[0].toUpperCase() : "CONCLUSION";
  
  let colorClass = "border-neutral-500/50 bg-neutral-500/10 text-neutral-200";
  if (action.includes("BUY")) colorClass = "border-green-500/50 bg-green-500/10 text-green-200";
  if (action.includes("SELL") || action.includes("LIQUIDATE")) colorClass = "border-red-500/50 bg-red-500/10 text-red-200";
  if (action.includes("HOLD")) colorClass = "border-blue-500/50 bg-blue-500/10 text-blue-200";

  return (
    <div className="flex gap-4 group my-4">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-amber-500 text-black shadow-[0_0_20px_-5px_rgba(245,158,11,0.5)]">
        <Flag className="h-4 w-4 fill-current" />
      </div>
      <div className="flex-1 space-y-2 min-w-0">
        <HeaderRow source="Manager" timestamp={event.timestamp} color="text-amber-400" suffix="Strategic Decision" />
        
        <div className={`rounded-xl border-l-4 p-5 shadow-lg ${colorClass} bg-opacity-10 border-y border-r border-white/10`}>
          <div className="flex items-center gap-3 mb-3">
            <span className="text-xs font-bold uppercase tracking-widest opacity-70">Action Required</span>
            <span className="text-sm font-black px-2 py-0.5 rounded bg-white/10 border border-white/10">{action}</span>
          </div>
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}

function ErrorBlock({ event }: { event: AgentEvent }) {
  return (
    <div className="flex gap-4 group">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-red-500 text-white">
        <AlertCircle className="h-4 w-4" />
      </div>
      <div className="flex-1">
        <div className="bg-red-950/30 border border-red-500/30 rounded-xl p-4 text-red-200 text-sm">
          <h4 className="font-bold mb-1 flex items-center gap-2">
            <Activity className="h-4 w-4" /> System Error
          </h4>
          <p>{event.content}</p>
        </div>
      </div>
    </div>
  );
}

function InfoMessage({ event }: { event: AgentEvent }) {
  const content = event.content;
  
  // Highlight specific headers with a nice font
  const highlightHeaders = (text: string) => {
    const parts = text.split(/(Manager Started:|Consulting Quant:)/g);
    return parts.map((part, i) => {
      if (part === "Manager Started:" || part === "Consulting Quant:") {
        return (
          <span key={i} className="font-serif font-bold text-indigo-300 mr-1 text-base">
            {part}
          </span>
        );
      }
      return part;
    });
  };

  return (
    <div className="flex gap-4 group my-2">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
        <Bot className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-2 min-w-0">
        <HeaderRow source="Manager" timestamp={event.timestamp} color="text-indigo-400" />
        
        <div className="bg-indigo-950/20 border border-indigo-500/20 rounded-lg p-4 relative overflow-hidden">
          <div className="absolute top-0 left-0 w-1 h-full bg-indigo-500/30" />
          <p className="text-sm leading-relaxed text-indigo-100/90">
            {highlightHeaders(content)}
          </p>
        </div>
      </div>
    </div>
  );
}

function MemoryCloud({ event }: { event: AgentEvent }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const storageKey = `memory-expanded-${event.timestamp || event.content.slice(0, 20)}`;

  useEffect(() => {
    const saved = localStorage.getItem(storageKey);
    if (saved !== null) {
      setIsExpanded(saved === 'true');
    }
  }, [storageKey]);

  const toggleExpanded = () => {
    const newState = !isExpanded;
    setIsExpanded(newState);
    localStorage.setItem(storageKey, String(newState));
  };

  let memoryData: Record<string, any> = {};
  try {
    memoryData = JSON.parse(event.content);
  } catch (e) {
    return null;
  }

  const humanizeKey = (key: string) => {
    return key.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, l => l.toUpperCase());
  };

  return (
    <div className="flex gap-4 group my-6">
      <div className="mt-1 h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-amber-500/10 text-amber-400 border border-amber-500/20 shadow-[0_0_15px_-3px_rgba(245,158,11,0.15)]">
        <History className="h-4 w-4" />
      </div>
      <div className="flex-1 space-y-3 min-w-0">
        <HeaderRow source="System" timestamp={event.timestamp} color="text-amber-400" suffix="Cycle Memory" />
        
        <div 
          onClick={toggleExpanded}
          className={`
            relative cursor-pointer transition-all duration-500 ease-in-out
            bg-gradient-to-br from-amber-500/5 to-orange-500/5 
            border border-amber-500/20 rounded-[2rem] p-6
            hover:border-amber-500/40 hover:shadow-[0_0_30px_-10px_rgba(245,158,11,0.2)]
            ${isExpanded ? 'rounded-2xl' : 'max-w-md'}
            overflow-hidden break-words
          `}
        >
          {/* Cloud-like decorative elements */}
          <div className="absolute -top-2 -right-2 w-12 h-12 bg-amber-500/10 rounded-full blur-xl" />
          <div className="absolute -bottom-2 -left-2 w-16 h-16 bg-orange-500/10 rounded-full blur-xl" />

          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Cloud className="h-4 w-4 text-amber-400/70" />
              <span className="text-xs font-bold uppercase tracking-widest text-amber-200/70">Short Term Memory</span>
            </div>
            <div className="text-amber-400/40">
              {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </div>
          </div>

          {!isExpanded ? (
            <p className="text-sm text-amber-100/50 italic line-clamp-1">
              {memoryData.SHORT_TERM_SUMMARY || "Click to view cycle summary..."}
            </p>
          ) : (
            <div className="space-y-4 transition-all duration-300 ease-in-out opacity-100">
              {Object.entries(memoryData).map(([key, value]) => (
                <div key={key} className="space-y-1">
                  <h5 className="text-[10px] font-black uppercase tracking-tighter text-amber-500/60">
                    {humanizeKey(key)}
                  </h5>
                  <div className="text-sm text-amber-50/90 leading-relaxed">
                    {Array.isArray(value) ? (
                      <ul className="list-disc list-inside space-y-1">
                        {value.map((item, idx) => (
                          <li key={idx} className="pl-1">{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>{value}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SystemMessage({ event }: { event: AgentEvent }) {
  return (
    <div className="flex justify-center my-4">
      <div className="bg-white/5 border border-white/5 rounded-full px-4 py-1.5 flex items-center gap-2">
        <div className="w-1.5 h-1.5 rounded-full bg-neutral-500" />
        <span className="text-[10px] font-medium text-neutral-400 uppercase tracking-wider">
          {event.content}
        </span>
      </div>
    </div>
  );
}

// --- Shared Components ---

function HeaderRow({ 
  source, 
  timestamp, 
  color, 
  suffix, 
  onToggle, 
  isMinimized 
}: { 
  source: string, 
  timestamp?: string, 
  color: string, 
  suffix?: string,
  onToggle?: () => void,
  isMinimized?: boolean
}) {
  return (
    <div className="flex items-center justify-between group/header">
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider font-bold opacity-70">
        <span className={color}>{source}</span>
        {suffix && (
          <>
            <span className="text-neutral-600">/</span>
            <span className="text-neutral-400">{suffix}</span>
          </>
        )}
        {formatTime(timestamp) && (
          <>
            <span className="text-neutral-600">•</span>
            <span className="text-neutral-500">{formatTime(timestamp)}</span>
          </>
        )}
      </div>

      {onToggle && (
        <button 
          onClick={onToggle}
          className="flex items-center gap-1 px-2 py-0.5 rounded hover:bg-white/5 text-[10px] font-bold uppercase tracking-tighter text-neutral-500 hover:text-neutral-300 transition-all"
        >
          {isMinimized ? (
            <>
              <ChevronRight className="h-3 w-3" />
              <span>Expand</span>
            </>
          ) : (
            <>
              <ChevronDown className="h-3 w-3" />
              <span>Minimize</span>
            </>
          )}
        </button>
      )}
    </div>
  );
}