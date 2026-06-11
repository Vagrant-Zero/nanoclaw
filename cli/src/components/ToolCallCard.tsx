import { Box, Text } from "ink";
import { AgentActionData } from "../types";

interface ToolCallCardProps {
  tool: string;
  args: Record<string, unknown>;
  result?: string;
}

/**
 * Renders a tool call card:
 * - action: tool name + args summary (when invoked)
 * - observation: result summary (when returned)
 */
export function ToolCallCard({ tool, args, result }: ToolCallCardProps) {
  return (
    <Box flexDirection="column" marginLeft={2} marginBottom={1} borderStyle="round" borderColor="blue" padding={1}>
      <Box>
        <Text color="cyan">[{tool}]</Text>
        <Text dimColor>  {summarizeArgs(args)}</Text>
      </Box>
      {result !== undefined && (
        <Box marginTop={1}>
          <Text color="green">result</Text>
          <Text>  {result.slice(0, 200)}</Text>
        </Box>
      )}
    </Box>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`)
    .join("  ");
}
