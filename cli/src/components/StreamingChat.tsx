import { useEffect, useState } from "react";
import { Text } from "ink";

interface Props {
    baseUrl: string
    message: string
    onDone: (text: string) => void
}

export function StreamingChat({baseUrl, message, onDone}: Props) {
    const [content, setContent] = useState("")

    useEffect(() => {
        if (!message) {
            return
        }
        let cancelled = false
        const run = async () => {
            const res=  await fetch(`${baseUrl}/chat/stream`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    message,
                }),
            })
            const reader = res.body!.getReader()
            const decoder = new TextDecoder()
            let currentEvent = ""
            let fullText = ""

            while(true) {
                const { done, value } = await reader.read()
                if (done || cancelled) {
                    break
                }

                const text = decoder.decode(value, {stream: true})
                const lines = text.split(/\r?\n/)

                for (const line of lines) {
                    if (!line) continue
                    if (line.startsWith("event: ")) {
                        currentEvent = line.slice(7)
                    } else if (line.startsWith("data: ")) {
                        const data = JSON.parse(line.slice(6))
                        if (currentEvent === "message_chunk") {
                            fullText += data.content
                            setContent(fullText) // 逐字更新
                        }
                    }
                }
            }
            if (!cancelled) onDone(fullText)
        }

        run()
        return () => {cancelled=true}
    }, [message]);

    return <Text>{content}</Text>
}
