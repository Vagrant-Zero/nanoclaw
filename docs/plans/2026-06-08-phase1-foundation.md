# Phase 1: 基础 (Foundation) 实施计划

> **目标**：构建 Phase 1 的完整基线：数据模型 + 存储抽象 + 基础 ReAct LangGraph + SSE 流式接入 + TUI 展示。

**架构**：Phase 1 只走简单路径（Router → ReAct Node → output），不涉及多任务分解。所有组件设计为后续 Phase 可扩展。

**技术栈**：Python 3.12+，FastAPI，LangGraph，langchain-openai（DeepSeek 兼容 API），Pydantic，uv

**前置阅读**：`docs/plans/2026-06-08-agent-architecture-design.md` 的数据模型、存储抽象、ReAct 图、SSE 协议章节

---

### Task 1：数据模型 — models/ 模块

**文件**：
- 创建：`backend/src/nanoclaw/models/__init__.py`
- 创建：`backend/src/nanoclaw/models/chat.py` — ChatMessage, Step
- 创建：`backend/src/nanoclaw/models/task.py` — TaskStatus (enum), Subtask, TaskPlan, EffectLogEntry, CheckpointState

**原因**：所有模块都依赖这些模型，必须先定义。注意：Phase 1 还不使用 Subtask/TaskPlan/EffectLogEntry（这些在 Phase 2 才活跃），但需要定义好以便 import 不报错。

**第 1 步： 定义 model/chat.py**

- `ChatMessage`：现有结构 `{content: str, role: str}`，但需要扩展 metadata 字段供后续使用
- `Step`：ReAct 每一步的记录，详见设计文档

为什么：ChatMessage 是前后端通信的最小单元，Step 是 ReAct 循环的日志。

**Step 2: 定义 models/task.py**

- `TaskStatus(str, Enum)`：PENDING / RUNNING / SUCCEEDED / FAILED / RETRYING / CANCELLED / COMPENSATING / COMPENSATED / COMPENSATION_FAILED
- `Subtask`：id, description, status, depends_on, tools_needed, trace, compensation, max_retries, result, error
- `TaskPlan`：session_id, subtasks: list[Subtask]
- `EffectLogEntry`：subtask_id, action, resource, metadata, version, timestamp
- `CheckpointState`：graph_state, queue_snapshot, node_name, timestamp

为什么：TaskStatus 包含所有生命周期状态（包括 COMPENSATION_FAILED）。EffectLog 和 CheckpointState 虽然 Phase 2+ 才用到，但先定义好类型让编译通过。

**Step 3: 更新 models/__init__.py**

导出所有类型，方便 `from nanoclaw.models import ...`

**Step 4: 验证**

