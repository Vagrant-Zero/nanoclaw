import { Box, Text } from "ink";

interface CheckResultBadgeProps {
  text: string;
  passed: boolean;
  reason: string;
}

export function CheckResultBadge({ text, passed, reason }: CheckResultBadgeProps) {
  const icon = passed ? "PASS" : "FAIL";
  const color = passed ? "green" : "red";
  return (
    <Box>
      <Text color={color}>{`[${icon}] `}</Text>
      <Text>{text}</Text>
      {!passed && <Text dimColor>{` (${reason})`}</Text>}
    </Box>
  );
}

interface CheckResultsPanelProps {
  results: CheckResultBadgeProps[];
}

export function CheckResultsPanel({ results }: CheckResultsPanelProps) {
  if (!results || results.length === 0) return null;

  const passed = results.filter((r) => r.passed).length;
  const total = results.length;

  return (
    <Box
      flexDirection="column"
      padding={1}
      borderStyle="round"
      borderColor="yellow"
    >
      <Text bold>
        Check Results ({passed}/{total} passed)
      </Text>
      {results.map((r, i) => (
        <CheckResultBadge key={i} {...r} />
      ))}
    </Box>
  );
}
