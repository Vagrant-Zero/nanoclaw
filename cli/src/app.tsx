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
import { confirmMemory, rejectMemory, listSchedules, createSchedule, deleteSchedule, toggleSchedule } from "./client.js";

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
    if (value.startsWith("/schedule")) {
      handleScheduleCommand(value);
      setInput("");
      return;
    }
    if (value === "/dream") {
      triggerDreaming();
      setInput("");
      return;
    }
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

  const handleScheduleCommand = async (cmd: string) => {
    const parts = cmd.trim().split(/\s+/);
    const subcmd = parts[1];
    switch (subcmd) {
      case "list": {
        try {
          const resp = await listSchedules(config.baseUrl);
          if (resp.tasks.length === 0) {
            setMessages((prev) => [...prev, { content: "No scheduled tasks.", role: "assistant" }]);
            return;
          }
          const lines = resp.tasks.map((t: any) => {
            const status = t.enabled ? "ON " : "OFF";
            return t.id.padEnd(12) + " " + status + " " + t.schedule.padEnd(12) + " " + t.description;
          });
          setMessages((prev) => [...prev, { content: lines.join("\n"), role: "assistant" }]);
        } catch (e) {
          setMessages((prev) => [...prev, { content: "Error: " + e, role: "assistant" }]);
        }
        return;
      }
      case "add": {
        if (parts.length < 4) {
          setMessages((prev) => [...prev, { content: "Usage: /schedule add <cron> <description> [prompt]", role: "assistant" }]);
          return;
        }
        const schedule = parts[2];
        const description = parts[3];
        const prompt = parts.slice(4).join(" ") || description;
        try {
          const resp = await createSchedule(config.baseUrl, { description, prompt, schedule });
          setMessages((prev) => [...prev, { content: "Created: " + resp.task.id, role: "assistant" }]);
        } catch (e) {
          setMessages((prev) => [...prev, { content: "Error: " + e, role: "assistant" }]);
        }
        return;
      }
      case "remove": {
        if (!parts[2]) {
          setMessages((prev) => [...prev, { content: "Usage: /schedule remove <id>", role: "assistant" }]);
          return;
        }
        try {
          await deleteSchedule(config.baseUrl, parts[2]);
          setMessages((prev) => [...prev, { content: "Removed: " + parts[2], role: "assistant" }]);
        } catch (e) {
          setMessages((prev) => [...prev, { content: "Error: " + e, role: "assistant" }]);
        }
        return;
      }
      case "toggle": {
        if (!parts[2]) {
          setMessages((prev) => [...prev, { content: "Usage: /schedule toggle <id>", role: "assistant" }]);
          return;
        }
        try {
          await toggleSchedule(config.baseUrl, parts[2]);
          setMessages((prev) => [...prev, { content: "Toggled: " + parts[2], role: "assistant" }]);
        } catch (e) {
          setMessages((prev) => [...prev, { content: "Error: " + e, role: "assistant" }]);
        }
        return;
      }
      default:
        setMessages((prev) => [...prev, { content: "Commands: list, add, remove, toggle", role: "assistant" }]);
    }
  };

  const triggerDreaming = async () => {
    setMessages((prev) => [...prev, { content: "Triggering dreaming...", role: "assistant" }]);
    try {
      const res = await fetch(config.baseUrl + "/dream", { method: "POST" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      setMessages((prev) => [...prev, { content: "Dreaming complete for " + data.date, role: "assistant" }]);
    } catch (e) {
      setMessages((prev) => [...prev, { content: "Dreaming failed: " + e, role: "assistant" }]);
    }
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
