import React, { useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  Play,
  Sliders,
  Sparkles,
  Zap,
} from 'lucide-react';
import { Task, Workspace } from '../types/api';

export const MVP_CALCULATOR_PROMPT =
  'Create a Python calculator project with addition, subtraction, multiplication, division and unit tests.';

interface TaskLauncherProps {
  activeWorkspace: Workspace | null;
  tasks: Task[];
  selectedTaskId: string | null;
  isSubmitting: boolean;
  onLaunchTask: (prompt: string, maxIterations: number) => Promise<void>;
  onSelectTask: (task: Task) => void;
}

export function getStatusBadge(status: string) {
  const upper = (status || 'PENDING').toUpperCase();
  if (upper === 'COMPLETED') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
        <CheckCircle2 className="w-3 h-3" />
        COMPLETED
      </span>
    );
  }
  if (upper === 'RUNNING' || upper === 'IN_PROGRESS') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">
        <Loader2 className="w-3 h-3 animate-spin" />
        RUNNING
      </span>
    );
  }
  if (upper === 'FAILED' || upper === 'ERROR') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-rose-500/15 text-rose-300 border border-rose-500/30">
        <AlertCircle className="w-3 h-3" />
        FAILED
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-amber-500/15 text-amber-300 border border-amber-500/30">
      <Clock className="w-3 h-3" />
      {upper}
    </span>
  );
}

export const TaskLauncher: React.FC<TaskLauncherProps> = ({
  activeWorkspace,
  tasks,
  selectedTaskId,
  isSubmitting,
  onLaunchTask,
  onSelectTask,
}) => {
  const [prompt, setPrompt] = useState(MVP_CALCULATOR_PROMPT);
  const [maxIterations, setMaxIterations] = useState(8);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || !activeWorkspace) return;
    setSubmitError(null);
    try {
      await onLaunchTask(prompt.trim(), maxIterations);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to start task');
    }
  };

  const handlePrefillMvp = () => {
    setPrompt(MVP_CALCULATOR_PROMPT);
    setMaxIterations(8);
    setSubmitError(null);
  };

  const handleOneClickRunMvp = async () => {
    setPrompt(MVP_CALCULATOR_PROMPT);
    setMaxIterations(8);
    if (!activeWorkspace) return;
    setSubmitError(null);
    try {
      await onLaunchTask(MVP_CALCULATOR_PROMPT, 8);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to start MVP demo task');
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Agent Prompt & Task Launcher Card */}
      <div className="bg-slate-900/70 border border-slate-800 rounded-xl p-4 shadow-xl space-y-3.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-indigo-400" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
              Agent Task Launcher
            </h2>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handlePrefillMvp}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-gradient-to-r from-indigo-500/20 to-purple-500/20 hover:from-indigo-500/30 hover:to-purple-500/30 text-indigo-200 border border-indigo-500/40 transition cursor-pointer"
              title="Pre-fill the canonical MVP Python Calculator prompt"
            >
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              <span>Run MVP Demo: Python Calculator</span>
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <textarea
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the software engineering task for the autonomous agent..."
              className="w-full bg-slate-950 border border-slate-700/90 rounded-lg p-3 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 leading-relaxed resize-y"
              required
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            {/* Max Iterations Selector */}
            <div className="flex items-center gap-2.5 bg-slate-950 px-3 py-1.5 rounded-lg border border-slate-800">
              <Sliders className="w-3.5 h-3.5 text-indigo-400" />
              <label htmlFor="max-iterations" className="text-xs text-slate-300 font-medium">
                Max Iterations:
              </label>
              <input
                id="max-iterations"
                type="range"
                min={1}
                max={12}
                value={maxIterations}
                onChange={(e) => setMaxIterations(Number(e.target.value))}
                className="w-24 accent-indigo-500 cursor-pointer"
              />
              <span className="font-mono text-xs font-bold text-indigo-300 w-5 text-center">
                {maxIterations}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={isSubmitting || !activeWorkspace}
                onClick={handleOneClickRunMvp}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-200 border border-emerald-500/40 transition cursor-pointer disabled:opacity-50"
                title="Immediately pre-fill and dispatch the MVP Python Calculator task"
              >
                <Zap className="w-3.5 h-3.5 text-emerald-400" />
                <span>1-Click Launch MVP</span>
              </button>

              <button
                type="submit"
                disabled={isSubmitting || !activeWorkspace || !prompt.trim()}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/25 transition cursor-pointer disabled:opacity-50"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>Dispatching...</span>
                  </>
                ) : (
                  <>
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Execute Task</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {submitError && (
            <div className="text-xs text-rose-300 bg-rose-950/50 border border-rose-500/30 rounded-lg px-3 py-2">
              {submitError}
            </div>
          )}
        </form>
      </div>

      {/* Task History List */}
      <div className="bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
        <div className="px-4 py-2.5 border-b border-slate-800 bg-slate-900/90 flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider text-slate-200">
            Workspace Task History ({tasks.length})
          </span>
          <span className="text-[11px] text-slate-400">
            Click a task to stream &amp; inspect
          </span>
        </div>

        <div className="max-h-48 overflow-y-auto divide-y divide-slate-800/70">
          {tasks.length === 0 ? (
            <div className="py-6 text-center px-4 text-xs text-slate-400">
              No tasks executed in this workspace yet. Click{' '}
              <span className="text-indigo-300 font-semibold">Run MVP Demo: Python Calculator</span>{' '}
              and <span className="text-indigo-300 font-semibold">Execute Task</span> above!
            </div>
          ) : (
            tasks.map((t) => {
              const isSelected = t.id === selectedTaskId;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onSelectTask(t)}
                  className={`w-full text-left px-4 py-2.5 flex items-center justify-between gap-3 transition cursor-pointer ${
                    isSelected
                      ? 'bg-indigo-600/15 border-l-2 border-l-indigo-400'
                      : 'hover:bg-slate-800/50'
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-slate-100 truncate">{t.prompt}</p>
                    <div className="flex items-center gap-3 mt-1 text-[11px] text-slate-400 font-mono">
                      <span>ID: {t.id.slice(0, 8)}</span>
                      <span>•</span>
                      <span>
                        Iter: {t.current_iteration || 0}/{t.max_iterations}
                      </span>
                    </div>
                  </div>
                  <div className="shrink-0">{getStatusBadge(t.status)}</div>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};
