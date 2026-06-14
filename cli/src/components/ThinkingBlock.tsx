import { Box, Text } from "ink";

interface CheckFeedback {
  check_feedback: string;
  passed: boolean;
}

interface Props {
  content: string;
  taskId?: string;
  checkFeedback?: CheckFeedback;
}

/**
 * Renders LLM reasoning text (agent_think event) in dimmed text.
 * Content is cumulative — parent should pass the full accumulated text.
 * When Check feedback is available, shows a PASS/FAIL panel beneath.
 */
export function ThinkingBlock({ content, taskId, checkFeedback }: Props) {
  if (!content) return null;
  const lines = content.split("\n");
  return (
    <Box flexDirection="column" marginLeft={2} marginBottom={1}>
      {taskId && (
        <Text dimColor italic>
          [think:{taskId}]
        </Text>
      )}
      {lines.map((line, i) => (
        <Text key={i} dimColor italic>
          {line || " "}
        </Text>
      ))}
      {checkFeedback && (
        <Box
          flexDirection="column"
          marginTop={1}
          paddingLeft={2}
          borderStyle="round"
          borderColor={checkFeedback.passed ? "green" : "yellow"}
        >
          <Text bold color={checkFeedback.passed ? "green" : "yellow"}>
            {checkFeedback.passed ? "✓ Check passed" : "△ Check feedback"}
          </Text>
          <Text dimColor>{checkFeedback.check_feedback}</Text>
        </Box>
      )}
    </Box>
  );
}
