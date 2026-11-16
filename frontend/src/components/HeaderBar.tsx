import React from 'react';
import {
  Activity,
  Cpu,
  RefreshCw,
  Server,
  ShieldCheck,
  Sparkles,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { HealthStatus } from '../types/api';

interface HeaderBarProps {
  health: HealthStatus;
  sseConnected: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
}

export const HeaderBar: React.FC<HeaderBarProps> = ({
  health,
  sseConnected,
  isRefreshing,
  onRefresh,
}) => {
  const backendOnline = health.backend_status === 'online';
  const runtimeOnline = health.agent_runtime_status === 'online';

  return (
    <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur-md sticky top-0 z-30">
      <div className="max-w-[1600px] mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        {/* Brand & Architecture Title */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-cyan-500 flex items-center justify-center shadow-lg shadow-indigo-500/20 border border-white/10">
            <Cpu className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold tracking-tight text-white">
                Polyglot AI Agent Platform
              </h1>
              <span className="text-[11px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                Developer Control Plane
              </span>
            </div>
            <p className="text-xs text-slate-400 flex items-center gap-2 mt-0.5">
              <span>React + TS</span>
              <span className="text-slate-600">•</span>
              <span>Go API &amp; Sandbox Gate (:8080)</span>
              <span className="text-slate-600">•</span>
              <span>Python gRPC Agent Runtime (:50051)</span>
            </p>
          </div>
        </div>

        {/* System Status Indicators */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Go Backend Status */}
          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${
              backendOnline
                ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300'
                : 'bg-rose-950/40 border-rose-500/30 text-rose-300'
            }`}
            title="Go Backend Control Plane & SQLite Store (:8080)"
          >
            <Server className="w-3.5 h-3.5" />
            <span>Go Backend:</span>
            <span className="font-semibold uppercase">
              {backendOnline ? 'Connected' : 'Offline'}
            </span>
            <span
              className={`w-2 h-2 rounded-full ${
                backendOnline ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'
              }`}
            />
          </div>

          {/* Python gRPC Runtime Status */}
          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${
              runtimeOnline
                ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300'
                : health.agent_runtime_status === 'degraded'
                ? 'bg-amber-950/40 border-amber-500/30 text-amber-300'
                : 'bg-rose-950/40 border-rose-500/30 text-rose-300'
            }`}
            title="Python AI Agent Runtime gRPC Service (:50051)"
          >
            <Activity className="w-3.5 h-3.5" />
            <span>Python Agent Runtime:</span>
            <span className="font-semibold uppercase">
              {runtimeOnline
                ? 'Healthy'
                : health.agent_runtime_status === 'degraded'
                ? 'Degraded'
                : 'Offline'}
            </span>
            <span
              className={`w-2 h-2 rounded-full ${
                runtimeOnline
                  ? 'bg-emerald-400 animate-pulse'
                  : health.agent_runtime_status === 'degraded'
                  ? 'bg-amber-400'
                  : 'bg-rose-400'
              }`}
            />
          </div>

          {/* Active LLM Provider / Model */}
          <div
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-indigo-500/30 bg-indigo-950/40 text-indigo-200 text-xs font-medium"
            title="Active LLM Provider & Model reported by Agent Runtime"
          >
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span className="text-slate-400">LLM:</span>
            <span className="font-mono font-semibold text-indigo-200">
              {health.llm_provider}/{health.llm_model}
            </span>
          </div>

          {/* Sandbox Security Badge */}
          <div
            className="hidden xl:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-cyan-500/30 bg-cyan-950/30 text-cyan-300 text-xs font-medium"
            title="Workspace Path Jail & Command Allowlist Active"
          >
            <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
            <span>Sandbox Enforced</span>
          </div>

          {/* SSE Live Stream Indicator */}
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium ${
              sseConnected
                ? 'border-purple-500/40 bg-purple-950/40 text-purple-200'
                : 'border-slate-800 bg-slate-900 text-slate-400'
            }`}
            title="Server-Sent Events (SSE) Live Stream Status"
          >
            {sseConnected ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-purple-400 animate-pulse" />
                <span>SSE Live</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-slate-500" />
                <span>SSE Idle</span>
              </>
            )}
          </div>

          {/* Refresh Button */}
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-200 text-xs font-medium transition cursor-pointer"
            title="Refresh system health, workspaces, files, and task status"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-indigo-400' : ''}`} />
            <span className="hidden sm:inline">Sync</span>
          </button>
        </div>
      </div>
    </header>
  );
};
