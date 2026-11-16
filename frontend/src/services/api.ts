import {
  HealthStatus,
  StructuredCompletionReport,
  Task,
  TaskEvent,
  ToolCall,
  Workspace,
  WorkspaceFileContent,
  WorkspaceFileEntry,
} from '../types/api';

const rawBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080/api/v1';
export const API_BASE_URL = rawBaseUrl.replace(/\/+$/, '');

export function parseStructuredReport(
  rawJson?: string | null,
  existingObj?: StructuredCompletionReport | null
): StructuredCompletionReport | null {
  if (existingObj && typeof existingObj === 'object' && existingObj.summary) {
    return existingObj;
  }
  if (!rawJson) return null;
  try {
    const parsed = JSON.parse(rawJson);
    if (parsed && typeof parsed === 'object') {
      return {
        summary: parsed.summary || parsed.final_answer || '',
        files_created: Array.isArray(parsed.files_created) ? parsed.files_created : [],
        tests_executed: parsed.tests_executed ?? parsed.tests_run ?? [],
        passed:
          typeof parsed.passed === 'boolean'
            ? parsed.passed
            : parsed.status === 'PASS' ||
              parsed.status === 'PASSED' ||
              parsed.status === 'COMPLETED' ||
              parsed.status === 'success',
        test_results: parsed.test_results || parsed.details || '',
        details: parsed.details || parsed.test_results || '',
        status: parsed.status,
      };
    }
  } catch {
    // ignore invalid json
  }
  return null;
}

function normalizeTask(raw: Record<string, unknown>): Task {
  const taskObj = (raw.task && typeof raw.task === 'object' ? raw.task : raw) as Record<string, unknown>;
  const eventsRaw = (taskObj.events ?? raw.events) as TaskEvent[] | undefined;
  const toolCallsRaw = (taskObj.tool_calls ?? raw.tool_calls) as ToolCall[] | undefined;

  const structuredReportJson =
    typeof taskObj.structured_report_json === 'string' ? taskObj.structured_report_json : undefined;
  const structuredReportObj = taskObj.structured_report as StructuredCompletionReport | null | undefined;

  return {
    id: String(taskObj.id || ''),
    workspace_id: String(taskObj.workspace_id || ''),
    prompt: String(taskObj.prompt || ''),
    status: String(taskObj.status || 'PENDING'),
    final_answer: taskObj.final_answer ? String(taskObj.final_answer) : undefined,
    structured_report_json: structuredReportJson,
    structured_report: parseStructuredReport(structuredReportJson, structuredReportObj),
    max_iterations: Number(taskObj.max_iterations || 8),
    current_iteration: Number(taskObj.current_iteration || 0),
    error: taskObj.error ? String(taskObj.error) : undefined,
    created_at: String(taskObj.created_at || new Date().toISOString()),
    updated_at: String(taskObj.updated_at || new Date().toISOString()),
    events: Array.isArray(eventsRaw) ? eventsRaw : [],
    tool_calls: Array.isArray(toolCallsRaw) ? toolCallsRaw : [],
  };
}

export async function getHealth(): Promise<HealthStatus> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const runtime = data.agent_runtime || {};
    const runtimeHealthy =
      runtime.status === 'healthy' ||
      runtime.status === 'ok' ||
      runtime.connected === true ||
      data.agent_runtime_status === 'online';

    return {
      status: data.status || 'ok',
      backend: data.backend || 'healthy',
      backend_status: 'online',
      agent_runtime_status: runtimeHealthy ? 'online' : 'degraded',
      agent_runtime: runtime,
      llm_provider: data.llm_provider || runtime.llm_provider || 'gemini',
      llm_model: data.llm_model || runtime.llm_model || 'gemini-2.5-flash',
      llm_configured: Boolean(data.llm_configured ?? runtime.llm_configured ?? true),
    };
  } catch {
    return {
      status: 'offline',
      backend: 'unreachable',
      backend_status: 'offline',
      agent_runtime_status: 'offline',
      llm_provider: 'disconnected',
      llm_model: 'unavailable',
      llm_configured: false,
    };
  }
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const res = await fetch(`${API_BASE_URL}/workspaces`);
  if (!res.ok) {
    throw new Error(`Failed to list workspaces (HTTP ${res.status})`);
  }
  const data = await res.json();
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.workspaces)) return data.workspaces;
  return [];
}

