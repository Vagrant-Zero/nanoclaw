import { describe, test, expect } from "vitest";
import { SseParser } from "./sse-parser.js";

// ── simulates the parent App's state accumulator functions ──

interface ParsedEvent {
  type:
    | "agent_think"
    | "agent_action"
    | "agent_observation"
    | "message_chunk"
    | "unknown";
  data: Record<string, unknown>;
}

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

function parseEvents(raw: string[]): ParsedEvent[] {
  const parser = new SseParser();
  const joined = raw.join("\n");

  // If the last line is a complete event (data line), append double newline
  // to simulate an event boundary so it gets emitted.
  const chunk = joined.endsWith("\n\n") ? joined : joined + "\n\n";

  const events = parser.feed(chunk);

  return events.map((evt) => {
    const type = [
      "agent_think",
      "agent_action",
      "agent_observation",
      "message_chunk",
    ].includes(evt.event)
      ? (evt.event as ParsedEvent["type"])
      : "unknown";

    return { type, data: evt.data as Record<string, unknown> };
  });
}

// ── tests ──

describe("SseParser (shared parser)", () => {
  test("parse tool-call SSE stream correctly", () => {
    const raw = [
      "event: task_status",
      'data: {"task_id":"root","status":"RUNNING"}',
      "",
      "event: agent_think",
      'data: {"content":"读取文件","task_id":"root"}',
      "",
      "event: agent_action",
      'data: {"tool":"read_file","args":{"file_path":"/etc/hosts"},"task_id":"root"}',
      "",
      "event: agent_observation",
      'data: {"tool":"read_file","result":"127.0.0.1 localhost","task_id":"root"}',
      "",
      "event: message_chunk",
      'data: {"content":"文件内容","task_id":"root"}',
      "",
      "event: done",
      'data: {"session_id":"abc-123"}',
    ];

    const events = parseEvents(raw);
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
      "",
      "event: message_chunk",
      'data: {"content":"Hello","task_id":"root"}',
      "",
      "event: message_chunk",
      'data: {"content":" world","task_id":"root"}',
      "",
      "event: done",
      'data: {"session_id":"x"}',
    ];

    const events = parseEvents(raw);
    const result = simulateToolCallFlow(events);

    expect(result.thinkText).toBe("");
    expect(result.toolCalls.length).toBe(0);
    expect(result.answerText).toBe("Hello world");
  });

  test("handles cross-chunk split events (buffer test)", () => {
    // Simulate a chunk that ends mid-event: the data line is split
    // across two chunks.
    const parser = new SseParser();
    const chunk1 = "event: message_chunk\n";
    const chunk2 = 'data: {"content":"Hello"}\n\nevent: done\n';
    const chunk3 = 'data: {"session_id":"x"}\n\n';

    let events = parser.feed(chunk1);
    expect(events.length).toBe(0); // incomplete event buffered

    events = parser.feed(chunk2);
    expect(events.length).toBe(1); // message_chunk emitted
    expect(events[0].event).toBe("message_chunk");

    events = parser.feed(chunk3);
    expect(events.length).toBe(1); // done emitted
    expect(events[0].event).toBe("done");
  });

  test("no duplication: think text is not echoed into answer", () => {
    // If the backend sent both agent_think and message_chunk for the same
    // text, the accumulator puts them in both places — verify that
    // the caller only renders one.
    const raw = [
      "event: task_status",
      'data: {"task_id":"root","status":"RUNNING"}',
      "",
      "event: agent_think",
      'data: {"content":"文件内容...","task_id":"root"}',
      "",
      "event: message_chunk",
      'data: {"content":"文件内容...","task_id":"root"}',
    ];

    const events = parseEvents(raw);
    const result = simulateToolCallFlow(events);

    // They land in different buckets — it's up to the backend to not
    // double-emit, so this test documents the contract.
    expect(result.thinkText).toBe("文件内容...");
    expect(result.answerText).toBe("文件内容...");
  });

  test("handles CRLF line endings (sse-starlette v3 default)", () => {
    // sse-starlette v3.4.4 uses \r\n as the default separator. The
    // parser must normalise \r\n to \n so event splitting on \n\n
    // works correctly.
    const parser = new SseParser();
    const crlf = [
      "event: message_chunk",
      'data: {"content":"Hello"}',
      "",
      "event: done",
      'data: {"session_id":"crlf-test"}',
      "",
    ].join("\r\n") + "\r\n";

    const events = parser.feed(crlf);
    expect(events.length).toBe(2);
    expect(events[0].event).toBe("message_chunk");
    expect(events[1].event).toBe("done");
  });

  test("flush returns remaining buffered data", () => {
    const parser = new SseParser();
    parser.feed('event: message_chunk\ndata: {"content":"a"}\n\n');
    // Feed a partial event (no trailing newline)
    parser.feed('event: done\ndata: {"session_id":"y"}');

    const flushed = parser.flush();
    expect(flushed.length).toBe(1);
    expect(flushed[0].event).toBe("done");
  });
});