```bash
cd backend && uv run python -c "from nanoclaw.models.chat import ChatMessage, Step; from nanoclaw.models.task import TaskStatus, Subtask, EffectLogEntry; print('Models OK')"
```

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/models/
git commit -m "feat: define data models for Phase 1"
```

---

### Task 2：存储抽象 — storage/ 模块

**文件**：
- 创建：`backend/src/nanoclaw/storage/__init__.py`
- 创建：`backend/src/nanoclaw/storage/session_repo.py` — SessionRepository(ABC) + MemorySessionRepo
- 创建：`backend/src/nanoclaw/storage/task_repo.py` — TaskRepository(ABC) + MemoryTaskRepo
- 创建：`backend/src/nanoclaw/storage/task_queue.py` — TaskQueue(ABC) + MemoryQueue

**原因**：Phase 1 只实现 Memory 版本（进程内 dict），但这些接口必须在 Phase 1 定义好，后续 Phase 加 PG 实现时不需要改调用方代码。这是"Mock-and-Swap"策略。

**第 1 步： 实现 SessionRepository**

接口方法（详见设计文档）：
- `create(session) -> Session`
- `get(session_id) -> Session | None`
- `append_message(session_id, msg)`
- `get_history(session_id) -> list[ChatMessage]`

MemorySessionRepo：内部用 `dict[str, Session]` 存储。append_message 修改 Session.messages 列表并写回 dict。

为什么：Session 管理对话历史，ReAct 循环需要历史消息做上下文。

**Step 2: 实现 TaskRepository**

接口方法：
- `save_plan(session_id, plan)`
- `get_plan(session_id) -> TaskPlan | None`
- `update_subtask(session_id, subtask)`

MemoryTaskRepo：内部用 `dict[str, TaskPlan]` 存储。

为什么：Phase 2 才用，但接口需要先定义。Phase 1 可留 stub 实现。

**Step 3: 实现 TaskQueue（含 DAG 感知）**

接口方法（详见设计文档）：
- `init_plan(plan)` — 解析 DAG，叶子节点直接入 ready 队列
- `dequeue() -> Subtask | None` — 只返回依赖满足的 subtask
- `complete(task_id, result)` — 触发下游入队
- `fail(task_id, error)` — 标记下游 CANCELLED
- `wait_for_all() -> dict` — asyncio.Event 等待全部完成
- `snapshot() -> dict` / `restore(snapshot)`

Key design: `complete()` 内部自动扫描下游依赖，依赖全部满足的自动入 ready 队列，不需要外部循环 dispatch。

为什么：DAG 感知是 H1 问题的修复。闭包在队列内部，避免 dispatch 只执行一次的缺陷。

**Step 4: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.storage.task_queue import MemoryQueue
from nanoclaw.models.task import TaskStatus
print('Storage OK')
"
```

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/storage/
git commit -m "feat: add storage abstractions and Memory implementations"
```

---

### Task 3: 扩展 Agent State — agent/state.py

**文件**：
- 修改：`backend/src/nanoclaw/agent/state.py`

**原因**：当前的 AgentState 只有 `messages` 字段。Phase 1 的 ReAct 图需要更多的状态字段。

**第 1 步： 扩展 AgentState**

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[AnyMessage], add_messages]
    session_id: str | None                # 当前会话 ID
    task_id: str | None                   # "root"（简单路径）或 "task_xxx"
    session_repo: SessionRepository | None # 会话存储引用
```

注意：tools 不在 AgentState 中传递，而是在 `create_react_agent()` 编译时注入。tools 参数在整个 session 生命周期内不变，确保 KV cache 前缀稳定。

为什么：session_repo 记录消息历史，task_id 拼 SSE 事件。tools 通过闭包访问，不在 State 中。

**Step 2: 验证编译**

```bash
cd backend && uv run python -c "from nanoclaw.agent.state import AgentState; print('State OK')"
```

**Step 3: 提交**

```bash
git add backend/src/nanoclaw/agent/state.py
git commit -m "feat: extend AgentState with session fields"
```

---

### Task 4: 基础 ReAct LangGraph — agent/nodes/react_agent.py

**文件**：
- 创建：`backend/src/nanoclaw/agent/nodes/__init__.py`
- 创建：`backend/src/nanoclaw/agent/nodes/react_agent.py`

**原因**：ReAct 是核心循环，Phase 1 只走简单路径（不经过 planner/dispatch/await/collect）。同时也是 Phase 2 Worker 的内部实现——同一个图。

**第 1 步： 理解 ReAct 结构**

ReAct 是一个 LangGraph `StateGraph`，内部组成：

```
输入消息 → [call_model] → 有 tool_calls? → 是 → [call_tool] → 循环回去
                                            → 否 → 输出 → Done
```

- `call_model` 节点：调 LLM.ainvoke()，传入当前消息历史 + 系统 prompt + 工具列表
- `call_tool` 节点：解析 LLM 返回的 tool_call，从 registry 找工具执行，结果追加到 messages
- 条件边：判断 LLM 输出是否有 tool_calls，有就循环，没有就结束

**Step 2: 实现 call_model 节点**

核心逻辑：
1. 从 AgentState 取 messages（历史）
2. 从 ToolRegistry 取工具列表（给 LLM 的 tools 参数）
3. 调 `llm.ainvoke(messages, tools=tools)`
4. 返回结果追加到 messages

注意事项：
- 必须用 `.ainvoke()` 不是 `.invoke()`（H2 问题修复）
- LLM 实例在构建图时注入，不在 State 中传递
- ReAct 图不关心 Router/Planner 的存在——只管执行

实现方式：用 `StateGraph` + `add_node` + `add_conditional_edges`

参考 LangGraph 官方教程：
- https://langchain-ai.github.io/langgraph/tutorials/reage nt/

