import { render, Text, Box, useInput } from "ink";
import { useEffect, useState } from "react";
import TextInput from "ink-text-input";
import { ChatMessage } from "./types.js";
import { MessageBubble } from "./components/MessageBubble.js";
import { loadConfig } from "./config.js";
import { StreamingChat } from "./components/StreamingChat.js";
import { StatusBar } from "./components/bar.js";
import { ThinkingBlock } from "./components/ThinkingBlock.js";
import { ToolCallCard } from "./components/ToolCallCard.js";

const config = await loadConfig();

/** Intermediate ReAct step (think / action / observation) */
interface ReActStep {
  type: "think" | "action" | "observation";
  content?: string; // think text
  tool?: string; // action / observation
  args?: Record<string, unknown>; // action
  result?: string; // observation
}

function App() {
  useEffect(() => {
    console.log("base_url:", config.baseUrl);
  }, []);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [reactSteps, setReactSteps] = useState<ReActStep[]>([]);
  const [streamingMsg, setStreamingMsg] = useState<string | null>(null);
  const [exiting, setExiting] = useState(false);

  // exercise
  const [count, setCount] = useState<number>(0);
  const [input, setInput] = useState<string>("");

  useInput((data, key) => {
    if (key.escape) {
      process.exit(0);
    }
    if (data === "r") {
      setCount(0);
    } else {
      setCount(count + 1);
    }
  });

  const handleSubmit = (value: string) => {
    if (value === "/exit") {
      setExiting(true);
      setTimeout(() => process.exit(0), 100);
      return;
    }
    setMessages([...messages, { content: value, role: "user" }]);
    setReactSteps([]); // new request → clear previous steps
    setStreamingMsg(value);
    setInput("");
  };

  const addStep = (step: ReActStep) => {
    setReactSteps((prev) => [...prev, step]);
  };

  const handleStreamDone = (fullText: string) => {
    setMessages((prev) => [...prev, { content: fullText, role: "assistant" }]);
    setStreamingMsg(null);
  };

  return (
    <Box flexDirection="column" padding={1}>
      <Text bold color="green">
        Nanoclaw cli
      </Text>
      <Text dimColor>Type /exit to quit</Text>
      <Text dimColor>Messages: {messages.length}</Text>

      {/* User + assistant messages */}
      {messages.map((msg, i) => (
        <MessageBubble key={i} content={msg.content} role={msg.role} />
      ))}

      {/* ReAct step stream for the current request */}
      {reactSteps.length > 0 && (
        <Box flexDirection="column">
          <Text dimColor>steps [{reactSteps.length}]</Text>
          {reactSteps.map((step, i) =>
            step.type === "think" ? (
              <ThinkingBlock key={i} content={step.content ?? ""} />
            ) : step.type === "action" ? (
              <ToolCallCard
                key={i}
                tool={step.tool ?? "?"}
                args={step.args ?? {}}
              />
            ) : step.type === "observation" ? (
              <ToolCallCard
                key={i}
                tool={step.tool ?? "?"}
                args={{}}
                result={step.result ?? ""}
              />
            ) : null
          )}
        </Box>
      )}

      <Text dimColor>count: {count}</Text>

      {/* Streaming chat (wires up ReAct callbacks) */}
      {streamingMsg && (
        <StreamingChat
          baseUrl={config.baseUrl}
          message={streamingMsg}
          onDone={handleStreamDone}
          onThink={(content) => addStep({ type: "think", content })}
          onAction={(tool, args) =>
            addStep({ type: "action", tool, args })
          }
          onObservation={(tool, result) =>
            addStep({ type: "observation", tool, result })
          }
        />
      )}

      <Box>
        <Text>&gt;</Text>
        <TextInput value={input} onChange={setInput} onSubmit={handleSubmit} />
      </Box>
      <StatusBar baseUrl={config.baseUrl} />
      {exiting && <Text dimColor>Goodbye!</Text>}
    </Box>
  );
}

process.on("uncaughtException", (err) => {
  process.stderr.write("\n[ERROR] " + err.message + "\n");
  if (err.stack) {
    const lines = err.stack.split("\n");
    for (const line of lines.slice(0, 8)) {
      if (line.includes("/src/")) process.stderr.write(line + "\n");
    }
  }
  process.exit(1);
});

render(<App />);
