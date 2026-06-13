export interface HealthResponse {
  status: string
  version: string
}

export interface ChatRequest {
  message: string
  thread_id?: string | null
}

export interface ToolCallInfo {
  name: string
  args: Record<string, unknown>
  result: string | null
}

export interface ChatResponse {
  response: string
  thread_id: string | null
  tool_calls: ToolCallInfo[]
}

export interface Config {
  baseUrl: string
  default_model: string
}

export interface ExperienceEntry {
  entry_id: string
  summary: string
  type: "user_profile" | "skill" | "semantic" | "reflection"
}

export interface ChatMessage {
  content: string
  role: "user" | "assistant"
}

// ── SSE event types ──────────────────────────────────────

/**
 * Events the backend emits over the SSE stream.
 */
export type SSEEventName =
  | "task_status"
  | "agent_think"
  | "agent_action"
  | "agent_observation"
  | "agent_plan"
  | "check_result"
  | "iteration_exhausted"
  | "message_chunk"
  | "done"
  | "error"

/** agent_think  — LLM reasoning text */
export interface AgentThinkData {
  content: string
  task_id: string
}

/** agent_action — tool invocation requested */
export interface AgentActionData {
  tool: string
  args: Record<string, unknown>
  task_id: string
}

/** agent_observation — tool result returned */
export interface AgentObservationData {
  tool: string
  result: string
  task_id: string
}

/** message_chunk — streaming answer text */
export interface MessageChunkData {
  content: string
  task_id: string
}

/** task_status — subtask status update */
export interface TaskStatusData {
  task_id: string
  status: string
}

/** done — stream complete */
export interface DoneData {
  session_id: string
}

/** error — runtime error during agent execution */
// ── Phase 2 event types ─────────────────────────────────

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "RETRYING"
  | "CANCELLED"
  | "COMPENSATING"
  | "COMPENSATED"
  | "COMPENSATION_FAILED"

export interface SubtaskInfo {
  id: string
  description: string
  status: TaskStatus
  depends_on: string[]
}

/** agent_plan — Planner subtask DAG */
export interface AgentPlanData {
  tasks: SubtaskInfo[]
  session_id: string
}

/** check_result — Checker evaluation outcome */
export interface CheckResultData {
  task_id: string
  passed: boolean
  feedback: string
  failure_category?: "execution" | "planning"
}

/** check_result — per-criterion detail */
export interface CheckCriterionResult {
  text: string
  passed: boolean
  reason: string
}

/** iteration_exhausted — retry budget depleted */
export interface IterationExhaustedData {
  session_id: string
  failed_subtask_ids: string[]
  trajectory_paths: string[]
  budget: {
    global_count: number
    global_max: number
    per_subtask: Record<string, number>
  }
}


export interface ErrorData {
  message: string
  task_id: string
}

// ── Phase 4: Scheduled task types ──────────────────────────

export interface ScheduledTask {
  id: string
  description: string
  prompt: string
  schedule: string
  enabled: boolean
  last_run: string | null
  created_at: number
}

export interface CreateScheduleRequest {
  description: string
  prompt: string
  schedule: string
  enabled?: boolean
}

export interface ScheduleListResponse {
  tasks: ScheduledTask[]
}
