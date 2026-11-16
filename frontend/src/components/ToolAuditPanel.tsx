import React, { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-react';
import { TaskEvent, ToolCall } from '../types/api';

interface ToolAuditPanelProps {
  toolCalls: ToolCall[];
  events: TaskEvent[];
}

function formatArgs(rawJson: string): string {
  if (!rawJson) return '{}';
  try {
    return JSON.stringify(JSON.parse(rawJson), null, 2);
  } catch {
    return rawJson;
  }
}

function getToolStatusBadge(status: string) {
  const upper = (status || 'COMPLETED').toUpperCase();
  if (upper === 'COMPLETED' || upper === 'AUTHORIZED') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
        <CheckCircle2 className="w-3 h-3" />
        {upper}
      </span>
    );
  }
  if (upper === 'DENIED') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-rose-500/20 text-rose-300 border border-rose-500/40">
        <ShieldAlert className="w-3 h-3" />
        DENIED
      </span>
    );
  }
  if (upper === 'FAILED') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-500/20 text-amber-300 border border-amber-500/40">
        <XCircle className="w-3 h-3" />
        FAILED
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
      {upper}
    </span>
  );
}

export const ToolAuditPanel: React.FC<ToolAuditPanelProps> = ({ toolCalls, events }) => {
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  // Merge persisted toolCalls with any live SSE TOOL_CALL / TOOL_RESULT events not yet reconciled
  const displayCalls: ToolCall[] = React.useMemo(() => {
    if (toolCalls && toolCalls.length > 0) {
      return toolCalls;
    }
    const synthesized: ToolCall[] = [];
    events.forEach((evt, idx) => {
      if (evt.event_type === 'TOOL_CALL') {
        synthesized.push({
          id: evt.id || `live-call-${idx}`,
          task_id: evt.task_id,
          iteration: evt.iteration || 1,
          tool_name: evt.tool_name || 'tool',
          arguments_json: evt.tool_args_json || evt.content || '{}',
          status: 'AUTHORIZED',
          output: '',
          error: '',
          created_at: evt.created_at || new Date().toISOString(),
        });
      } else if (evt.event_type === 'TOOL_RESULT' && synthesized.length > 0) {
        const last = synthesized[synthesized.length - 1];
        const out = evt.tool_output || evt.content || '';
        const isDenied = out.toUpperCase().includes('DENIED');
        last.status = isDenied ? 'DENIED' : 'COMPLETED';
        last.output = out;
      }
    });
    return synthesized;
  }, [toolCalls, events]);

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const completedCount = displayCalls.filter(
    (c) => c.status.toUpperCase() === 'COMPLETED' || c.status.toUpperCase() === 'AUTHORIZED'
  ).length;
  const deniedCount = displayCalls.filter((c) => c.status.toUpperCase() === 'DENIED').length;
  const failedCount = displayCalls.filter((c) => c.status.toUpperCase() === 'FAILED').length;

  return (
    <div className="bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden shadow-xl flex flex-col h-full">
      {/* Header */}
      <div className="p-3.5 border-b border-slate-800 bg-slate-900/90 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-cyan-400" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
              Tool Activity &amp; Security Audit
            </h2>
          </div>
          <span className="text-[11px] font-mono text-slate-400">
            {displayCalls.length} invocation{displayCalls.length === 1 ? '' : 's'}
          </span>
        </div>

        {/* Security Gate Summary Counters */}
        <div className="grid grid-cols-3 gap-2">
          <div className="px-2.5 py-1.5 rounded-lg bg-emerald-950/30 border border-emerald-500/25 flex items-center justify-between">
            <span className="text-[11px] text-emerald-300 font-medium">Authorized</span>
            <span className="text-xs font-mono font-bold text-emerald-200">{completedCount}</span>
          </div>
          <div className="px-2.5 py-1.5 rounded-lg bg-rose-950/30 border border-rose-500/25 flex items-center justify-between">
            <span className="text-[11px] text-rose-300 font-medium">Denied</span>
            <span className="text-xs font-mono font-bold text-rose-200">{deniedCount}</span>
          </div>
          <div className="px-2.5 py-1.5 rounded-lg bg-amber-950/30 border border-amber-500/25 flex items-center justify-between">
            <span className="text-[11px] text-amber-300 font-medium">Failed</span>
            <span className="text-xs font-mono font-bold text-amber-200">{failedCount}</span>
          </div>
        </div>
      </div>

      {/* Tool Call Audit List */}
      <div className="p-3 space-y-2.5 overflow-y-auto flex-1 max-h-[680px]">
        {displayCalls.length === 0 ? (
          <div className="py-12 text-center px-4">
            <Wrench className="w-8 h-8 text-slate-700 mx-auto mb-2" />
            <p className="text-xs text-slate-400 font-medium">No tool calls recorded yet</p>
            <p className="text-[11px] text-slate-500 mt-1">
              Every tool request (`list_files`, `read_file`, `write_file`, `search_files`,
              `run_command`) is validated by the Go Security Gate and audited here.
            </p>
          </div>
        ) : (
          displayCalls.map((call, idx) => {
            const callKey = call.id || `call-${idx}`;
            const isExpanded = expandedIds[callKey] ?? true;

            return (
              <div
                key={callKey}
                className="rounded-lg border border-slate-800 bg-slate-950/80 overflow-hidden"
              >
                {/* Tool Call Header Row */}
                <button
                  type="button"
                  onClick={() => toggleExpand(callKey)}
                  className="w-full px-3 py-2 flex items-center justify-between gap-2 bg-slate-900/70 hover:bg-slate-800/70 transition text-left cursor-pointer"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {isExpanded ? (
                      <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    )}
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px]">
                      Iter #{call.iteration || idx + 1}
                    </span>
                    <span className="font-mono text-xs font-semibold text-cyan-300 truncate flex items-center gap-1">
                      <Terminal className="w-3 h-3 text-cyan-400 shrink-0" />
                      {call.tool_name}
                    </span>
                  </div>
                  <div className="shrink-0">{getToolStatusBadge(call.status)}</div>
                </button>

                {/* Tool Call Details (Args + Output/Error) */}
                {isExpanded && (
                  <div className="p-3 space-y-2 border-t border-slate-800/80 text-xs">
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">
                        Arguments JSON
                      </div>
                      <pre className="p-2 rounded bg-slate-900 border border-slate-800/90 text-[11px] font-mono text-amber-200 overflow-x-auto max-h-32">
                        {formatArgs(call.arguments_json)}
                      </pre>
                    </div>

                    {call.error && (
                      <div>
                        <div className="text-[10px] font-semibold uppercase tracking-wider text-rose-400 mb-1">
                          Security / Execution Error
                        </div>
                        <pre className="p-2 rounded bg-rose-950/40 border border-rose-500/30 text-[11px] font-mono text-rose-200 overflow-x-auto whitespace-pre-wrap max-h-36">
                          {call.error}
                        </pre>
                      </div>
                    )}

                    {call.output && (
                      <div>
                        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">
                          Stdout / Stderr Output
                        </div>
                        <pre className="p-2 rounded bg-slate-900 border border-slate-800/90 text-[11px] font-mono text-slate-200 overflow-x-auto whitespace-pre-wrap max-h-44">
                          {call.output}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
