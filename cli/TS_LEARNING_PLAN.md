# TypeScript 学习计划 — Nanoclaw CLI

> 目标：从零开始，通过编写 Nanoclaw CLI 逐步掌握 TypeScript。
> 每个阶段都对应项目中的实际代码，学完即用。

---

## 阶段概览

| 阶段 | 名称 | TS 知识点 | 产出文件 | 状态 |
|------|------|-----------|----------|------|
| 0 | 环境准备 | 无（配置好就行） | 完成 | ✅ |
| 1 | Hello, TS | 基础语法、const/let、fetch、try/catch、模板字符串 | `hello.ts` | ✅ |
| 2 | 类型系统入门 | interface、type注解、模块import/export、Promise<T> | `types.ts` + `client.ts` | ✅ |
| 3 | CLI 循环 | readline、while循环、数组操作（push/map/filter）、条件判断 | `index.ts` | ✅ |
| 4 | SSE 流式 | ReadableStream、TextDecoder、SSE协议解析、process.stdout.write、CRLF处理 | client升级 + index升级 | ✅ 基础 |
| — | 补充：async/await | Promise机制、async函数、await等待、与同步/goroutine对比 | — | ✅ |
| 5 | 配置管理 | 文件读写、CLI 参数 | config 命令 | ⏳ |
| 6 | Ink TUI | React/JSX、组件、hooks | 完整 TUI | 📅 |

---

## 阶段 0：环境准备（已完成）

```bash
# 项目结构
nanoclaw/cli/
├── package.json      # 项目配置和依赖
├── tsconfig.json     # TypeScript 编译器配置
└── src/              # 源码目录
```

`tsconfig.json` 的作用：告诉 TypeScript 用什么规则检查你的代码。
- `"target": "ES2022"` — 输出 ES2022 标准的 JS
- `"module": "ESNext"` — 使用最新的模块系统
- `"strict": true` — 开启最严格的类型检查（推荐）
- `"include": ["src"]` — 只检查 src 目录

运行方式：`npx tsx src/文件名.ts`

---

## 阶段 1：Hello, TS — 基础语法

### 需要掌握的概念

**1. 变量声明**
```typescript
const name = "hello"    // 常量，不可重新赋值
let count = 0           // 变量，可修改
// 优先用 const，除非你知道值会变
```

**2. 基本类型**
```typescript
const str: string = "text"      // 字符串
const num: number = 42           // 数字
const bool: boolean = true       // 布尔值
const arr: string[] = ["a", "b"] // 数组
const obj: Record<string, unknown> = { key: "value" }  // 对象
```

**3. async/await 和 Promise**
```typescript
// fetch() 返回一个 Promise，需要用 await 等待
const response = await fetch("http://...")
const data = await response.json()

// 错误处理
try {
  const res = await fetch(url)
  const data = await res.json()
} catch (error) {
  console.error("请求失败:", error)
}
```

**4. 模板字符串**
```typescript
const name = "world"
console.log(`Hello, ${name}!`)   // 反引号，${} 嵌入表达式
```

**5. import/export（模块系统）**
```typescript
// a.ts
export const foo = "bar"
export function hello() { return "hi" }

// b.ts
import { foo, hello } from "./a.js"  // 注意是 .js 不是 .ts！
```

> Node.js 使用 ESM（ES Modules），`import/export` 是现代标准。`tsx` 会自动处理 `.ts` 到 `.js` 的映射。

### 练习：hello.ts

写一个程序：
1. 用 `fetch` 调用 `GET http://127.0.0.1:8420/health`
2. 用 `console.log` 打印返回的 JSON
3. 用 `try/catch` 处理服务器没启动的情况
4. 用模板字符串格式化输出，例如：`Server status: ok (v0.1.0)`

### 自检清单

- [ ] 知道 `const` 和 `let` 的区别
- [ ] 能写出带类型注解的变量
- [ ] 理解 `async function` 和 `await`
- [ ] 知道 `try/catch` 怎么写
- [ ] 会用模板字符串 `${}`
- [ ] 知道 `import` 和 `export`

