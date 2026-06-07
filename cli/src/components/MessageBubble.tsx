import { Text, Box } from "ink"
import {ChatMessage} from "../types";

export function MessageBubble({ content, role }: ChatMessage) {
    const label = role === "user" ? "You" : "AI"
    const color = role === "user" ? "blue": "yellow"
    return (
        <Box>
            <Text bold color={color}>{label}</Text>
            <Text>: {content}</Text>
        </Box>
    )
}