import { useEffect, useState } from "react";
import { Text } from "ink";

interface Props {
  baseUrl: string;
  message: string;
  onDone: (text: string) => void;
  onThink?: (content: string) => void;
  onAction?: (tool: string, args: Record<string, unknown>) => void;
  onObservation?: (tool: string, result: string) => void;
}

/**
 * Opens an SSE stream to /chat/stream, parses the new protocol events,
 * and calls the provided callbacks so the parent can render ReAct steps.
 */
export function StreamingChat({
  baseUrl,
  message,
  onDone,
  onThink,
  onAction,
  onObservation,
}: Props) {
  const [content, setContent] = useState("");

  useEffect(() => {
    if (!message) return;
    let cancelled = false;

    const run = async () => {
      const res = await fetch(`${baseUrl}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let currentEvent = "";
      let fullText = "";

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
              case "message_chunk":
                fullText += data.content;
                setContent(fullText);
                break;
            }
          }
        }
      }
      if (!cancelled) onDone(fullText);
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [message]);

  return <Text>{content}</Text>;
}