关键代码模式：

```python
def should_continue(state: AgentState) -> Literal["continue", "end"]:
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "continue"
    return "end"
```

**Step 3: 实现 call_tool 节点**

核心逻辑：
1. 从 AgentState 取最后一条消息的 tool_calls
2. 对每个 tool_call：从 registry 找工具 → 执行 → 返回 ToolMessage
3. 所有 tool_call 结果 + 每个步骤的 SSE 事件

SSE 事件推送时机：
- 调用工具前 → push `agent_action`
- 工具返回后 → push `agent_observation`

注意：Phase 1 的 SSE 事件通过参数传入的 callback 函数推送，不直接依赖 FastAPI 的 StreamingResponse。这样 ReAct 图可以独立测试。

**Step 4: 构建 ReAct 图**

```python
def create_react_agent(llm, tool_registry) -> CompiledStateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("call_model", call_model_node)
    builder.add_node("call_tool", call_tool_node)
    builder.set_entry_point("call_model")
    builder.add_conditional_edges("call_model", should_continue, {...})
    builder.add_edge("call_tool", "call_model")
    return builder.compile()
```

关键设计：这个 `create_react_agent()` 工厂函数会被两处复用——Phase 1 的简单路径 supervisor 图，和 Phase 2 的 Worker 内部。

**Step 5: 验证编译**

```bash
cd backend && uv run python -c "from nanoclaw.agent.nodes.react_agent import create_react_agent; print('ReAct OK')"
```

**Step 6: 提交**

```bash
git add backend/src/nanoclaw/agent/nodes/
git commit -m "feat: add base ReAct LangGraph with async LLM"
```

---

### Task 5: Supervisor 主图 — agent/supervisor_graph.py (简单路径)

**文件**：
- 创建：`backend/src/nanoclaw/agent/supervisor_graph.py`

**原因**：简单路径的用户入口。只包含 Router + ReAct Node，不涉及 planner/dispatch/collect。

**第 1 步： 实现 Router 节点（启发式优先）**

Router 判断输入是"简单"还是"复杂"（Phase 1 永远走简单路径，但接口保留）：

```python
def router_node(state: AgentState) -> Literal["react", "plan"]:
    content = state["messages"][-1].content
    # 启发式规则
    complex_keywords = ["分析", "报告", "比较", "计划", "explore", "analyze",
                        "investigate", "research", "survey", "总结", "规划"]
    if any(kw in content.lower() for kw in complex_keywords) and len(content) > 20:
        return "plan"
    return "react"
```

为什么：如果每次简单查询（如"你好"）都调 LLM 做 router 分类，浪费 token。先启发式，Phase 2 再加 LLM fallback。

**Step 2: 构建简单路径的 Supervisor 图**

```
输入 → [router] → "react" → [react_node] → (循环执行 ReAct) → 输出
```

```python
def create_supervisor(llm, tool_registry, session_repo) -> CompiledStateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("react", create_react_agent(llm, tool_registry))
    builder.set_entry_point("router")
    builder.add_conditional_edges("router", lambda s: "react", {"react": "react"})
    builder.add_edge("react", END)
    return builder.compile()
```

为什么：简单路径就是 Router → ReAct → 结束。未来 Phase 2 添加 planner/dispatch/collect/await 时，在这个图上加条件边。

**Step 3: 验证编译**

```bash
cd backend && uv run python -c "from nanoclaw.agent.supervisor_graph import create_supervisor; print('Supervisor OK')"
```

**Step 4: 提交**

```bash
git add backend/src/nanoclaw/agent/supervisor_graph.py
git commit -m "feat: add supervisor graph with heuristic router"
```

---

### Task 6: 改造 /chat/stream — 走 ReAct 图 + SSE 协议

**文件**：
- 修改：`backend/src/nanoclaw/server/app.py`
- 创建：`backend/src/nanoclaw/server/deps.py`

**原因**：当前 /chat/stream 是 mock SSE（人工构造字符串）。Phase 1 需要替换为真实的 LangGraph 执行 + SSE 协议。

**第 1 步： 理解新的 SSE 事件格式**

设计文档规定所有事件携带 task_id（简单路径用 "root"）：

