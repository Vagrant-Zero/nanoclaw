import { Box, Text } from "ink";

interface Props {
  content: string;
}

/**
 * Renders LLM reasoning text (agent_think event) in dimmed text.
 * Content is cumulative — parent should pass the full accumulated text.
 */
export function ThinkingBlock({ content }: Props) {
  if (!content) return null;
  return (
    <Box marginLeft={2} marginBottom={1}>
      <Text dimColor>{content.slice(0, 300)}</Text>
    </Box>
  );
}
