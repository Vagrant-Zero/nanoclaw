import { Box, Text } from "ink";

interface Props {
  content: string;
}

/**
 * Renders LLM reasoning text (agent_think event) in dimmed italic.
 * Shows the user what the agent is thinking before it acts.
 */
export function ThinkingBlock({ content }: Props) {
  if (!content) return null;
  return (
    <Box marginLeft={2} marginBottom={1}>
      <Text dimColor>thinking</Text>
      <Text dimColor>  {content.slice(0, 200)}</Text>
    </Box>
  );
}
