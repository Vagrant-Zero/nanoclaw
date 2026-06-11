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
export interface ErrorData {
  message: string
  task_id: string
}