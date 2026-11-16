import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ExecutionTimeline } from './components/ExecutionTimeline';
import { HeaderBar } from './components/HeaderBar';
import { TaskLauncher } from './components/TaskLauncher';
import { ToolAuditPanel } from './components/ToolAuditPanel';
import { WorkspaceExplorer } from './components/WorkspaceExplorer';
import {
  createTask,
  createWorkspace,
  getHealth,
  getTask,
  listTasks,
  listWorkspaceFiles,
  listWorkspaces,
  readWorkspaceFile,
  subscribeToTaskStream,
} from './services/api';
import {
  HealthStatus,
  Task,
  TaskEvent,
  ToolCall,
  Workspace,
  WorkspaceFileContent,
  WorkspaceFileEntry,
} from './types/api';

export const App: React.FC = () => {
  const [health, setHealth] = useState<HealthStatus>({
    status: 'connecting',
    backend: 'connecting',
    backend_status: 'offline',
    agent_runtime_status: 'offline',
    llm_provider: 'gemini',
    llm_model: 'gemini-2.5-flash',
    llm_configured: false,
  });

  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
  const [files, setFiles] = useState<WorkspaceFileEntry[]>([]);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [selectedFileContent, setSelectedFileContent] = useState<WorkspaceFileContent | null>(
    null
  );

  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [liveEvents, setLiveEvents] = useState<TaskEvent[]>([]);
  const [liveToolCalls, setLiveToolCalls] = useState<ToolCall[]>([]);

  const [sseConnected, setSseConnected] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingFiles, setIsLoadingFiles] = useState(false);
  const [isLoadingFileContent, setIsLoadingFileContent] = useState(false);
  const [isSubmittingTask, setIsSubmittingTask] = useState(false);

  const selectedFilePathRef = useRef<string | null>(null);
  selectedFilePathRef.current = selectedFilePath;

  const refreshHealth = useCallback(async () => {
    const h = await getHealth();
    setHealth(h);
  }, []);

  const loadFileContent = useCallback(async (workspaceId: string, path: string) => {
    setSelectedFilePath(path);
    setIsLoadingFileContent(true);
    try {
      const content = await readWorkspaceFile(workspaceId, path);
      setSelectedFileContent(content);
    } catch {
      setSelectedFileContent(null);
    } finally {
      setIsLoadingFileContent(false);
    }
  }, []);

  const refreshFiles = useCallback(
    async (workspaceId: string) => {
      setIsLoadingFiles(true);
      try {
        const list = await listWorkspaceFiles(workspaceId);
        setFiles(list);
        const firstFile = list.find((f) => !f.is_dir);
        if (firstFile && !selectedFilePathRef.current) {
          loadFileContent(workspaceId, firstFile.path);
        } else if (
          selectedFilePathRef.current &&
          list.some((f) => f.path === selectedFilePathRef.current)
        ) {
          // Keep current file content fresh if agent modified it
          const content = await readWorkspaceFile(workspaceId, selectedFilePathRef.current);
          setSelectedFileContent(content);
        }
      } catch {
        // ignore when backend is still starting
      } finally {
        setIsLoadingFiles(false);
      }
    },
    [loadFileContent]
  );

  const refreshTasks = useCallback(async (workspaceId: string) => {
    try {
      const list = await listTasks(workspaceId);
      setTasks(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  const reconcileTaskDetail = useCallback(
    async (taskId: string, workspaceId?: string) => {
      try {
        const fullTask = await getTask(taskId);
        setSelectedTask(fullTask);
        if (fullTask.events && fullTask.events.length > 0) {
          setLiveEvents(fullTask.events);
        }
        if (fullTask.tool_calls) {
          setLiveToolCalls(fullTask.tool_calls);
        }
        setTasks((prev) => {
          const exists = prev.some((t) => t.id === fullTask.id);
          if (!exists) return [fullTask, ...prev];
          return prev.map((t) => (t.id === fullTask.id ? fullTask : t));
        });
        if (workspaceId) {
          refreshFiles(workspaceId);
        }
        return fullTask;
      } catch {
        return null;
      }
    },
    [refreshFiles]
  );

  // Initial load of health & workspaces
  const initializeDashboard = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await refreshHealth();
      const wsList = await listWorkspaces();
      setWorkspaces(wsList);
      if (wsList.length > 0) {
        setActiveWorkspace((prev) => prev || wsList[0]);
      }
    } catch {
      // Backend may still be booting
    } finally {
      setIsRefreshing(false);
    }
  }, [refreshHealth]);

  useEffect(() => {
    initializeDashboard();
    const interval = setInterval(() => {
      refreshHealth();
    }, 6000);
    return () => clearInterval(interval);
  }, [initializeDashboard, refreshHealth]);

  // When activeWorkspace changes, load its files and tasks
  useEffect(() => {
    if (!activeWorkspace) return;
    setSelectedFilePath(null);
    setSelectedFileContent(null);
    refreshFiles(activeWorkspace.id);
    refreshTasks(activeWorkspace.id).then((taskList) => {
      if (taskList.length > 0) {
        reconcileTaskDetail(taskList[0].id, activeWorkspace.id);
      } else {
        setSelectedTask(null);
        setLiveEvents([]);
        setLiveToolCalls([]);
      }
    });
  }, [activeWorkspace, refreshFiles, refreshTasks, reconcileTaskDetail]);

  // Subscribe to SSE stream & poll reconciliation when a task is selected
  useEffect(() => {
    if (!selectedTask?.id) {
      setSseConnected(false);
      return;
    }

    const taskId = selectedTask.id;
    const workspaceId = selectedTask.workspace_id || activeWorkspace?.id;

    const unsubscribe = subscribeToTaskStream(
      taskId,
      (evt) => {
        setLiveEvents((prev) => {
          const isDup = prev.some(
            (e) =>
              (e.id && evt.id && e.id === evt.id) ||
              (e.iteration === evt.iteration &&
                e.event_type === evt.event_type &&
                e.content === evt.content &&
                e.tool_name === evt.tool_name)
          );
          return isDup ? prev : [...prev, evt];
        });

        // Reconcile full state on tool result, final answer, or error
        const upperType = (evt.event_type || '').toUpperCase();
        if (
          upperType === 'TOOL_RESULT' ||
          upperType === 'FINAL_ANSWER' ||
          upperType === 'ERROR' ||
          upperType === 'STATUS'
        ) {
          reconcileTaskDetail(taskId, workspaceId);
        }
      },
      (connected) => setSseConnected(connected)
    );

    const pollInterval = setInterval(async () => {
      const updated = await reconcileTaskDetail(taskId, workspaceId);
      if (
        updated &&
        (updated.status.toUpperCase() === 'COMPLETED' ||
          updated.status.toUpperCase() === 'FAILED')
      ) {
        clearInterval(pollInterval);
      }
    }, 1800);

    return () => {
      unsubscribe();
      clearInterval(pollInterval);
      setSseConnected(false);
    };
  }, [selectedTask?.id, selectedTask?.workspace_id, activeWorkspace?.id, reconcileTaskDetail]);

  const handleCreateWorkspace = async (name: string, path?: string) => {
    const created = await createWorkspace(name, path);
    const updatedList = await listWorkspaces();
    setWorkspaces(updatedList);
    const found = updatedList.find((w) => w.id === created.id) || created;
    setActiveWorkspace(found);
  };

  const handleLaunchTask = async (prompt: string, maxIterations: number) => {
    if (!activeWorkspace) return;
    setIsSubmittingTask(true);
    try {
      const created = await createTask(activeWorkspace.id, prompt, maxIterations);
      setLiveEvents(created.events || []);
      setLiveToolCalls(created.tool_calls || []);
      setSelectedTask(created);
      await refreshTasks(activeWorkspace.id);
    } finally {
      setIsSubmittingTask(false);
    }
  };

  const handleSelectTask = async (task: Task) => {
    setSelectedTask(task);
    await reconcileTaskDetail(task.id, task.workspace_id);
  };

  const handleManualSync = async () => {
    setIsRefreshing(true);
    try {
      await refreshHealth();
      const wsList = await listWorkspaces();
      setWorkspaces(wsList);
      const currentWs = activeWorkspace || wsList[0] || null;
      if (currentWs) {
        if (!activeWorkspace) setActiveWorkspace(currentWs);
        await refreshFiles(currentWs.id);
        await refreshTasks(currentWs.id);
      }
      if (selectedTask) {
        await reconcileTaskDetail(selectedTask.id, selectedTask.workspace_id);
      }
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <HeaderBar
        health={health}
        sseConnected={sseConnected}
        isRefreshing={isRefreshing}
        onRefresh={handleManualSync}
      />

      <main className="flex-1 max-w-[1600px] w-full mx-auto p-4 grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* Left Column: Workspace Manager, File Explorer & Code Viewer (4 cols) */}
        <div className="lg:col-span-4 h-full">
          <WorkspaceExplorer
            workspaces={workspaces}
            activeWorkspace={activeWorkspace}
            files={files}
            selectedFilePath={selectedFilePath}
            selectedFileContent={selectedFileContent}
            isLoadingFiles={isLoadingFiles}
            isLoadingFileContent={isLoadingFileContent}
            onSelectWorkspace={(ws) => setActiveWorkspace(ws)}
            onCreateWorkspace={handleCreateWorkspace}
            onRefreshFiles={() => activeWorkspace && refreshFiles(activeWorkspace.id)}
            onSelectFile={(path) =>
              activeWorkspace && loadFileContent(activeWorkspace.id, path)
            }
          />
        </div>

        {/* Center Column: Agent Chat & Task Launcher + Live Execution Timeline (5 cols) */}
        <div className="lg:col-span-5 flex flex-col gap-4">
          <TaskLauncher
            activeWorkspace={activeWorkspace}
            tasks={tasks}
            selectedTaskId={selectedTask?.id || null}
            isSubmitting={isSubmittingTask}
            onLaunchTask={handleLaunchTask}
            onSelectTask={handleSelectTask}
          />

          <ExecutionTimeline
            task={selectedTask}
            events={liveEvents}
            onOpenFile={(path) =>
              activeWorkspace && loadFileContent(activeWorkspace.id, path)
            }
          />
        </div>

        {/* Right Column: Tool Activity & Security Audit Panel (3 cols) */}
        <div className="lg:col-span-3 h-full">
          <ToolAuditPanel toolCalls={liveToolCalls} events={liveEvents} />
        </div>
      </main>
    </div>
  );
};

export default App;