---

## 阶段 2：类型系统入门

### 需要掌握的概念

**1. interface — 定义数据结构**
```typescript
interface User {
  name: string
  age: number
  email?: string    // ? 表示可选字段
}

const user: User = { name: "Alice", age: 30 }
```

**2. type — 另一种定义方式**
```typescript
// type 和 interface 大部分时候可以互换
type Point = { x: number; y: number }

// type 可以做联合类型（interface 不行）
type Status = "ok" | "error" | "loading"
type Nullable<T> = T | null
```

**3. 函数签名**
```typescript
// 普通函数
function add(a: number, b: number): number {
  return a + b
}

// 箭头函数
const multiply = (a: number, b: number): number => a * b

// 可选参数 + 默认值
function greet(name: string, prefix: string = "Hello"): string {
  return `${prefix}, ${name}!`
}
```

**4. 泛型（简单理解）**
```typescript
// <T> 是一个"类型参数"，调用时确定具体类型
function identity<T>(value: T): T {
  return value
}

const num = identity<number>(42)   // num 的类型是 number
const str = identity("hello")      // 类型推断：str 的类型是 string
```

### 练习：写 types.ts + client.ts

**types.ts** — 从 API.md 复制所有接口定义

**client.ts** — 一个模块，导出 API 函数：
```typescript
import type { HealthResponse } from "./types.js"

const BASE_URL = "http://127.0.0.1:8420"

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE_URL}/health`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
```

然后在 hello.ts 里改为 import 使用：
```typescript
import { checkHealth } from "./client.js"
```

### 自检清单

- [ ] 知道 `interface` 和 `type` 的区别
- [ ] 能写出带类型注解的函数
- [ ] 理解 `import type` 的作用（运行时被移除，只做类型检查）
- [ ] 知道 `Promise<T>` 表示什么
- [ ] 理解模块化：一个文件一个功能，通过 import/export 组合

---

## 阶段 3：CLI 循环

### 需要掌握的概念

**1. process 对象**
```typescript
// Node.js 提供的全局对象
process.stdin   // 标准输入（键盘）
process.stdout  // 标准输出（屏幕）
process.exit(0) // 退出程序
```

**2. readline — 读取用户输入**
```typescript
import * as readline from "node:readline/promises"
import { stdin, stdout } from "node:process"

const rl = readline.createInterface({ input: stdin, output: stdout })

const answer = await rl.question("请输入: ")
console.log(`你输入了: ${answer}`)

rl.close()  // 用完要关闭
```

**3. 数组操作**
```typescript
const arr = [1, 2, 3, 4, 5]

arr.push(6)           // 末尾添加
arr.map(x => x * 2)   // 映射：[2, 4, 6, 8, 10]
arr.filter(x => x > 2) // 过滤：[3, 4, 5]
arr.find(x => x === 3) // 查找：3（找不到返回 undefined）
arr.some(x => x > 4)   // 是否有满足条件的：true
```

**4. for 循环**
```typescript
for (const item of arr) {
  console.log(item)
}

for (let i = 0; i < arr.length; i++) {
  console.log(arr[i])
}
```

**5. 字符串拼接**
```typescript
// 多行字符串
const block = `
╔══════════════╗
║  Nanoclaw    ║
╚══════════════╝
`

// 重复字符
const line = "=".repeat(40)
```

### 练习：index.ts — 交互式聊天

用 readline 实现循环：
1. 启动时打印欢迎信息
2. 循环：显示 `> ` 提示符 → 读用户输入 → 调 API → 打印回复 → 继续
3. 输入 `/exit` 或空行时退出
4. 显示一个简单的文本分隔线区分每次对话
5. 发送 ChatRequest，显示 ChatResponse 中的 response 字段

```typescript
// 伪代码
import { createInterface } from "node:readline/promises"
import { sendMessage } from "./client.js"

const rl = createInterface(...)