export async function createWorkspace(name: string, path?: string): Promise<Workspace> {
  const body: Record<string, string> = { name };
  if (path && path.trim()) {
    body.path = path.trim();
  }
  const res = await fetch(`${API_BASE_URL}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `Failed to create workspace (HTTP ${res.status})`);
  }
  const data = await res.json();
  return data.workspace ? data.workspace : data;
}

export async function listWorkspaceFiles(
  workspaceId: string,
  path = '.'
): Promise<WorkspaceFileEntry[]> {
  const url = `${API_BASE_URL}/workspaces/${encodeURIComponent(workspaceId)}/files?path=${encodeURIComponent(path)}`;
  const res = await fetch(url);
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `Failed to list files (HTTP ${res.status})`);
  }
  const data = await res.json();
  const rawList = Array.isArray(data) ? data : Array.isArray(data?.files) ? data.files : [];
  return rawList.map((item: unknown) => {
    if (typeof item === 'string') {
      const parts = item.replace(/\\/g, '/').split('/');
      return {
        name: parts[parts.length - 1] || item,
        path: item,
        is_dir: false,
      };
    }
    const obj = item as Record<string, unknown>;
    const normalizedPath = String(obj.path || obj.name || '').replace(/\\/g, '/');
    const parts = normalizedPath.split('/');
    return {
      name: String(obj.name || parts[parts.length - 1] || normalizedPath),
      path: normalizedPath,
      is_dir: Boolean(obj.is_dir ?? obj.type === 'dir' ?? obj.type === 'directory'),
      size: typeof obj.size === 'number' ? obj.size : undefined,
      updated_at: typeof obj.updated_at === 'string' ? obj.updated_at : undefined,
    };
  });
}

export async function readWorkspaceFile(
  workspaceId: string,
  path: string
): Promise<WorkspaceFileContent> {
  const url = `${API_BASE_URL}/workspaces/${encodeURIComponent(workspaceId)}/file?path=${encodeURIComponent(path)}`;
  const res = await fetch(url);
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `Failed to read file (HTTP ${res.status})`);
  }
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const data = await res.json();
    return {
      workspace_id: data.workspace_id || workspaceId,
      path: data.path || path,
      content: typeof data.content === 'string' ? data.content : JSON.stringify(data, null, 2),
      size: typeof data.size === 'number' ? data.size : undefined,
    };
  }
  const text = await res.text();
  return {
    workspace_id: workspaceId,
    path,
    content: text,
    size: text.length,
  };
}

export async function listTasks(workspaceId?: string): Promise<Task[]> {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
  const res = await fetch(`${API_BASE_URL}/tasks${query}`);
  if (!res.ok) {
    throw new Error(`Failed to list tasks (HTTP ${res.status})`);
  }
  const data = await res.json();
  const rawTasks = Array.isArray(data) ? data : Array.isArray(data?.tasks) ? data.tasks : [];
  return rawTasks.map((t: Record<string, unknown>) => normalizeTask(t));
}

export async function createTask(
  workspaceId: string,
  prompt: string,
  maxIterations: number
): Promise<Task> {
  const res = await fetch(`${API_BASE_URL}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workspace_id: workspaceId,
      prompt,
      max_iterations: maxIterations,
    }),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `Failed to create task (HTTP ${res.status})`);
  }
  const data = await res.json();
  return normalizeTask(data);
}

export async function getTask(taskId: string): Promise<Task> {
  const res = await fetch(`${API_BASE_URL}/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch task ${taskId} (HTTP ${res.status})`);
  }
  const data = await res.json();
  return normalizeTask(data);
}

export function subscribeToTaskStream(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onStatusChange?: (connected: boolean) => void
): () => void {
  const url = `${API_BASE_URL}/tasks/${encodeURIComponent(taskId)}/stream`;
  const es = new EventSource(url);
  const seenKeys = new Set<string>();

  const handleRawData = (rawData: string, fallbackType?: string) => {
    if (!rawData) return;
    try {
      const parsed = JSON.parse(rawData);
      const evt: TaskEvent = {
        id: parsed.id ? String(parsed.id) : undefined,
        task_id: String(parsed.task_id || taskId),
        iteration: Number(parsed.iteration || 0),
        event_type: String(parsed.event_type || fallbackType || 'STATUS'),
        content: String(parsed.content || ''),
        tool_name: parsed.tool_name ? String(parsed.tool_name) : undefined,
        tool_args_json: parsed.tool_args_json ? String(parsed.tool_args_json) : undefined,
        tool_output: parsed.tool_output ? String(parsed.tool_output) : undefined,
        structured_report_json: parsed.structured_report_json
          ? String(parsed.structured_report_json)
          : undefined,
        timestamp: parsed.timestamp || parsed.created_at || new Date().toISOString(),
        created_at: parsed.created_at || parsed.timestamp || new Date().toISOString(),
      };
      const dedupKey = evt.id
        ? `id:${evt.id}`
        : `${evt.iteration}:${evt.event_type}:${evt.tool_name || ''}:${evt.content}`;
      if (seenKeys.has(dedupKey)) return;
      seenKeys.add(dedupKey);
      onEvent(evt);
    } catch {
      // Ignore heartbeats or non-JSON comment lines
    }
  };

  es.onopen = () => {
    onStatusChange?.(true);
  };

  es.onmessage = (e: MessageEvent) => {
    handleRawData(e.data);
  };

  const namedEvents = [
    'THOUGHT',
    'TOOL_CALL',
    'TOOL_RESULT',
    'FINAL_ANSWER',
    'ERROR',
    'STATUS',
    'event',
    'agent_event',
  ];
  for (const eventName of namedEvents) {
    es.addEventListener(eventName, (e: Event) => {
      const msg = e as MessageEvent;
      handleRawData(msg.data, eventName);
    });
  }

  es.onerror = () => {
    onStatusChange?.(false);
  };

  return () => {
    es.close();
  };
}
