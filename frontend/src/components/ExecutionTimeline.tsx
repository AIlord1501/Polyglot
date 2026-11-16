import React from 'react';
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  FileCode,
  Info,
  Loader2,
  Sparkles,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-react';
import { parseStructuredReport } from '../services/api';
import { StructuredCompletionReport, Task, TaskEvent } from '../types/api';
import { getStatusBadge } from './TaskLauncher';

interface ExecutionTimelineProps {
  task: Task | null;
  events: TaskEvent[];
  onOpenFile: (path: string) => void;
}

function tryFormatJson(raw?: string): string {
  if (!raw) return '';
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

export const ExecutionTimeline: React.FC<ExecutionTimelineProps> = ({
  task,
  events,
  onOpenFile,
}) => {
  if (!task) {
    return (
      <div className="bg-slate-900/70 border border-slate-800 rounded-xl p-8 text-center shadow-xl flex flex-col items-center justify-center min-h-[340px]">
        <Brain className="w-10 h-10 text-slate-700 mb-3" />
        <h3 className="text-sm font-semibold text-slate-300">No Active Task Selected</h3>
        <p className="text-xs text-slate-500 max-w-md mt-1">
          Launch a task or select one from the Workspace Task History to watch the live ReAct
          reasoning loop, tool executions, and structured completion report.
        </p>
      </div>
    );
  }

  // Extract structured report from task or from a FINAL_ANSWER event
  const finalEvent = [...events].reverse().find((e) => e.event_type === 'FINAL_ANSWER');
  const structuredReport: StructuredCompletionReport | null =
    task.structured_report ||
    parseStructuredReport(
      task.structured_report_json || finalEvent?.structured_report_json || finalEvent?.content
    );

  const isRunning =
    task.status.toUpperCase() === 'RUNNING' || task.status.toUpperCase() === 'PENDING';

  return (
    <div className="bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden shadow-xl flex flex-col">
      {/* Task Header Banner */}
      <div className="px-4 py-3 border-b border-slate-800 bg-slate-900/95 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold uppercase tracking-wider text-indigo-300">
              Live Execution Timeline
            </span>
            <span className="text-[11px] font-mono text-slate-500">#{task.id.slice(0, 8)}</span>
          </div>
          <p className="text-xs text-slate-200 font-medium mt-0.5 truncate" title={task.prompt}>
            {task.prompt}
          </p>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="text-right font-mono text-xs">
            <span className="text-slate-400">Iteration: </span>
            <span className="text-indigo-300 font-bold">
              {task.current_iteration ||
                events.reduce((max, e) => Math.max(max, e.iteration || 0), 0)}
            </span>
            <span className="text-slate-500"> / {task.max_iterations}</span>
          </div>
          {getStatusBadge(task.status)}
        </div>
      </div>

      {/* Chronological Event Stream */}
      <div className="p-4 space-y-3 max-h-[540px] overflow-y-auto">
        {events.length === 0 ? (
          <div className="py-10 text-center text-xs text-slate-400 flex flex-col items-center gap-2">
            {isRunning ? (
              <>
                <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
                <span>Waiting for first agent iteration step from gRPC stream...</span>
              </>
            ) : (
              <span>No timeline events recorded for this task.</span>
            )}
          </div>
        ) : (
          events.map((evt, idx) => {
            const type = (evt.event_type || 'STATUS').toUpperCase();

            if (type === 'THOUGHT') {
              return (
                <div
                  key={evt.id || idx}
                  className="p-3 rounded-lg bg-indigo-950/25 border border-indigo-500/30 space-y-1.5"
                >
                  <div className="flex items-center justify-between text-[11px]">
                    <div className="flex items-center gap-1.5 font-semibold text-indigo-300">
                      <Brain className="w-3.5 h-3.5 text-indigo-400" />
                      <span>THOUGHT</span>
                      {evt.iteration > 0 && (
                        <span className="px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-200 font-mono text-[10px]">
                          Iter #{evt.iteration}
                        </span>
                      )}
                    </div>
                    {evt.created_at && (
                      <span className="text-slate-500 font-mono text-[10px]">
                        {new Date(evt.created_at).toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap">
                    {evt.content}
                  </p>
                </div>
              );
            }

            if (type === 'TOOL_CALL') {
              return (
                <div
                  key={evt.id || idx}
                  className="p-3 rounded-lg bg-amber-950/20 border border-amber-500/30 space-y-2"
                >
                  <div className="flex items-center justify-between text-[11px]">
                    <div className="flex items-center gap-1.5 font-semibold text-amber-300">
                      <Wrench className="w-3.5 h-3.5 text-amber-400" />
                      <span>TOOL_CALL</span>
                      {evt.iteration > 0 && (
                        <span className="px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-200 font-mono text-[10px]">
                          Iter #{evt.iteration}
                        </span>
                      )}
                      {evt.tool_name && (
                        <span className="px-2 py-0.5 rounded bg-slate-900 text-amber-300 border border-amber-500/40 font-mono text-[11px]">
                          {evt.tool_name}
                        </span>
                      )}
                    </div>
                  </div>
                  {evt.tool_args_json ? (
                    <pre className="p-2 rounded bg-slate-950/90 border border-slate-800 text-[11px] font-mono text-amber-100 overflow-x-auto max-h-40">
                      {tryFormatJson(evt.tool_args_json)}
                    </pre>
                  ) : (
                    <p className="text-xs text-slate-200 font-mono whitespace-pre-wrap">
                      {evt.content}
                    </p>
                  )}
                </div>
              );
            }

            if (type === 'TOOL_RESULT') {
              const outputText = evt.tool_output || evt.content || '';
              const isDeniedOrError =
                outputText.toUpperCase().includes('DENIED') ||
                outputText.toUpperCase().includes('FORBIDDEN') ||
                outputText.toUpperCase().includes('OUTSIDE WORKSPACE');
              return (
                <div
                  key={evt.id || idx}
                  className={`p-3 rounded-lg border space-y-2 ${
                    isDeniedOrError
                      ? 'bg-rose-950/20 border-rose-500/30'
                      : 'bg-cyan-950/20 border-cyan-500/30'
                  }`}
                >
                  <div className="flex items-center justify-between text-[11px]">
                    <div
                      className={`flex items-center gap-1.5 font-semibold ${
                        isDeniedOrError ? 'text-rose-300' : 'text-cyan-300'
                      }`}
                    >
                      <Terminal className="w-3.5 h-3.5" />
                      <span>TOOL_RESULT</span>
                      {evt.iteration > 0 && (
                        <span className="px-1.5 py-0.2 rounded bg-slate-800 text-slate-200 font-mono text-[10px]">
                          Iter #{evt.iteration}
                        </span>
                      )}
                      {evt.tool_name && (
                        <span className="px-2 py-0.5 rounded bg-slate-900 text-cyan-200 border border-cyan-500/30 font-mono text-[11px]">
                          {evt.tool_name}
                        </span>
                      )}
                    </div>
                  </div>
                  <pre className="p-2.5 rounded bg-slate-950/90 border border-slate-800 text-[11px] font-mono text-slate-200 overflow-x-auto max-h-48 whitespace-pre-wrap">
                    {outputText || '(empty output)'}
                  </pre>
                </div>
              );
            }

            if (type === 'ERROR') {
              return (
                <div
                  key={evt.id || idx}
                  className="p-3 rounded-lg bg-rose-950/30 border border-rose-500/40 space-y-1.5"
                >
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-rose-300">
                    <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                    <span>ERROR</span>
                    {evt.iteration > 0 && (
                      <span className="px-1.5 py-0.2 rounded bg-rose-500/20 text-rose-200 font-mono text-[10px]">
                        Iter #{evt.iteration}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-rose-200 font-mono whitespace-pre-wrap">
                    {evt.content}
                  </p>
                </div>
              );
            }

            if (type === 'FINAL_ANSWER') {
              return (
                <div
                  key={evt.id || idx}
                  className="p-3.5 rounded-lg bg-emerald-950/25 border border-emerald-500/40 space-y-2"
                >
                  <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-300">
                    <Sparkles className="w-4 h-4 text-emerald-400" />
                    <span>FINAL_ANSWER</span>
                    {evt.iteration > 0 && (
                      <span className="px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-200 font-mono text-[10px]">
                        Iter #{evt.iteration}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-100 leading-relaxed whitespace-pre-wrap">
                    {evt.content}
                  </p>
                </div>
              );
            }

            return (
              <div
                key={evt.id || idx}
                className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 flex items-center gap-2 text-xs text-slate-300"
              >
                <Info className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                <span className="font-mono text-[10px] uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                  {type}
                </span>
                <span className="truncate">{evt.content}</span>
              </div>
            );
          })
        )}

        {/* Final Structured Completion Report Card */}
        {(structuredReport || task.final_answer) && (
          <div className="mt-4 p-4 rounded-xl bg-gradient-to-br from-emerald-950/40 via-slate-900 to-indigo-950/30 border border-emerald-500/40 shadow-lg space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-500/20 pb-2.5">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <h4 className="text-xs font-bold uppercase tracking-wider text-emerald-200">
                  Structured Completion Report
                </h4>
              </div>

              {structuredReport && (
                <span
                  className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase ${
                    structuredReport.passed !== false
                      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                      : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                  }`}
                >
                  {structuredReport.passed !== false ? (
                    <>
                      <CheckCircle2 className="w-3 h-3" />
                      TESTS PASSED
                    </>
                  ) : (
                    <>
                      <XCircle className="w-3 h-3" />
                      TESTS FAILED
                    </>
                  )}
                </span>
              )}
            </div>

            <div className="space-y-2">
              <p className="text-xs text-slate-100 leading-relaxed whitespace-pre-wrap">
                {structuredReport?.summary || task.final_answer}
              </p>

              {structuredReport?.files_created && structuredReport.files_created.length > 0 && (
                <div className="pt-1">
                  <div className="text-[11px] font-semibold text-slate-400 mb-1.5">
                    Files Created / Modified:
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {structuredReport.files_created.map((filePath) => (
                      <button
                        key={filePath}
                        type="button"
                        onClick={() => onOpenFile(filePath)}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono bg-slate-900 hover:bg-indigo-950/60 text-indigo-300 border border-indigo-500/30 transition cursor-pointer"
                        title="Click to inspect file in Workspace Code Viewer"
                      >
                        <FileCode className="w-3 h-3 text-indigo-400" />
                        <span>{filePath}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {(structuredReport?.test_results || structuredReport?.details) && (
                <div className="pt-1">
                  <div className="text-[11px] font-semibold text-slate-400 mb-1">
                    Verification &amp; Test Execution Output:
                  </div>
                  <pre className="p-2.5 rounded-lg bg-slate-950/90 border border-slate-800 text-[11px] font-mono text-emerald-200 overflow-x-auto whitespace-pre-wrap max-h-48">
                    {structuredReport.test_results || structuredReport.details}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