while (true) {
  const input = await rl.question("> ")
  if (input === "/exit") break
  const result = await sendMessage({ message: input })
  console.log(result.response)
}
rl.close()
```

### 自检清单

- [x] 理解 readline 的工作原理
- [x] 会写 while 循环和条件判断
- [x] 知道数组的 push/map/filter 用法并实际应用（/history 功能）
- [x] 会拼接多行文本输出
- [x] 能组合阶段 1-3 的知识点写出完整程序（交互式聊天 CLI）

---

## 阶段 4：SSE 流式处理

### 需要掌握的概念

**1. ReadableStream**
```typescript
const response = await fetch("http://.../chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "hello" }),
})

const reader = response.body!.getReader()
const decoder = new TextDecoder()

while (true) {
  const { done, value } = await reader.read()
  if (done) break
  const text = decoder.decode(value, { stream: true })
  // 处理 text ...
}
```

**2. 逐行解析**
```typescript
// SSE 格式：
// event: message_chunk
// data: {"content":"Hel"}
// （空行表示一个事件结束）

const lines = text.split("\n")
for (const line of lines) {
  if (line.startsWith("event: ")) {
    currentEvent = line.slice(7)
  } else if (line.startsWith("data: ")) {
    const data = JSON.parse(line.slice(6))
    // 根据 currentEvent 处理 data
  }
}
```

**3. 终端控制（\r 回车符）**
```typescript
// \r 回到行首，覆盖当前行——实现"原地更新"
process.stdout.write("Loading")      // 打印
process.stdout.write("\rLoading.")   // 回到行首 + 新内容 = 覆盖
process.stdout.write("\rLoading..")  // 再次覆盖
```

### 练习

在 client.ts 中添加 `sendMessageStream()` 函数，返回一个异步迭代器或回调：
- 逐步解析 SSE 事件
- 对 `message_chunk` 事件：逐字追加到当前行显示
- 对 `tool_call` 事件：显示工具的调用信息（如 `[🔍 Searching web...]`）
- 对 `done` 事件：结束

### 自检清单

- [x] 理解 Stream API 的基本用法（ReadableStream, getReader）
- [x] 能手动解析 SSE 协议（event:/data: 行解析）
- [x] 知道 `\r` 回车符的作用 + CRLF vs LF 问题
- [x] 能区分不同事件类型并分别处理（message_chunk / tool_call / done）

---

## 补充：async/await 与 Promise 深入理解

> 在阶段 3-4 的实践中深入讨论，单独记录以便回顾。

### 核心概念

**Promise** — "将来才完成的操作"的容器，类似 Go 的 `chan`：
- `Promise<string>` = 承诺将来给你一个 string
- `Promise<Response>` = 承诺将来给你一个 Response
- 三种状态：pending（等待中）→ fulfilled（成功）/ rejected（失败）

**async** — 标记函数返回 Promise，并允许内部使用 await：
- `async function` 的返回值自动被 Promise 包裹
- 没有 async，函数内部不能写 await

**await** — 等待 Promise 完成，把值取出来：
- `await Promise<string>` → string
- 类似 Go 的 `<-ch`（从 channel 读数据）

### 类比对照

| TypeScript | Go |
|---|---|
| `Promise<T>` | `chan T`（有缓冲 channel） |
| `await promise` | `<-ch` |
| `Promise.all([p1, p2])` | `sync.WaitGroup` |
| `async function` | `go func()` + channel 返回 |

### 关键理解

1. **单个 await 和同步写法效果一样** — 代码会停在那等结果。区别在于 async/await 不会阻塞整个进程（事件循环还能处理其他任务）。

2. **可以分开写以实现并发**：
   ```typescript
   const p1 = fetch("/api/a")  // 发起请求，不等待
   const p2 = fetch("/api/b")  // 发起请求，不等待
   const r1 = await p1         // 等 a 完成
   const r2 = await p2         // b 可能已经完成了
   ```
   两个请求并行执行，总耗时 = max(a, b) 而不是 a + b。

3. **async 是惰性的** — 函数被调用时开始执行，await 只是等结果。不调用就不会执行。

### TypeScript 中的 Promise 类型

```typescript
// 返回 Promise<string> — 异步函数
async function fetchData(): Promise<string> {
  const res = await fetch("http://...")
  const data = await res.json()
  return data.toString()
}

