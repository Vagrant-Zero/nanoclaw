import type { HealthResponse, ChatRequest, ChatResponse } from "./types.js"
import { SseParser } from "./sse-parser.js"

const BASE_URL = "http://127.0.0.1:8420"

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE_URL}/health`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function sendMessage(baseUrl: string, req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${baseUrl}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export interface StreamResult {
  text: string
  sessionId: string
}

/** Stream the response, emitting progress to stdout as SSE events arrive. */
export async function sendMessageStream(
  baseUrl: string,
  req: ChatRequest,
  signal?: AbortSignal,
): Promise<StreamResult> {
  const res = await fetch(`${baseUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  })

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  let result = ""
  let sessionId = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      // Flush any remaining buffered SSE data
      for (const evt of parser.flush()) {
        handleEvent(evt)
      }
      break
    }

    const text = decoder.decode(value, { stream: true })
    for (const evt of parser.feed(text)) {
      handleEvent(evt)
    }
  }

  process.stdout.write("\n")
  return { text: result, sessionId }

  function handleEvent(evt: { event: string; data: unknown }): void {
    if (!evt.data || typeof evt.data !== "object") return
    const d = evt.data as Record<string, unknown>

    switch (evt.event) {
      case "message_chunk":
        process.stdout.write(d.content as string)
        result += d.content as string
        break
      case "tool_call":
        process.stdout.write(`\n[use tool: ${d.name}]\n`)
        break
      case "done":
        sessionId = (d as { session_id: string }).session_id || ""
        break
    }
  }
}


export async function confirmMemory(baseUrl: string, entryId: string): Promise<void> {
  const res = await fetch(`${baseUrl}/memories/${entryId}/confirm`, { method: "POST" })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

export async function rejectMemory(baseUrl: string, entryId: string): Promise<void> {
  const res = await fetch(`${baseUrl}/memories/${entryId}/reject`, { method: "POST" })
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
}
export async function listSchedules(baseUrl: string): Promise<import("./types.js").ScheduleListResponse> {
  const res = await fetch(baseUrl + "/schedules")
  if (!res.ok) throw new Error("HTTP " + res.status)
  return res.json()
}

export async function createSchedule(baseUrl: string, req: import("./types.js").CreateScheduleRequest): Promise<{status: string; task: import("./types.js").ScheduledTask}> {
  const res = await fetch(baseUrl + "/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error("HTTP " + res.status)
  return res.json()
}

export async function deleteSchedule(baseUrl: string, taskId: string): Promise<void> {
  const res = await fetch(baseUrl + "/schedules/" + taskId, { method: "DELETE" })
  if (!res.ok) throw new Error("HTTP " + res.status)
}

export async function toggleSchedule(baseUrl: string, taskId: string): Promise<{status: string}> {
  const res = await fetch(baseUrl + "/schedules/" + taskId + "/toggle", { method: "PATCH" })
  if (!res.ok) throw new Error("HTTP " + res.status)
  return res.json()
}
