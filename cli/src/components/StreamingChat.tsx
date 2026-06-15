import { useEffect, useRef, useState } from "react";
import { Text } from "ink";
import { SseParser } from "../sse-parser.js";
import type {
  SubtaskInfo,
  CheckResultData,
  IterationExhaustedData,
} from "../types.js";
import type { ExperienceEntry } from "../types.js";

interface Props {
  baseUrl: string;
  message: string;
  threadId?: string;
  onDone: (text: string, sessionId: string) => void;
  onThink?: (content: string) => void;
  onAction?: (tool: string, args: Record<string, unknown>) => void;
  onObservation?: (tool: string, result: string) => void;
  onPlan?: (tasks: SubtaskInfo[]) => void;
  onTaskStatus?: (taskId: string, status: string) => void;
  onCheckResult?: (data: CheckResultData) => void;
  onIterationExhausted?: (data: IterationExhaustedData) => void;
  onExperience?: (experience: ExperienceEntry) => void;
}

/**
 * Opens an SSE stream to /chat/stream, parses events using the shared
 * SSE parser, and dispatches them to the relevant callbacks.
 *
 * Fixes over the previous implementation:
 * - Uses SseParser for proper event buffering (handles cross-chunk splits)
 * - Uses AbortController — component cleanup actually aborts the fetch
 * - Handles `done` event explicitly (not just connection close)
 * - Accepts `threadId` prop for multi-turn session continuity
 * - All callbacks stored via refs to eliminate stale closure issues
 */
export function StreamingChat({
  baseUrl,
  message,
  threadId,
  onDone,
  onThink,
  onAction,
  onObservation,
  onPlan,
  onTaskStatus,
  onCheckResult,
  onIterationExhausted,
  onExperience,
}: Props) {
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  // Refs for mutable state shared with the async loop
  const fullTextRef = useRef("");
  const sessionIdRef = useRef("");

  // Refs for callbacks — prevents stale closures in useEffect
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const onThinkRef = useRef(onThink);
  onThinkRef.current = onThink;
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;
  const onObservationRef = useRef(onObservation);
  onObservationRef.current = onObservation;
  const onPlanRef = useRef(onPlan);
  onPlanRef.current = onPlan;
  const onTaskStatusRef = useRef(onTaskStatus);
  onTaskStatusRef.current = onTaskStatus;
  const onCheckResultRef = useRef(onCheckResult);
  onCheckResultRef.current = onCheckResult;
  const onIterationExhaustedRef = useRef(onIterationExhausted);
  onIterationExhaustedRef.current = onIterationExhausted;
  const onExperienceRef = useRef(onExperience);
  onExperienceRef.current = onExperience;

  useEffect(() => {
    if (!message) return;
    let completed = false;
    const abortController = new AbortController();
    fullTextRef.current = "";

    const run = async () => {
      try {
        const res = await fetch(`${baseUrl}/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, thread_id: threadId ?? null }),
          signal: abortController.signal,
        });

        if (!res.ok) {
          const body = await res.text().catch(() => "");
          setError(`Backend error: HTTP ${res.status}${body ? ` — ${body.slice(0, 200)}` : ""}`);
          return;
        }

        if (!res.body) {
          setError("Backend returned empty response body");
          return;
        }

        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();

        while (true) {
          const { done, value } = await reader.read();

          // Stream ended by the server — flush parser buffer
          if (done) {
            for (const evt of parser.flush()) {
              dispatch(evt.event, evt.data);
            }
            break;
          }

          const text = decoder.decode(value, { stream: true });
          for (const evt of parser.feed(text)) {
            dispatch(evt.event, evt.data);
          }
        }
      } catch (err: unknown) {
        // AbortError is expected on component unmount, not a real error
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (!completed) {
          setError((err as Error)?.message || "Connection failed");
        }
        return;
      }

      // After the stream ends (either via done event or connection close),
      // call onDone in the next macrotask so React can flush
      // setContent updates before the parent's setStreamingMsg(null)
      // triggers component unmount (React 18 batch avoidance).
      const finalText = fullTextRef.current;
      // Use a longer timeout so Ink has time to flush the rendered
      // content to the terminal before onDone triggers component unmount
      // via the parent's setStreamingMsg(null).
      setTimeout(() => {
        onDoneRef.current(finalText, completed ? sessionIdRef.current : "");
      }, 100);
    };

    const dispatch = (event: string, data: unknown) => {
      if (typeof data !== "object" || data === null) return;
      const d = data as Record<string, unknown>;

      switch (event) {
        case "agent_think":
          onThinkRef.current?.(d.content as string);
          break;
        case "agent_action":
          onActionRef.current?.(d.tool as string, d.args as Record<string, unknown>);
          break;
        case "agent_observation":
          onObservationRef.current?.(d.tool as string, d.result as string);
          break;
        case "agent_plan":
          onPlanRef.current?.((d.tasks ?? []) as SubtaskInfo[]);
          break;
        case "task_status":
          onTaskStatusRef.current?.(d.task_id as string, d.status as string);
          break;
        case "check_result":
          onCheckResultRef.current?.(d as unknown as CheckResultData);
          break;
        case "iteration_exhausted":
          onIterationExhaustedRef.current?.(d as unknown as IterationExhaustedData);
          break;
        case "experience_ready":
          onExperienceRef.current?.(d as unknown as ExperienceEntry);
          break;
        case "error":
          // Append error to content instead of hiding content — the error
          // text is displayed inline so the user sees what happened
          fullTextRef.current += (d.message as string) || "Backend error";
          setContent(fullTextRef.current);
          break;
        case "message_chunk":
          fullTextRef.current += d.content as string;
          setContent(fullTextRef.current);
          break;
        case "done":
          completed = true;
          sessionIdRef.current = (d as Record<string, unknown>).session_id as string || "";
          break;
      }
    };

    run();

    // Cleanup: abort the fetch on unmount
    return () => {
      abortController.abort();
    };
  }, [message, baseUrl, threadId]);

  if (error) {
    return <Text color="red">Error: {error}</Text>;
  }

  if (!connected) {
    return <Text dimColor>Connecting...</Text>;
  }

  if (!content) {
    return <Text dimColor italic>Thinking...</Text>;
  }

  return <Text>{content}</Text>;
}

export default StreamingChat;
