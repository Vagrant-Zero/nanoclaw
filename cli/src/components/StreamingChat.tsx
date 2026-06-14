import { useEffect, useRef, useState } from "react";
import { Text } from "ink";
import type {
  SubtaskInfo,
  CheckResultData,
  IterationExhaustedData,
} from "../types.js";
import type { ExperienceEntry } from "../types.js";

interface Props {
  baseUrl: string;
  message: string;
  onDone: (text: string) => void;
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
 * Opens an SSE stream to /chat/stream, parses the protocol events.
 *
 * Phase 1 events: agent_think, agent_action, agent_observation, message_chunk
 * Phase 2 events: agent_plan, task_status, check_result, iteration_exhausted
 * All forwarded to their respective callbacks.
 */
export function StreamingChat({
  baseUrl,
  message,
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
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    if (!message) return;
    let cancelled = false;
    let fullText = "";

    const run = async () => {
      try {
        const res = await fetch(`${baseUrl}/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
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
        let currentEvent = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done || cancelled) break;

          const text = decoder.decode(value, { stream: true });
          const lines = text.split(/\r?\n/);

          for (const line of lines) {
            if (!line) continue;
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7);
            } else if (line.startsWith("data: ")) {
              const data = JSON.parse(line.slice(6));
              switch (currentEvent) {
                case "agent_think":
                  onThink?.(data.content);
                  break;
                case "agent_action":
                  onAction?.(data.tool, data.args);
                  break;
                case "agent_observation":
                  onObservation?.(data.tool, data.result);
                  break;
                case "agent_plan":
                  onPlan?.(data.tasks ?? []);
                  break;
                case "task_status":
                  onTaskStatus?.(data.task_id, data.status);
                  break;
                case "check_result":
                  onCheckResult?.(data);
                  break;
                case "iteration_exhausted":
                  onIterationExhausted?.(data);
                  break;
                case "experience_ready":
                  onExperience?.(data);
                  break;
                case "error":
                  setError(data.message || "Backend error");
                  break;
                case "message_chunk":
                  fullText += data.content;
                  setContent(fullText);
                  break;
              }
            }
          }
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || "Connection failed");
        }
        return;
      }

      if (!cancelled) {
        onDoneRef.current(fullText);
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [message]);

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
