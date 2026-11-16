export interface AgentRuntimeHealth {
  status: string;
  connected: boolean;
  llm_provider: string;
  llm_model: string;
  llm_configured: boolean;
  version?: string;
}

export interface HealthStatus {
  status: string;
  backend: string;
  backend_status: 'online' | 'offline' | 'degraded';
  agent_runtime_status: 'online' | 'offline' | 'degraded';
  agent_runtime?: AgentRuntimeHealth;
  llm_provider: string;
  llm_model: string;
  llm_configured: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  root_path: string;
  created_at: string;
}

export interface WorkspaceFileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  updated_at?: string;
}

export interface WorkspaceFileContent {
  workspace_id: string;
  path: string;
  content: string;
  size?: number;
}

export type TaskStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | string;

export type EventType =
  | 'THOUGHT'
  | 'TOOL_CALL'
  | 'TOOL_RESULT'
  | 'FINAL_ANSWER'
  | 'ERROR'
  | 'STATUS'
  | string;

export interface TaskEvent {
  id?: string;
  task_id: string;
  iteration: number;
  event_type: EventType;
  content: string;
  tool_name?: string;
  tool_args_json?: string;
  tool_output?: string;
  structured_report_json?: string;
  timestamp?: string;
  created_at?: string;
}

export type ToolCallStatus =
  | 'PENDING'
  | 'AUTHORIZED'
  | 'RUNNING'
  | 'COMPLETED'
  | 'DENIED'
  | 'FAILED'
  | string;

export interface ToolCall {
  id: string;
  task_id: string;
  iteration: number;
  tool_name: string;
  arguments_json: string;
  status: ToolCallStatus;
  output: string;
  error: string;
  created_at: string;
  updated_at?: string;
}

export interface StructuredCompletionReport {
  summary: string;
  files_created?: string[];
  tests_executed?: string[] | string;
  tests_run?: string[] | string;
  passed?: boolean;
  test_results?: string;
  details?: string;
  status?: string;
}

export interface Task {
  id: string;
  workspace_id: string;
  prompt: string;
  status: TaskStatus;
  final_answer?: string;
  structured_report_json?: string;
  structured_report?: StructuredCompletionReport | null;
  max_iterations: number;
  current_iteration: number;
  error?: string;
  created_at: string;
  updated_at: string;
  events?: TaskEvent[];
  tool_calls?: ToolCall[];
}