// 调用方
const result = await fetchData()  // result 是 string
const promise = fetchData()       // promise 是 Promise<string>
```

---

## 阶段 5：配置文件管理

### 需要掌握的概念

**1. fs 模块**
```typescript
import * as fs from "node:fs/promises"

// 读文件
const content = await fs.readFile("path/to/file", "utf-8")

// 写文件
await fs.writeFile("path/to/file", content, "utf-8")

// 检查文件是否存在
try {
  await fs.access("path/to/file")
  // 文件存在
} catch {
  // 文件不存在
}
```

**2. 确定用户目录**
```typescript
import { homedir } from "node:os"
import { join } from "node:path"

const configDir = join(homedir(), ".nanoclaw")
// => /Users/yourname/.nanoclaw 或 /home/yourname/.nanoclaw
```

**3. CLI 参数解析**
```typescript
const args = process.argv.slice(2)
// process.argv = ["node", "script.js", "chat", "--verbose"]
// args = ["chat", "--verbose"]
```

### 练习

1. 读取 `~/.nanoclaw/config.json`（如果存在）
2. 支持配置项：`baseUrl`, `defaultModel`
3. CLI 参数：`nanoclaw chat`（进入聊天）、`nanoclaw config --show`（显示配置）

---

## 阶段 6：Ink TUI

> 前提：已完成阶段 5（config.ts, CLI 参数解析）
>
> 前置安装：`npm install ink react @types/react ink-text-input`

---

### 概念 1：JSX 语法（已学）

JSX 让 TypeScript 里写 `<标签>`，编译器将其转为普通函数调用：

```tsx
// 你写的
<Text bold color="green">Hello</Text>

// 编译后（简写，不需要你关心）
jsx(Text, { bold: true, color: "green", children: "Hello" })
```

**核心规则：**
- 组件名首字母大写：`function App()` ✅，`function app()` ❌（会被当成 HTML 标签）
- 返回单一根元素，多行用 `<>...</>` 包裹
- `{}` 插 JS 表达式，`{arr.map(...)}` 渲染列表

---

### 概念 2：useState（已学）

```tsx
const [messages, setMessages] = useState<string[]>([])
//      ^当前值     ^更新函数           ^泛型约束类型  ^初始值
```

`useState` 返回一个 `[当前值, 更新函数]` 的数组，用解构赋值命名。**更新函数调用时，React 自动重新渲染 UI。**

**必须遵守的规则：**
- `useState` 必须在组件函数**内部**调用，不能在顶层
- 不能直接修改当前值：`messages.push(x)` ❌，必须 `setMessages([...messages, x])`
- Hooks 的调用顺序不能变：不要在 `if` 或循环里调 `useState`

---

### 概念 3：useInput（已学）

Ink 提供的 hook，捕获每一次按键：

```tsx
import { useInput } from "ink"

useInput((input, key) => {
    // input:  按下的字符（"a", "b", 功能键为空串）
    // key:    对象 { escape, return, up, down, ctrl, tab } 全是 boolean
    if (input === "q") process.exit(0)
    if (key.escape) process.exit(0)
})
```

---

### 概念 4：TextInput（输入框）

Ink 提供的输入框组件，封装了退格、光标、IME 输入法等复杂逻辑：

```tsx
import TextInput from "ink-text-input"

