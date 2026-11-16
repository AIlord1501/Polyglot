import React, { useState } from 'react';
import {
  Check,
  Copy,
  FileCode,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Lock,
  Plus,
  RefreshCw,
  Terminal,
} from 'lucide-react';
import { Workspace, WorkspaceFileContent, WorkspaceFileEntry } from '../types/api';

interface WorkspaceExplorerProps {
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  files: WorkspaceFileEntry[];
  selectedFilePath: string | null;
  selectedFileContent: WorkspaceFileContent | null;
  isLoadingFiles: boolean;
  isLoadingFileContent: boolean;
  onSelectWorkspace: (ws: Workspace) => void;
  onCreateWorkspace: (name: string, path?: string) => Promise<void>;
  onRefreshFiles: () => void;
  onSelectFile: (path: string) => void;
}

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const WorkspaceExplorer: React.FC<WorkspaceExplorerProps> = ({
  workspaces,
  activeWorkspace,
  files,
  selectedFilePath,
  selectedFileContent,
  isLoadingFiles,
  isLoadingFileContent,
  onSelectWorkspace,
  onCreateWorkspace,
  onRefreshFiles,
  onSelectFile,
}) => {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newWsName, setNewWsName] = useState('');
  const [newWsPath, setNewWsPath] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWsName.trim()) return;
    setIsCreating(true);
    setCreateError(null);
    try {
      await onCreateWorkspace(newWsName.trim(), newWsPath.trim() || undefined);
      setNewWsName('');
      setNewWsPath('');
      setShowCreateForm(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create workspace');
    } finally {
      setIsCreating(false);
    }
  };

  const handleQuickCreateDemo = async () => {
    setIsCreating(true);
    setCreateError(null);
    try {
      const suffix = workspaces.length > 0 ? `-${workspaces.length + 1}` : '';
      await onCreateWorkspace(`calculator-demo${suffix}`);
      setShowCreateForm(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create demo workspace');
    } finally {
      setIsCreating(false);
    }
  };

  const handleCopyCode = () => {
    if (!selectedFileContent?.content) return;
    navigator.clipboard.writeText(selectedFileContent.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const codeLines = selectedFileContent?.content
    ? selectedFileContent.content.split(/\r?\n/)
    : [];

  return (
    <div className="flex flex-col h-full bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      {/* Workspace Selector Header */}
      <div className="p-3.5 border-b border-slate-800 bg-slate-900/90 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-indigo-400" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
              Workspace Sandbox
            </h2>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleQuickCreateDemo}
              disabled={isCreating}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-300 border border-indigo-500/30 transition cursor-pointer disabled:opacity-50"
              title="Quick-create a clean sandboxed workspace for the MVP Calculator demo"
            >
              <FolderPlus className="w-3 h-3" />
              <span>Demo Preset</span>
            </button>
            <button
              type="button"
              onClick={() => setShowCreateForm((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition cursor-pointer"
            >
              <Plus className="w-3 h-3" />
              <span>New</span>
            </button>
          </div>
        </div>

        {/* Active Workspace Selector Dropdown */}
        <div className="flex items-center gap-2">
          <select
            aria-label="Select Active Workspace"
            value={activeWorkspace?.id || ''}
            onChange={(e) => {
              const found = workspaces.find((w) => w.id === e.target.value);
              if (found) onSelectWorkspace(found);
            }}
            className="w-full bg-slate-950 border border-slate-700/80 rounded-lg px-2.5 py-1.5 text-xs text-slate-100 font-medium focus:outline-none focus:border-indigo-500"
          >
            {workspaces.length === 0 ? (
              <option value="">No workspaces yet — create one</option>
            ) : (
              workspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name} ({ws.id.slice(0, 8)})
                </option>
              ))
            )}
          </select>
        </div>

        {/* Sandboxed Root Path Indicator */}
        {activeWorkspace && (
          <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-slate-950/80 border border-slate-800/90 text-[11px]">
            <div className="flex items-center gap-1.5 text-slate-400 truncate" title={activeWorkspace.root_path}>
              <Lock className="w-3 h-3 text-emerald-400 shrink-0" />
              <span className="font-mono truncate text-slate-300">{activeWorkspace.root_path}</span>
            </div>
            <span className="shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
              Path Jailed
            </span>
          </div>
        )}

        {/* Inline New Workspace Form */}
        {showCreateForm && (
          <form
            onSubmit={handleCreateSubmit}
            className="p-2.5 rounded-lg bg-slate-950 border border-indigo-500/30 space-y-2"
          >
            <div className="text-[11px] font-semibold text-indigo-300">
              Create Isolated Workspace
            </div>
            <input
              type="text"
              placeholder="Workspace name (e.g. python-calculator)"
              value={newWsName}
              onChange={(e) => setNewWsName(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              required
            />
            <input
              type="text"
              placeholder="Optional relative path (defaults to ./workspaces/<slug>)"
              value={newWsPath}
              onChange={(e) => setNewWsPath(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
            {createError && (
              <div className="text-[11px] text-rose-400 bg-rose-950/40 border border-rose-500/30 rounded px-2 py-1">
                {createError}
              </div>
            )}
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="px-2.5 py-1 rounded text-xs text-slate-400 hover:text-slate-200 cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isCreating}
                className="px-3 py-1 rounded text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition cursor-pointer disabled:opacity-50"
              >
                {isCreating ? 'Creating...' : 'Create Workspace'}
              </button>
            </div>
          </form>
        )}
      </div>

      {/* File Tree List */}
      <div className="border-b border-slate-800 flex flex-col max-h-56 min-h-[150px]">
        <div className="px-3.5 py-2 bg-slate-900/50 border-b border-slate-800/70 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Workspace Files ({files.filter((f) => !f.is_dir).length})
          </span>
          <button
            type="button"
            onClick={onRefreshFiles}
            className="text-slate-400 hover:text-slate-200 p-1 rounded hover:bg-slate-800 transition cursor-pointer"
            title="Refresh workspace file tree"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoadingFiles ? 'animate-spin text-indigo-400' : ''}`} />
          </button>
        </div>

        <div className="overflow-y-auto p-2 space-y-0.5 flex-1">
          {files.length === 0 ? (
            <div className="py-6 text-center px-3">
              <FileCode className="w-6 h-6 text-slate-600 mx-auto mb-1.5" />
              <p className="text-xs text-slate-400 font-medium">Workspace is currently empty</p>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Launch an agent task to generate code &amp; unit tests here.
              </p>
            </div>
          ) : (
            files.map((entry) => {
              const isSelected = selectedFilePath === entry.path;
              const isTestFile =
                entry.name.startsWith('test_') ||
                entry.name.endsWith('_test.py') ||
                entry.name.includes('.test.');
              return (
                <button
                  key={entry.path}
                  type="button"
                  disabled={entry.is_dir}
                  onClick={() => !entry.is_dir && onSelectFile(entry.path)}
                  className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs transition ${
                    entry.is_dir
                      ? 'text-slate-400 cursor-default'
                      : isSelected
                      ? 'bg-indigo-600/20 text-indigo-200 border border-indigo-500/40 font-medium cursor-pointer'
                      : 'text-slate-300 hover:bg-slate-800/70 cursor-pointer'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {entry.is_dir ? (
                      <Folder className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                    ) : isTestFile ? (
                      <Terminal className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                    ) : entry.name.endsWith('.py') ? (
                      <FileCode className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                    ) : (
                      <FileText className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    )}
                    <span className="font-mono truncate">{entry.path}</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {isTestFile && !entry.is_dir && (
                      <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        test
                      </span>
                    )}
                    {!entry.is_dir && entry.size !== undefined && (
                      <span className="text-[10px] text-slate-500 font-mono">
                        {formatBytes(entry.size)}
                      </span>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Code Viewer Pane */}
      <div className="flex-1 flex flex-col min-h-[260px] bg-slate-950/90">
        <div className="px-3.5 py-2 border-b border-slate-800 flex items-center justify-between gap-2 bg-slate-900/60">
          <div className="flex items-center gap-2 min-w-0">
            <FileCode className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <span className="text-xs font-mono font-medium text-slate-200 truncate">
              {selectedFilePath || 'Select a file to inspect source code'}
            </span>
          </div>
          {selectedFileContent && (
            <button
              type="button"
              onClick={handleCopyCode}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition cursor-pointer shrink-0"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3 text-emerald-400" />
                  <span className="text-emerald-300">Copied</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  <span>Copy</span>
                </>
              )}
            </button>
          )}
        </div>

        <div className="flex-1 overflow-auto p-3 font-mono text-xs">
          {isLoadingFileContent ? (
            <div className="h-full flex items-center justify-center text-slate-400 text-xs gap-2">
              <RefreshCw className="w-4 h-4 animate-spin text-indigo-400" />
              <span>Loading file content...</span>
            </div>
          ) : selectedFileContent ? (
            <table className="w-full border-collapse">
              <tbody>
                {codeLines.map((line, idx) => (
                  <tr key={idx} className="hover:bg-slate-900/80">
                    <td className="select-none pr-3 text-right text-slate-600 w-8 align-top">
                      {idx + 1}
                    </td>
                    <td className="text-slate-200 whitespace-pre break-normal">{line || ' '}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center p-4 text-slate-500">
              <FileCode className="w-8 h-8 text-slate-700 mb-2" />
              <p className="text-xs text-slate-400">No file selected</p>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Click any file in the workspace explorer above to view its contents.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