```
event: agent_think
data: {"content": "...", "task_id": "root"}

event: agent_action
data: {"tool": "read_file", "args": {...}, "task_id": "root"}

event: agent_observation
data: {"tool": "read_file", "result": "...", "task_id": "root"}

event: message_chunk
data: {"content": "逐字...", "task_id": "root"}

event: done
data: {"session_id": "..."}
```

**Step 2: 实现 deps.py — FastAPI 依赖注入**

```python
# 全局单例，应用启动时初始化
def get_supervisor() -> CompiledStateGraph:
    ...
def get_session_repo() -> SessionRepository:
    ...
```

为什么：LangGraph 实例和存储层应该在应用启动时创建一次，而不是每个请求都重新构造。FastAPI 的 `Depends` 配合 `lru_cache` 实现单例。

**Step 3: 改造 /chat/stream 端点**

新流程：
1. 接收 ChatRequest（message, thread_id）
2. 创建或获取 Session
3. 把用户消息追加到历史
4. 在 async generator 中调用 `supervisor.ainvoke()`（可能需要在独立线程运行以避免阻塞 SSE）
5. 通过 SSE 事件回调或 generator 逐步 yield 事件

关键设计：LangGraph 的 `astream_events()` 可以逐事件输出。或者用 callback 方式在 ReAct 节点执行时 push SSE。

```python
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> EventSourceResponse:
    async def event_generator() -> AsyncGenerator[dict, None]:
        # 1. 创建 session
        session = await session_repo.create(Session(...))
        yield {"event": "task_status", "data": json.dumps({"task_id": "root", "status": "RUNNING"})}

        # 2. 调用 supervisor 图，传入 callback 来推送 SSE
        async for event in supervisor.astream_events(
            {"messages": history, ...},
            version="v2",
        ):
            # 把 LangGraph 事件映射为 SSE 事件
            if event["event"] == "on_chat_model_stream":
                yield {"event": "message_chunk", "data": json.dumps({
                    "content": event["data"]["chunk"].content,
                    "task_id": "root"
                })}

        yield {"event": "done", "data": json.dumps({"session_id": session.id})}

    return EventSourceResponse(event_generator())
```

为什么：`astream_events()` 是 LangGraph 的流式 API，提供 `on_chat_model_stream`、`on_tool_start`、`on_tool_end` 等事件，天然适合映射到我们的 SSE 协议。

**Step 4: 验证**

```bash
# 启动后端
make backend

# 另一个终端测试
curl -N http://localhost:8420/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

期望看到 SSE 事件流：agent_think → agent_action → agent_observation → message_chunk → done

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/server/app.py backend/src/nanoclaw/server/deps.py
git commit -m "feat: wire /chat/stream to ReAct graph with SSE protocol"
```

---

### Task 7: TUI 展示 ReAct 步骤 — 前端适配

**文件**：
- 修改：`cli/src/types.ts` — 添加 SSE 事件类型定义
- 修改：`cli/src/app.tsx` — 适配新的 SSE 事件流
- 创建：`cli/src/components/ThinkingBlock.tsx` — 显示思考文本
- 创建：`cli/src/components/ToolCallCard.tsx` — 显示工具调用 + 结果

**原因**：当前 TUI 只展示最终回答（message_chunk）。Phase 1 需要展示 ReAct 中间过程（think/action/observation），用户能看到 Agent 在思考什么、调用了什么工具、结果是什么。

**第 1 步： 扩展 types.ts**

添加 SSE 事件相关类型：

```typescript
export interface SSEEvent {
  event: "agent_think" | "agent_action" | "agent_observation" | "message_chunk" | "task_status" | "done"
  data: string  // JSON string
}

export interface AgentThinkData {
  content: string
  task_id: string
}

export interface AgentActionData {
  tool: string
  args: Record<string, unknown>
  task_id: string
}

export interface AgentObservationData {
  tool: string
  result: string
  task_id: string
}
```

**Step 2: 实现 ThinkingBlock 组件**

展示"Agent 正在思考..."：

```tsx
export function ThinkingBlock({ content }: { content: string }) {
  // 灰色/斜体文本，表示 LLM 的推理过程
  return (
    <Box flexDirection="column" marginLeft={2}>
      <Text dimColor italic>💭 {content}</Text>
    </Box>
  )
}
```