function App() {
    const [input, setInput] = useState("")

    const handleSubmit = (value: string) => {
        console.log("用户按了回车，内容：", value)
    }

    return (
        <Box>
            <Text>&gt; </Text>
            <TextInput
                value={input}
                onChange={setInput}
                onSubmit={handleSubmit}
            />
        </Box>
    )
}
```

**三个 props：**

| prop | 类型 | 作用 |
|------|------|------|
| `value` | `string` | 当前输入内容（state） |
| `onChange` | `(v: string) => void` | 每次按键后触发，传回完整的当前输入字符串 |
| `onSubmit` | `(v: string) => void` | 按回车触发，传回最终输入内容 |

**为什么不用 `useInput` 拼字符串？**
`useInput` 一次只给一个字符，你要自己拼字符串、处理退格、处理粘贴。`TextInput` 帮你全做了。

**练习 4.1：** 在 `app.tsx` 中加入输入框，`onSubmit` 时把内容 `setMessages`，并清空输入框。（`onChange` 还是绑 `setInput`，`onSubmit` 里调 `setMessages` 后再 `setInput("")`）

---

### 概念 5：Props — 组件之间传数据

组件就像函数：**props 就是参数**。

```tsx
// 定义：MessageBubble 接收一个 text prop
function MessageBubble({ text, role }: { text: string; role: string }) {
    return (
        <Box>
            <Text bold color={role === "user" ? "blue" : "yellow"}>
                {role === "user" ? "You" : "AI"}
            </Text>
            <Text>: {text}</Text>
        </Box>
    )
}

// 使用：给组件传 props
<MessageBubble text="Hello" role="user" />
<MessageBubble text="Hi there" role="assistant" />
```

**规则：**
- 组件函数的第一个参数就是 props 对象
- 用解构直接取字段：`function Foo({ name, age }: { name: string; age: number })`
- 子组件通过 props 接收数据，**不能修改 props**（只读的）
- 子组件也可以有自己的 `useState`（内部状态）

**练习 5.1：** 把 `app.tsx` 中渲染消息的 JSX 抽成 `<MessageBubble>` 组件：
- props 是 `{ content: string; role: "user" | "assistant" }`
- 用户消息颜色蓝色，AI 消息黄色
- 在 App 里 `{messages.map(msg => <MessageBubble content={msg} role="user" />)}`

---

### 概念 6：组件拆分

一个文件里可以写多个组件。把 UI 拆成小块，每个文件一个组件是推荐做法。

**当前 `app.tsx` 的结构（一个文件一个组件）：**

```
app.tsx
  └─ App (管理所有状态)
```

**目标结构（多个文件多个组件）：**

```
app.tsx
  └─ App (管理消息列表、输入状态)
       ├─ ChatMessages (接收 messages[]，渲染列表)
       │    └─ MessageBubble (接收单条消息，渲染单行)
       ├─ TextInput (Ink 自带)
       └─ ToolIndicator (接收 tool calls，渲染状态) [后续]
```

拆分的好处：
- 每个组件只做一件事
- 状态在 App 里统一管理，子组件只管展示（"状态提升"）
- 单独文件好维护

**规则：**
- 子组件需要什么数据，父组件通过 props 传
- 子组件内部可以有自己的 `useState`（UI 相关的局部状态）
- 子组件通过 props 里的**回调函数**通知父组件（如 `onSend`）

**练习 6.1：** 创建 `src/components/ChatMessages.tsx`，把渲染消息列表的逻辑移进去：
- props: `{ messages: { role: string; content: string }[] }`
- 内部用 `messages.map(...)` 渲染每条消息
- 在 `app.tsx` 里：`<ChatMessages messages={messages} />`

---

### 概念 7：useEffect — 副作用

组件需要加载数据、启动定时器、或跟外部通信时用的 hook：

```tsx
import { useEffect } from "react"

