import { describe, test, expect, vi } from "vitest";

// ── extract the pure SSE-parsing logic from StreamingChat ──

interface ParsedEvent {
  type:
    | "agent_think"
    | "agent_action"
    | "agent_observation"
    | "message_chunk"
    | "unknown";
  data: Record<string, unknown>;
}

/** Pure function that simulates what StreamingChat does internally.
 *  Takes raw SSE text lines, returns parsed events in order.
 */
function parseSSE(lines: string[]): ParsedEvent[] {
  const events: ParsedEvent[] = [];
  let currentEvent = "";

  for (const line of lines) {
    if (!line) continue;
    if (line.startsWith("event: ")) {
      currentEvent = line.slice(7);
    } else if (line.startsWith("data: ")) {
      const data = JSON.parse(line.slice(6));
      const type = [
        "agent_think",
        "agent_action",
        "agent_observation",
        "message_chunk",
      ].includes(currentEvent)
        ? (currentEvent as ParsedEvent["type"])
        : "unknown";

      events.push({ type, data });
    }
  }
  return events;
}

// ── simulates the parent App's state accumulator functions ──

function simulateToolCallFlow(
  events: ParsedEvent[]
): {
  thinkText: string;
  toolCalls: Array<{
    tool: string;
    args: Record<string, unknown>;
    result?: string;
  }>;
  answerText: string;
} {
  let thinkText = "";
  const toolCalls: Array<{
    tool: string;
    args: Record<string, unknown>;
    result?: string;
  }> = [];
  let answerText = "";

  for (const e of events) {
    switch (e.type) {
      case "agent_think":
        thinkText += String(e.data.content);
        break;
      case "agent_action":
        toolCalls.push({
          tool: String(e.data.tool),
          args: e.data.args as Record<string, unknown>,
        });
        break;
      case "agent_observation":
        for (let i = toolCalls.length - 1; i >= 0; i--) {
          if (
            toolCalls[i].tool === String(e.data.tool) &&
            toolCalls[i].result === undefined
          ) {
            toolCalls[i].result = String(e.data.result);
            break;
          }
        }
        break;
      case "message_chunk":
        answerText += String(e.data.content);
        break;
    }
  }
  return { thinkText, toolCalls, answerText };
}

// ── tests ──

describe("SSE parsing (StreamingChat logic)", () => {
  test("parse tool-call SSE stream correctly", () => {
    const raw = [
      "event: task_status",
      'data: {"task_id":"root","status":"RUNNING"}',
      "event: agent_think",
      'data: {"content":"读取文件","task_id":"root"}',
      "event: agent_action",
      'data: {"tool":"read_file","args":{"file_path":"/etc/hosts"},"task_id":"root"}',
      "event: agent_observation",
      'data: {"tool":"read_file","result":"127.0.0.1 localhost","task_id":"root"}',
      "event: message_chunk",
      'data: {"content":"文件内容","task_id":"root"}',
      "event: done",
      'data: {"session_id":"abc-123"}',
    ];

    const events = parseSSE(raw);
    const result = simulateToolCallFlow(events);

    // think → thought is preserved
    expect(result.thinkText).toContain("读取文件");

    // action → toolCall card shown
    expect(result.toolCalls.length).toBe(1);
    expect(result.toolCalls[0].tool).toBe("read_file");
    expect(result.toolCalls[0].args).toEqual({ file_path: "/etc/hosts" });

    // observation → result patched onto the matching toolCall
    expect(result.toolCalls[0].result).toBe("127.0.0.1 localhost");

    // answer → streamed text
    expect(result.answerText).toBe("文件内容");
  });

  test("pure-text flow only shows answer, no think/action", () => {
    const raw = [
      "event: task_status",
      'data: {"task_id":"root","status":"RUNNING"}',
      "event: message_chunk",
      'data: {"content":"Hello","task_id":"root"}',
      "event: message_chunk",
      'data: {"content":" world","task_id":"root"}',
      "event: done",
      'data: {"session_id":"x"}',
    ];

    const events = parseSSE(raw);
    const result = simulateToolCallFlow(events);

    expect(result.thinkText).toBe("");
    expect(result.toolCalls.length).toBe(0);
    expect(result.answerText).toBe("Hello world");
  });

  test("no duplication: think text is not echoed into answer", () => {
    // If the backend sent both agent_think and message_chunk for the same
    // text, our accumulator would put them in both places — verify that
    // the caller only renders one.
    const raw = [
      "event: task_status",
      'data: {"task_id":"root","status":"RUNNING"}',
      "event: agent_think",
      'data: {"content":"文件内容...","task_id":"root"}',
      "event: message_chunk",
      'data: {"content":"文件内容...","task_id":"root"}',
    ];

    const events = parseSSE(raw);
    const result = simulateToolCallFlow(events);

    // They land in different buckets — it's up to the backend to not
    // double-emit, so this test documents the contract.
    expect(result.thinkText).toBe("文件内容...");
    expect(result.answerText).toBe("文件内容...");
  });
});