为什么：ThinkingBlock 让用户看到 Agent 的推理过程，理解 Agent 为什么做某个操作。

**Step 3: 实现 ToolCallCard 组件**

展示工具调用卡片：

```tsx
interface ToolCallCardProps {
  tool: string
  args: Record<string, unknown>
  result?: string
  isDone: boolean  // true = 有结果了, false = 正在执行
}

export function ToolCallCard({ tool, args, result, isDone }: ToolCallCardProps) {
  // 显示工具名 + 参数（用 Box 包裹，彩色边框）
  // 有结果时显示结果摘要
}
```

为什么：ToolCallCard 类似于 Claude Code 的工具调用展示——让用户看到"Agent 做了什么事、得到了什么结果"。

**Step 4: 改造 app.tsx 的事件处理**

当前的 StreamingChat 组件假设直接拿到完整响应。新的 event stream 包含多种事件类型：

```tsx
// 思路：useEffect 中 fetch /chat/stream，解析 SSE 事件
// 根据 event 类型做不同处理：
// - agent_think → 追加到 thinkingLogs 状态
// - agent_action → 追加到 toolCalls 状态
// - agent_observation → 更新最近的 toolCall 添加 result
// - message_chunk → 追加到 content（现有的 StreamingChat 逻辑）
// - done → 处理完成

// 渲染时：
{messages.map((msg, i) => (
  <MessageBubble key={i} content={msg.content} role={msg.role} />
))}
{thinkingLogs.map((think, i) => (
  <ThinkingBlock key={i} content={think.content} />
))}
{toolCalls.map((tc, i) => (
  <ToolCallCard key={i} tool={tc.tool} args={tc.args} result={tc.result} isDone={tc.isDone} />
))}
```

注意：这是一个比较大的改动。用户需要理解 SSE 解析流程——逐行读取 `data:` 前缀的内容，JSON.parse，然后根据 `event:` 派发。

**Step 5: 验证**

```bash
make app
```

输入消息，验证：
- 是否能看到 `ThinkingBlock`（灰色推理文本）
- 是否能看到 `ToolCallCard`（工具调用信息）
- 最终回答是否逐字输出
- 所有事件都带 `task_id`（目前只有 "root"）

**Step 6: 提交**

```bash
git add cli/src/types.ts cli/src/app.tsx cli/src/components/ThinkingBlock.tsx cli/src/components/ToolCallCard.tsx
git commit -m "feat: display ReAct steps in TUI with ThinkingBlock and ToolCallCard"
```

---

### Task 8: 集成测试 + 错误处理

**文件**：
- 创建：`backend/src/nanoclaw/server/app.py` 中添加错误处理

**第 1 步： 添加 SSE 错误事件**

当 ReAct 图执行异常时，发送 error 事件：

```
event: error
data: {"message": "工具执行超时", "task_id": "root"}
```

**Step 2: 添加超时保护**

创建 `llm.ainvoke()` 调用时的超时控制：

```python
async with asyncio.timeout(30):  # 30 秒
    response = await llm.ainvoke(...)
```

**Step 3: 端到端验证**

```bash
# 1. 启动后端
make backend

# 2. 启动前端
make app

# 3. 输入一条简单消息
# 期望：看到思考过程 → 工具调用 → 最终回答
```

**Step 4: 提交**

```bash
git commit -am "fix: add error handling and timeout to ReAct execution"
```

---

### Phase 1 完成检查清单

- [x] 数据模型全部定义（models/chat.py, models/task.py）
- [x] 存储抽象全部定义（session_repo.py, task_repo.py, task_queue.py）
- [x] AgentState 扩展（session_id, task_id, session_repo）
- [x] ReAct 图可构建（react_agent.py）
- [x] Supervisor 简单路径可运行（supervisor_graph.py）
- [x] /chat/stream 走 ReAct 图 + SSE 协议 + 错误处理
- [x] TUI 展示 ThinkingBlock
- [x] TUI 展示 ToolCallCard
- [x] 所有 LLM 调用使用 .ainvoke() + asyncio.timeout(30)
- [x] 端到端可运行（9 tests passing incl. E2E）
