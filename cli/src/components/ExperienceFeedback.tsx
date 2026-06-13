import { Box, Text, useInput } from "ink"
import type { ExperienceEntry } from "../types.js"

interface Props {
  experience: ExperienceEntry
  onConfirm: () => void
  onReject: () => void
  onDismiss: () => void
}

export function ExperienceFeedback({ experience, onConfirm, onReject, onDismiss }: Props) {
  useInput((input) => {
    if (input === "t") {
      onConfirm()
    } else if (input === "f") {
      onReject()
    } else {
      onDismiss()
    }
  })

  return (
    <Box flexDirection="column" padding={1} borderStyle="round" borderColor="yellow">
      <Text dimColor>New insight: {experience.summary.slice(0, 60)}</Text>
      <Text dimColor>[t] confirm  [f] reject  [any] dismiss</Text>
    </Box>
  )
}
