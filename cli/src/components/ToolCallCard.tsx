import { Box, Text } from "ink";

interface CriterionResult {
  text: string;
  passed: boolean;
  reason?: string;
}

interface Props {
  tool: string;
  args: Record<string, unknown>;
  result?: string;
  error?: string;
  isRunning?: boolean;
  checkCriteria?: CriterionResult[];
}

/**
 * Renders a tool call card with:
 * - action: tool name + args summary (when invoked)
 * - observation: result summary (when returned)
 * - per-criterion Check PASS/FAIL display (when available)
 * - colored border: yellow=running, green=success, red=error
 */
export function ToolCallCard({
  tool,
  args,
  result,
  error,
  isRunning,
  checkCriteria,
}: Props) {
  const borderColor = error ? "red" : isRunning ? "yellow" : "green";

  return (
    <Box
      flexDirection="column"
      marginLeft={2}
      marginBottom={1}
      borderStyle="round"
      borderColor={borderColor}
      padding={1}
    >
      {/* Header: tool name */}
      <Box>
        <Text bold color={borderColor}>
          {isRunning ? "▶" : error ? "✗" : "✓"}
        </Text>
        <Text bold> </Text>
        <Text bold color={borderColor}>
          [{tool}]
        </Text>
        {isRunning && <Text dimColor> (running...)</Text>}
      </Box>

      {/* Args */}
      {Object.keys(args).length > 0 && (
        <Box flexDirection="column" paddingLeft={2} marginTop={1}>
          <Text dimColor>args:</Text>
          {Object.entries(args).map(([key, value]) => (
            <Text key={key} dimColor>
              {"  "}
              {key}:{" "}
              {typeof value === "string" ? value : JSON.stringify(value)}
            </Text>
          ))}
        </Box>
      )}

      {/* Result */}
      {result !== undefined && (
        <Box flexDirection="column" paddingLeft={2} marginTop={1}>
          <Text dimColor>result:</Text>
          <Text wrap="wrap">{result.slice(0, 500)}</Text>
          {result.length > 500 && (
            <Text dimColor>
              ... (truncated, {result.length} chars total)
            </Text>
          )}
        </Box>
      )}

      {/* Error */}
      {error && (
        <Box flexDirection="column" paddingLeft={2} marginTop={1}>
          <Text color="red">error: {error}</Text>
        </Box>
      )}

      {/* Check criteria — per-criterion PASS/FAIL */}
      {checkCriteria && checkCriteria.length > 0 && (
        <Box
          flexDirection="column"
          paddingLeft={2}
          marginTop={1}
          borderStyle="single"
          borderColor="gray"
        >
          <Text bold dimColor>
            Check criteria:
          </Text>
          {checkCriteria.map((c, i) => (
            <Text key={i} color={c.passed ? "green" : "red"}>
              {"  "}
              {c.passed ? "✓" : "✗"} {c.text}
              {c.reason && !c.passed && (
                <Text color="yellow"> — {c.reason}</Text>
              )}
            </Text>
          ))}
        </Box>
      )}
    </Box>
  );
}
