import { render, Text, Box, useInput } from "ink"
import { useEffect, useState} from "react";
import TextInput from "ink-text-input";
import { ChatMessage } from "./types";
import { MessageBubble } from "./components/MessageBubble.js";
import { loadConfig } from "./config.js";
import { StreamingChat } from "./components/StreamingChat.js";
import { StatusBar } from "./components/bar.js";

const config = await loadConfig()

function App() {
    useEffect(() => {
        console.log("base_url:", config.baseUrl)
    }, [])

    const [messages, setMessages] = useState<ChatMessage[]>([])
    const [streamingMsg, setStreamingMsg] = useState<string | null>(null)
    const [exiting, setExiting] = useState(false)

    // exercise
    const [count, setCount] = useState<number>(0);
    const [input, setInput] = useState<string>("");

    useInput((data, key) => {
        // 单次判断单个字符
        if (key.escape) {
            process.exit(0)
        }
        if (data === "r") {
            setCount(0)
        } else {
            setCount(count + 1)
        }
    })

    const handleSubmit = (value: string) => {
        if (value === "/exit") {
            setExiting(true)
            setTimeout(() => process.exit(0), 100)  // 给 Ink 一点时间渲染
            return
        }
        setMessages([...messages, {content:value, role: "user"}])
        setStreamingMsg(value)
        setInput("")
    }

    const handleStreamDone = (fullText: string)=> {
        setMessages([...messages, {content:fullText, role:"assistant"}])
        setStreamingMsg(null)
    }

    return (
        <Box flexDirection="column" padding={1}>
            <Text bold color="green">
                Nanoclaw cli
            </Text>
            <Text dimColor>
                Type /exit to quit
            </Text>
            <Text dimColor>
                Messages: {messages.length}
            </Text>
            {messages.map((msg, i) => (
                <MessageBubble key={i} content={msg.content} role={msg.role} />
            ))}
            <Text dimColor>
                count: {count}
            </Text>

            {/*当前有消息正在流式输出时，显示 StreamingChat组件；流结束后 streamingMsg 变 null，组件自动消失。*/}
            {streamingMsg && (
                <StreamingChat baseUrl={config.baseUrl} message={streamingMsg} onDone={handleStreamDone}/>
            )}

            <Box>
                <Text>&gt;</Text>
                <TextInput
                    value={input}
                    onChange={setInput}
                    onSubmit={handleSubmit}
                />
            </Box>
            <StatusBar baseUrl={config.baseUrl}/>
            {exiting && <Text dimColor>Goodbye!</Text>}
        </Box>
    )
}

process.on("uncaughtException", (err) => {
  process.stderr.write("\n[ERROR] " + err.message + "\n")
  if (err.stack) {
      const lines = err.stack.split("\n")
      // 只取前几行 + 自己代码的行
      for (const line of lines.slice(0, 8)) {
          if (line.includes("/src/")) process.stderr.write(line + "\n")
      }
  }
  process.exit(1)
})

render(<App/>)