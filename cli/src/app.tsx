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

interface ToolCallState {
  tool: string;
  args: Record<string, unknown>;
  result?: string;
}

function App() {
  useEffect(() => {
    console.log("base_url:", config.baseUrl);
  }, []);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinkText, setThinkText] = useState<string>("");
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);
  const [streamingMsg, setStreamingMsg] = useState<string | null>(null);
  const [exiting, setExiting] = useState(false);
  const [input, setInput] = useState<string>("");

  useInput((_data, key) => {
    if (key.escape) {
      process.exit(0);
    }
  });

  const handleSubmit = (value: string) => {
    if (value === "/exit") {
      setExiting(true);
      setTimeout(() => process.exit(0), 100);
      return;
    }
    setMessages((prev) => [...prev, { content: value, role: "user" }]);
    setThinkText("");
    setToolCalls([]);
    setStreamingMsg(value);
    setInput("");
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

      {/* User + assistant messages */}
      {messages.map((msg, i) => (
        <MessageBubble key={i} content={msg.content} role={msg.role} />
      ))}

      {/* Thinking text — single block, cumulative */}
      {thinkText.length > 0 && <ThinkingBlock content={thinkText} />}

      {/* Tool call cards */}
      {toolCalls.map((tc, i) => (
        <ToolCallCard
          key={i}
          tool={tc.tool}
          args={tc.args}
          result={tc.result}
        />
      ))}

      {/* Streaming chat */}
      {streamingMsg && (
        <StreamingChat
          baseUrl={config.baseUrl}
          message={streamingMsg}
          onDone={handleStreamDone}
          onThink={(content) => setThinkText((prev) => prev + content)}
          onAction={(tool, args) =>
            setToolCalls((prev) => [...prev, { tool, args }])
          }
          onObservation={(tool, result) =>
            setToolCalls((prev) =>
              prev.map((tc) =>
                tc.tool === tool && tc.result === undefined
                  ? { ...tc, result }
                  : tc
              )
            )
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
