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