function App() {
    const [ready, setReady] = useState(false)

    useEffect(() => {
        // 这里会在组件"挂载"后执行一次
        // 适合做：加载配置、请求数据、启动定时器
        setReady(true)
    }, [])  // [] = "只执行一次"（依赖数组）
}
```

**依赖数组 `[]` 的含义：**

| 写法 | 什么时候执行 |
|------|-------------|
| `useEffect(fn, [])` | 组件挂载时执行一次 |
| `useEffect(fn, [count])` | 组件挂载时 + `count` 变化时 |
| `useEffect(fn)` | 每次渲染都执行（谨慎用） |

**为什么需要它？** 你不能在组件函数里直接 `await fetch()`——函数组件是同步执行的，`useEffect` 是 React 给你的"事后处理"通道。

**练习 7.1：** 在 `App` 组件挂载时，用 `useEffect` + `loadConfig()` 加载配置，然后 `console.log` 打印 baseUrl。（不涉及 UI，只是为了演示 useEffect 的用法）

**练习 7.2（可选）：** 在 `useEffect` 里调 `checkHealth()`，把后端健康状态显示在标题行旁边。

---

### 概念 8：StreamingText — 异步更新

Streaming（流式输出）在渲染模型中需要特殊处理：AI 回复是逐字到达的，需要在收到每个 chunk 时更新 UI。

**模式：** 外层状态 + useEffect 启动异步流程，收到数据后 `setXxx` 触发重渲染：

```tsx
function StreamingText({ baseUrl, message }: { baseUrl: string; message: string }) {
    const [content, setContent] = useState("")

    useEffect(() => {
        let cancelled = false
        const stream = fetch(`${baseUrl}/chat/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        })
            .then(res => res.body!.getReader())
            .then(async (reader) => {
                const decoder = new TextDecoder()
                while (true) {
                    const { done, value } = await reader.read()
                    if (done || cancelled) break
                    const chunk = decoder.decode(value, { stream: true })
                    // 解析 SSE 事件，提取 message_chunk 的 content
                    setContent(prev => prev + chunk)  // 追加到已有内容
                }
            })
        return () => { cancelled = true }  // 组件卸载时停止
    }, [message])  // message 变化时重新开始

    return <Text>{content}</Text>
}
```

**关键点：**
- `useEffect` 里写 async 操作（先 `.then()` 或用 IIFE）
- cleanup 函数（`return () => {}`）在组件卸载或依赖变化时执行，防止内存泄漏
- 用 `setContent(prev => prev + chunk)` 而不是 `setContent(content + chunk)`，因为 chunk 到达时 `content` 可能已经过期

**练习 8.1：** 创建 `src/components/StreamingChat.tsx`，用户按回车时启动流式请求，逐字显示 AI 回复：
- props: `{ baseUrl: string; message: string; onDone: (fullText: string) => void }`
- 内部用 `useState` 管理当前已收到的内容
- `onDone` 在流结束后把完整回复传回给父组件

---

### 概念 9：Ink 布局组件

Ink 的几个常用布局组件：

```tsx
import { Box, Text, Spacer, Newline } from "ink"

<Box flexDirection="column">    // 垂直排列（默认是 row 水平）
<Box width={20}>                // 固定宽度
<Box flexGrow={1}>              // 占满剩余空间
<Spacer />                      // 推挤布局：把后面的元素推到右边
<Newline />                     // 空行（等价于 <Text>{"\n"}</Text>）

// TextStyle props
<Text bold>粗体</Text>
<Text italic>斜体</Text>
<Text underline>下划线</Text>
<Text strikethrough>删除线</Text>
<Text dimColor>弱化/灰色</Text>
<Text color="green">颜色</Text>
<Text backgroundColor="black">背景色</Text>
<Text color="#ff8800">hex 颜色</Text>
```

**练习 9.1（可选）：** 在对话界面加一个底部分隔线和状态栏，显示当前后端连接状态：

```
╔══════════════════════════╗
║      Nanoclaw CLI       ║
╚══════════════════════════╝
[You] hello
[AI] hi there
────────────────────────────
Connected | Model: gpt-4o-mini
[> ]_
```

（Box 用 `borderStyle` props 实现边框，但目前版本可能不支持，保持简洁即可）

---

### 综合练习：改造完整对话界面

把 `app.tsx` 改造成一个完整的对话 TUI，包含：

**目标交互：**
1. 启动后显示标题
2. 底部显示输入框，按回车发送消息
3. 消息显示在输入框上方（最新的在最下面）
4. AI 回复支持 Streaming（逐字显示）
5. Esc 退出

**推荐结构：**

```
src/
├── components/
│   ├── MessageBubble.tsx    # 单条消息（role + content）
│   └── StreamingChat.tsx    # 流式对话（启动 stream + 逐字显示）
├── app.tsx                  # App 主组件（管理状态 + 输入框）
├── config.ts                # 配置文件（已有）
├── client.ts                # API 调用（已有）
└── types.ts                 # 类型定义（已有）
```

**数据流：**

```
                     App (状态管理中心)
                  /          |          \
         messages[]      input""      request in flight
             |                          |
    <MessageBubble />           <StreamingChat />
    (只展示，不修改)            (只管流式显示，结束时通知 App)
```

**注意：**
- 流式请求不能在 `App` 组件里直接用 `async` 调（组件必须是同步的）
- 用 `useEffect` 启动流，或者把流逻辑封装在组件内部
- Streaming 时输入框应该保持可用（不阻塞 UI）

---

### 练习参考代码

以下是对应每个练习的参考实现，**先自己写，写完再对照**。

**练习 4.1 参考 — TextInput 接入 App：**

```tsx
function App() {
    const [messages, setMessages] = useState<string[]>([])
    const [input, setInput] = useState("")

    const handleSubmit = (value: string) => {
        setMessages([...messages, value])
        setInput("")
    }

    return (
        <Box flexDirection="column" padding={1}>
            {/* 消息列表 */}
            {messages.map((msg, i) => (
                <Text key={i}>{msg}</Text>
            ))}
            {/* 输入框 */}
            <Box>
                <Text>&gt; </Text>
                <TextInput value={input} onChange={setInput} onSubmit={handleSubmit} />
            </Box>
        </Box>
    )
}
```

**练习 5.1 参考 — MessageBubble 组件（放在 app.tsx 末尾或单独文件）：**

```tsx
function MessageBubble({ content, role }: { content: string; role: "user" | "assistant" }) {
    const label = role === "user" ? "You" : "AI"
    const color = role === "user" ? "blue" : "yellow"
    return (
        <Box>
            <Text bold color={color}>{label}</Text>
            <Text>: {content}</Text>
        </Box>
    )
}

// 在 App 的 JSX 中使用：
{messages.map((msg, i) => (
    <MessageBubble key={i} content={msg} role={msg.role} />
))}
```

注意：这里的 `msg` 不再是 `string`，需要把 `messages` 的类型从 `string[]` 改为 `{ role: "user" | "assistant"; content: string }[]`。

**练习 6.1 参考 — 创建 `src/components/ChatMessages.tsx`：**

```tsx
import { Text, Box } from "ink"

interface ChatMessage {
    role: "user" | "assistant"
    content: string
}

export function ChatMessages({ messages }: { messages: ChatMessage[] }) {
    return (
        <Box flexDirection="column">
            {messages.map((msg, i) => (
                <Box key={i}>
                    <Text bold color={msg.role === "user" ? "blue" : "yellow"}>
                        {msg.role === "user" ? "You" : "AI"}
                    </Text>
                    <Text>: {msg.content}</Text>
                </Box>
            ))}
        </Box>
    )
}
```

在 app.tsx 中：`import { ChatMessages } from "./components/ChatMessages.js"`

**练习 7.1 参考 — useEffect 加载配置：**

```tsx
import { useEffect } from "react"
import { loadConfig } from "./config.js"

function App() {
    const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8420")

    useEffect(() => {
        loadConfig().then(config => {
            console.log("Loaded config:", config.baseUrl)
            setBaseUrl(config.baseUrl)
        })
    }, [])  // [] = 只执行一次

    // ...
}
```

注意：`useEffect` 的回调不能是 `async`，所以用 `.then()` 或内部写 `async function`。

**练习 7.2 参考 — 显示健康状态：**

```tsx
function App() {
    const [health, setHealth] = useState<string>("checking...")

    useEffect(() => {
        checkHealth()
            .then(res => setHealth(`${res.status} (v${res.version})`))
            .catch(() => setHealth("offline"))
    }, [])

    return (
        <Box>
            <Text bold color="green">Nanoclaw CLI</Text>
            <Text dimColor> — {health}</Text>
        </Box>
    )
}
```

**练习 8.1 参考 — StreamingChat 组件（`src/components/StreamingChat.tsx`）：**

```tsx
import { useEffect, useState } from "react"
import { Text } from "ink"

interface Props {
    baseUrl: string
    message: string        // 用户本次发送的消息
    onDone: (text: string) => void
}

export function StreamingChat({ baseUrl, message, onDone }: Props) {
    const [content, setContent] = useState("")

    useEffect(() => {
        if (!message) return

        let cancelled = false

        const run = async () => {
            const res = await fetch(`${baseUrl}/chat/stream`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message }),
            })
            const reader = res.body!.getReader()
            const decoder = new TextDecoder()
            let currentEvent = ""
            let fullText = ""

            while (true) {
                const { done, value } = await reader.read()
                if (done || cancelled) break

                const text = decoder.decode(value, { stream: true })
                const lines = text.split(/\r?\n/)

                for (const line of lines) {
                    if (!line) continue
                    if (line.startsWith("event: ")) {
                        currentEvent = line.slice(7)
                    } else if (line.startsWith("data: ")) {
                        const data = JSON.parse(line.slice(6))
                        if (currentEvent === "message_chunk") {
                            fullText += data.content
                            setContent(fullText)
                        }
                    }
                }
            }

            if (!cancelled) onDone(fullText)
        }

        run()
        return () => { cancelled = true }
    }, [message])  // message 变化时重新开始

    return <Text>{content}</Text>
}
```

在 App 中使用：`{streamingMsg && <StreamingChat baseUrl={baseUrl} message={streamingMsg} onDone={handleStreamDone} />}`

需要维护一个 `streamingMsg` 状态（`string | null`），用户发送时设为消息内容，流结束后设为 `null`，同时把完整回复追加到 `messages`。

---

### 自检清单

阶段 6 完成后，你应该能回答这些问题：

- [ ] JSX 怎么嵌入 JS 表达式？`{}` 里能写什么、不能写什么？
- [ ] `useState` 返回什么？为什么不能直接修改 state 变量？
- [ ] 怎么把数据从父组件传给子组件？props 是什么？
- [ ] 为什么需要拆分组件？一个文件写一个组件的好处？
- [ ] `useEffect` 的 `[]` 参数有什么用？不传会怎样？
- [ ] 流式输出在 React 中用什么模式实现？
- [ ] `TextInput` 的三个 props 分别什么时候触发？
- [ ] Ink 的 `Box` 布局默认是水平还是垂直？怎么变？
- [ ] 组件卸载时怎么清理资源（定时器、流）？

---

## 学习建议

### 最简路径（推荐）

只做阶段 1-4 就能得到一个功能完整的 CLI：

```
hello.ts  →  types.ts + client.ts  →  index.ts  →  + streaming
  阶段1          阶段2                  阶段3          阶段4
```

阶段 5-6 是"锦上添花"，学有余力再搞。

### 遇到问题时

1. **语法不会** → 问 AI，直接说"TS 里 interface 和 type 有什么区别"
2. **类型报错** → 读错误信息，TypeScript 的错误信息非常详细
3. **运行时出错** → 加 `console.log` 看变量值，理解数据流
4. **不确定怎么组织代码** → 先写一个文件里，跑通了再拆分

### 资源

- [TypeScript Handbook](https://www.typescriptlang.org/docs/handbook/intro.html) — 官方文档
- [TypeScript Playground](https://www.typescriptlang.org/play/) — 在线试代码
- 多看 `node_modules/@types/node/index.d.ts` — Node.js 的 TS 类型定义，是最好的参考
