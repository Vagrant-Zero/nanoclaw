import type { HealthResponse, ChatRequest, ChatResponse } from "./types.js"

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

export async function sendMessageStream(baseUrl: string, req: ChatRequest): Promise<string> {
  const res = await fetch(`${baseUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let result = ""

  let currentEvent = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    const text = decoder.decode(value, { stream: true })
    const lines = text.split(/\r?\n/)

    for (const line of lines) {
      if (!line) continue
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7)
      } else if (line.startsWith("data: ")) {
        const data = JSON.parse(line.slice(6))
        if (currentEvent === "message_chunk") {
          process.stdout.write(data.content)
          result += data.content
        } else if (currentEvent === "tool_call") {
          process.stdout.write(`\n[use tool: ${data.name}]\n`)
        }
      }
    }
  }

  process.stdout.write("\n")
  return result
}


export async function confirmMemory(baseUrl: string, entryId: string): Promise<void> {
  const res = await fetch(`${baseUrl}/memories/${entryId}/confirm`, { method: "POST" })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

export async function rejectMemory(baseUrl: string, entryId: string): Promise<void> {
  const res = await fetch(`${baseUrl}/memories/${entryId}/reject`, { method: "POST" })
  if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`)
}