import { Text, Box } from "ink"

export function StatusBar({ baseUrl }: { baseUrl: string }) {
  return (
      <Box flexDirection="column">
          <Text dimColor>{"─".repeat(40)}</Text>
          <Text dimColor>Connected | {baseUrl}</Text>
      </Box>
  )
}