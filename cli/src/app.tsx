import { render, Text, Box, useInput } from "ink";
import { useEffect, useState } from "react";
import TextInput from "ink-text-input";
import { ChatMessage, SubtaskInfo, CheckResultData, IterationExhaustedData, TaskStatus, ExperienceEntry } from "./types.js";
import { MessageBubble } from "./components/MessageBubble.js";
import { loadConfig } from "./config.js";
import { StreamingChat } from "./components/StreamingChat.js";
import { StatusBar } from "./components/bar.js";
import { ThinkingBlock } from "./components/ThinkingBlock.js";
import { ToolCallCard } from "./components/ToolCallCard.js";
import { PlanView } from "./components/PlanView.js";
import { CheckResultsPanel } from "./components/CheckResultBadge.js";
import { ExperienceFeedback } from "./components/ExperienceFeedback.js";
import { confirmMemory, rejectMemory } from "./client.js";

const config = await loadConfig();

interface ToolCallState {
  tool: string;
  args: Record<string, unknown>;
  result?: string;
}

interface CheckResultEntry {
  task_id: string;
  passed: boolean;
  feedback: string;
}

function App() {
  useEffect(() => {
    console.log("base_url:", config.baseUrl);
  }, []);

  // Phase 1 state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinkText, setThinkText] = useState<string>("");
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);
  const [streamingMsg, setStreamingMsg] = useState<string | null>(null);
  const [exiting, setExiting] = useState(false);
  const [input, setInput] = useState<string>("");

  // Phase 2 state
  const [currentPlan, setCurrentPlan] = useState<SubtaskInfo[]>([]);
  const [checkResults, setCheckResults] = useState<CheckResultEntry[]>([]);
  const [budgetExhausted, setBudgetExhausted] = useState<IterationExhaustedData | null>(null);
  const [pendingExperiences, setPendingExperiences] = useState<ExperienceEntry[]>([]);

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
    setCurrentPlan([]);
    setCheckResults([]);
    setBudgetExhausted(null);
    setPendingExperiences([]);
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

      {/* Phase 2: plan view (subtask DAG) */}
      {currentPlan.length > 0 && <PlanView tasks={currentPlan} />}

      {/* Phase 2: check results */}
      {checkResults.length > 0 && (
        <CheckResultsPanel
          results={checkResults.map((r) => ({
            text: r.feedback,
            passed: r.passed,
            reason: r.feedback,
          }))}
        />
      )}

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
          onPlan={(tasks) => setCurrentPlan(tasks)}
          onTaskStatus={(taskId, status) =>
            setCurrentPlan((prev) =>
              prev.map((t) => (t.id === taskId ? { ...t, status: status as TaskStatus } : t))
            )
          }
          onCheckResult={(data) =>
            setCheckResults((prev) => [
              ...prev,
              {
                task_id: data.task_id,
                passed: data.passed,
                feedback: data.feedback,
              },
            ])
          }
          onIterationExhausted={(data) => setBudgetExhausted(data)}
          onExperience={(exp) => setPendingExperiences((prev) => [...prev, exp])}
        />
      )}

      {/* Experience feedback */}
      {pendingExperiences.length > 0 && (
        <ExperienceFeedback
          experience={pendingExperiences[0]}
          onConfirm={async () => {
            await confirmMemory(config.baseUrl, pendingExperiences[0].entry_id);
            setPendingExperiences((prev) => prev.slice(1));
          }}
          onReject={async () => {
            await rejectMemory(config.baseUrl, pendingExperiences[0].entry_id);
            setPendingExperiences((prev) => prev.slice(1));
          }}
          onDismiss={() => setPendingExperiences((prev) => prev.slice(1))}
        />
      )}

      {/* Budget exhausted warning */}
      {budgetExhausted && (
        <Box
          flexDirection="column"
          padding={1}
          borderStyle="round"
          borderColor="red"
        >
          <Text bold color="red">
            Iteration Budget Exhausted
          </Text>
          <Text>
            Failed subtasks: {budgetExhausted.failed_subtask_ids.join(", ")}
          </Text>
          <Text dimColor>
            Options: Cancel the task or adjust parameters and resume.
          </Text>
        </Box>
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
