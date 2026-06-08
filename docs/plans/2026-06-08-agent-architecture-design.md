# Nanoclaw Agent 架构设计

## 概述

Nanoclaw 是一个个人 AI 助手，接收用户的高层级请求，分解为结构化子任务，通过 ReAct Agent 循环调用工具执行，并将结果交付给用户。系统采用 Supervisor-Worker 架构，基于 LangGraph 构建，使用 PostgreSQL 持久化存储、Redis 任务队列、Ink TUI 前端。

## 架构

### 分层图

```
┌──────────────────────────────────────────────────┐
│               TUI 终端 (Ink)                       │
│        流式展示 agent_think/action/result          │
└──────────────────────┬───────────────────────────┘
                       │ HTTP/SSE
┌──────────────────────▼───────────────────────────┐
│              API 网关 (FastAPI)                    │
│     /chat/stream  /tasks  /sessions/callback      │
└──────┬────────────────────────────┬──────────────┘
       │                            │
┌──────▼────────┐         ┌────────▼──────────────┐
│  输入路由器    │         │  会话/状态 API         │
│  简单/复杂判定  │         │                        │
└──────┬────────┘         └────────┬──────────────┘
       │                            │
┌──────▼────────────────────────────▼──────────────┐
│              Agent 层 (LangGraph)                  │
│                                                   │
│  ┌───────────┐   ┌────────────────────────────┐  │
│  │ ReAct     │   │ Planner 节点               │  │
│  │ 节点      │   │ (仅复杂任务:               │  │
│  │ (简单任务) │   │  拆解 → Subtask DAG)      │  │
│  └───────────┘   └──────────┬─────────────────┘  │
│                             │                     │
│                    ┌────────▼─────────────────┐   │
│                    │  Dispatch 节点            │   │
│                    │  → TaskQueue.enqueue()   │   │
│                    └────────┬─────────────────┘   │
│                             │                     │
│                    ┌────────▼─────────────────┐   │
│                    │  Await 节点              │   │
│                    │  → queue.wait_for_all()  │   │
│                    └────────┬─────────────────┘   │
│                             │                     │
│                    ┌────────▼─────────────────┐   │
│                    │  Collect 节点             │   │
│                    │  → 验证结果               │   │
│                    │  → 触发补偿 if 部分失败   │   │
│                    │  → 汇总输出              │   │
│                    └────────┬─────────────────┘   │
│                             │                     │
│                    ┌────────▼─────────────────┐   │
│                    │  Worker Pool             │   │
│                    │  N 个 Worker             │   │
│                    │  每个运行独立 ReAct 子图  │   │
│                    └──────────────────────────┘   │
└──────┬───────────────────────────┬───────────────┘
       │                           │
┌──────▼──────────┐    ┌──────────▼──────────────┐
│  会话/任务仓库   │    │  任务队列 (抽象)         │
│  ┌───────────┐  │    │  ┌────────────────────┐ │
│  │ MemoryRepo│  │    │  │ MemoryQueue (mock) │ │
│  │ PgRepo    │  │    │  │ RedisQueue (生产)  │ │
│  └───────────┘  │    │  └────────────────────┘ │
└─────────────────┘    └─────────────────────────┘
```

### 任务/子任务生命周期

```
PENDING ──→ RUNNING ──→ SUCCEEDED
                  ↘ FAILED ──→ RETRYING ──→ RUNNING (重试循环)
                             ↘ CANCELLED (上游失败，依赖不满足)
                             ↘ COMPENSATING ──→ COMPENSATED (回滚副作用)
```

- **CANCELLED**: 当上游依赖任务 FAILED，当前任务无需执行直接取消
- **COMPENSATING**: 失败后执行 compensation 动作（删除已创建文件等）回滚副作用
- **COMPENSATED**: compensation 执行完毕

### SSE 事件协议

| 事件 | 载荷 | 触发时机 |
|------|------|---------|
| `agent_think` | `{content}` | LLM 产生推理文本 |
| `agent_action` | `{tool, args, task_id}` | 工具调用发起 |
| `agent_observation` | `{tool, result, task_id}` | 工具返回结果 |
| `agent_plan` | `{tasks: [...]}` | Planner 产出子任务 DAG |
| `message_chunk` | `{content}` | 最终回答逐字输出 |
| `task_status` | `{task_id, status}` | 子任务状态变更 |
| `done` | `{session_id}` | 全部处理完成 |

## 组件设计

### Supervisor 主图

```
input → [router] → simple → [react_node] → output
                 → complex → [planner] → [dispatch] → [await] → [collect] → output
```

**各节点职责：**

| 节点 | 输入 | 输出 | 行为 |
|------|------|------|------|
| **router** | 用户消息 | "simple" / "plan" | LLM 快速分类：是否需要多步分解 |
| **react_node** | 用户消息 | 最终回答 | 直接走 ReAct 循环，调用工具 |
| **planner** | 用户消息 + 可用工具列表 | Subtask DAG | LLM 拆解为多个子任务，标注依赖关系 |
| **dispatch** | TaskPlan | 子任务入队 | 遍历 PENDING 子任务，将可执行（依赖已满足）的入队 |
| **await** | 等待信号 | 全部结果 | asyncio.Event 等待所有子任务完成 |
| **collect** | 子任务结果 | 最终汇总 | 检查失败，触发补偿，聚合输出 |

当 await 返回后，collect 按以下逻辑处理：
1. 所有 SUCCEEDED → 聚合结果
2. 部分 FAILED → 标记下游 CANCELLED → 对已执行的副作用任务执行 compensation → 返回错误汇总

### Worker Pool

- N 个 Worker（初期 3 个），每个 Worker 内运行一个 ReAct 子图
- Worker 从 TaskQueue 拉取任务，每个 Worker 同一时间处理一个子任务
- Worker 拉取任务时检查 `depends_on` 是否全部 SUCCEEDED，不满足则跳过留待下次
- 每个 ReAct 步骤（think/action/observation）实时写入 Subtask.trace 并通过 SSE 推送给前端
- Worker 完成后调用 `TaskQueue.complete(id, result)`，触发对应 Event.set()

### 基础 ReAct 图（复用核心）

ReAct 是 LangGraph 子图，被两处复用：
1. **react_node**（简单任务路径）：直接运行
2. **每个 Worker 内部**：子任务执行器

```
State → LLM(think) → 有工具调用？→ 是 → 调用工具 → 观察 → 回到 LLM
                                         → 否 → 输出最终回答 → Done
```

每个循环步骤：
1. LLM 输出推理文本 → SSE 推送 `agent_think`
2. LLM 请求调用工具 → SSE 推送 `agent_action`
3. 工具执行返回 → SSE 推送 `agent_observation`
4. 以上全部写入 `Step` 追加到 `trace`

### 数据模型

```python
@dataclass
class Subtask:
    id: str                          # "task_001"
    description: str                 # "读取项目src目录结构"
    status: TaskStatus               # PENDING/RUNNING/SUCCEEDED/FAILED/RETRYING/CANCELLED/COMPENSATING/COMPENSATED
    depends_on: list[str]            # ["task_000"]
    tools_needed: list[str]          # ["read_file", "run_shell"]

    trace: list[Step]                # ReAct 执行轨迹
    compensation: str | None         # "rm -rf output/" — 回滚动作
    max_retries: int = 3
    retry_count: int = 0

    result: str | None               # 最终输出
    output_files: list[str]          # ["/path/to/report.md"]
    error: str | None                # 失败原因

@dataclass
class Step:
    type: Literal["think", "action", "observation"]
    content: str                     # 推理文本 / 工具入参 / 工具结果
    tool_name: str | None
    tool_args: dict | None
    tool_result: str | None
    timestamp: float

# 会话（Session）结构
@dataclass
class Session:
    id: str
    created_at: float
    messages: list[ChatMessage]      # 消息历史
    active_plan: TaskPlan | None     # 当前正在执行的计划（如有）
```

### 存储抽象

三个接口，每层两套实现：

```
SessionRepository(ABC)
├── MemorySessionRepo      # dict[str, Session]，进程内 mock
└── PgSessionRepo          # SQLAlchemy async + PostgreSQL

TaskRepository(ABC)
├── MemoryTaskRepo          # dict[str, TaskPlan]
└── PgTaskRepo             # SQLAlchemy async + PostgreSQL

TaskQueue(ABC)
├── MemoryQueue             # asyncio.Queue + dict[Event]，进程内
└── RedisQueue             # Redis list + pub/sub
```

**接口定义：**

```python
class SessionRepository(ABC):
    async def create(self, session: Session) -> Session
    async def get(self, session_id: str) -> Session | None
    async def append_message(self, session_id: str, msg: ChatMessage)
    async def get_history(self, session_id: str) -> list[ChatMessage]

class TaskRepository(ABC):
    async def save_plan(self, session_id: str, plan: TaskPlan)
    async def get_plan(self, session_id: str) -> TaskPlan | None
    async def update_subtask(self, session_id: str, subtask: Subtask)

class TaskQueue(ABC):
    async def enqueue(self, subtask: Subtask)
    async def dequeue(self) -> Subtask | None        # Worker 拉任务
    async def complete(self, task_id: str, result: str)
    async def fail(self, task_id: str, error: str)
    async def wait_for_all(self, task_ids: list[str]) -> dict   # WaitGroup.Wait()
```

MemoryQueue 的 `wait_for_all` 实现：

```python
class MemoryQueue(TaskQueue):
    def __init__(self):
        self._tasks: dict[str, Subtask] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._queue: asyncio.Queue[Subtask] = asyncio.Queue()

    async def enqueue(self, subtask: Subtask):
        self._tasks[subtask.id] = subtask
        self._events[subtask.id] = asyncio.Event()
        await self._queue.put(subtask)

    async def wait_for_all(self, task_ids: list[str]) -> dict:
        await asyncio.gather(*[self._events[tid].wait() for tid in task_ids])
        return {tid: self._tasks[tid].result for tid in task_ids}

    async def complete(self, task_id: str, result: str):
        self._tasks[task_id].result = result
        self._events[task_id].set()   # 唤醒 await_node
```

### LLM 配置

- **主要提供商**: Anthropic（使用 langchain-anthropic 的 ChatAnthropic）
- **配置**: 通过 env `NANOCLAW_LLM_MODEL` / `NANOCLAW_ANTHROPIC_API_KEY` 注入
- **注入方式**: LangGraph 节点的 State 中包含 LLM 实例引用，或通过依赖注入在构建图时传入

### 会话管理

- 每次 `/chat/stream` 连接开启一个 Session
- Session 记录消息历史，用于上下文续接
- 复杂任务执行期间，Session 持有 `active_plan` 引用
- 后续可通过 `session_id` 恢复对话历史

### 上下文 (Context) 管理

#### Context 的组成

```
┌────────────────────────────────────────────────────┐
│                 System Prompt                      │  ← 静态：角色定义、规则、行为约束
├────────────────────────────────────────────────────┤
│              User Profile (来自 Memory)             │  ← 跨会话：用户偏好、常用语言等
├────────────────────────────────────────────────────┤
│              Skill 注入 (来自 Memory)               │  ← 当前任务相关的技能/模式
├────────────────────────────────────────────────────┤
│         Thread Context (当前会话完整历史)            │  ← Session.messages
├────────────────────────────────────────────────────┤
│         Active Task State (当前执行状态)             │  ← Planner 输出 + Subtask.trace
├────────────────────────────────────────────────────┤
│           Tool Results / File Contents              │  ← 最近的工具输出、读文件内容
└────────────────────────────────────────────────────┘
```

#### 压缩策略（参考 Claude Code 实现）

四种独立的压缩策略，各自有独立触发条件，可根据场景组合使用。

##### 1. 微压缩 — 时间触发 (Time-based Micro Compact)

**触发条件**：距离上次助手回复超过 N 分钟。对应场景：用户昨晚的对话今早继续。

**动作**：清空旧的 tool_result 内容，保留工具调用记录。

```
// 压缩前
[user] 调用 read_file("src/main.py")
  → result: "import os\nimport sys\n\nclass App:\n..."
[user] 调用 grep("class.*Handler")
  → result: "class RequestHandler:\n  def handle..."

// 压缩后（保留"调用了什么工具"，清空结果）
[user] 调用 read_file("src/main.py")
  → result: [旧结果已清除 — 可在 session transcript 中恢复]
[user] 调用 grep("class.*Handler")
  → result: [旧结果已清除 — 可在 session transcript 中恢复]
```

**实现**：替换 tool_result 内容为一个标记字符串，不修改 tool_use 记录。

##### 2. 微压缩 — 计数触发 (Count-based Micro Compact)

**触发条件**：可压缩工具的结果数量超过阈值（如最近 N 轮外的结果需要清理）。

**可压缩工具**：read_file, run_shell, grep, web_search, web_fetch, file_edit, file_write（这些工具的结果通常较大且有副作用）。

**不可压缩工具**：agent_think, agent_plan（这些是推理过程，需要完整保留）。

**动作**：从最早的 tool_result 开始删除，保持最近 N 个结果完好。

##### 3. 微压缩 — 代码级压缩 (Rule-based Micro Compact)

**触发条件**：单条 tool_result 内容过大。

**动作**：不对工具结果做二阶段破坏性截断——首次保留完整结果，超出上下文限制时才触发聚合压缩。聚合压缩后可在后续通过 Source Recall（回溯原始数据源）恢复全量上下文。

**压缩手段**：
- 文件内容 → 保留路径 + 行数 + 首/末关键行
- JSON/列表 → 保留 schema + 条数
- 命令输出 → 保留摘要统计
- HTML → 标签结构摘要

**与 LLM 完整摘要的关键差异**：
- 不调用 LLM，纯代码逻辑压缩
- 不生成 summary message，只压缩具体 tool_result 内容
- **全量信息保留在数据源**：文件路径、搜索记录等元数据保留，需要时可以重新读取原始内容

##### 4. 完整摘要 (Auto Compact / LLM Summary)

**触发条件**：上下文 total tokens 超过阈值（如上下文窗口的 85%）。

**动作**：LLM 对旧对话生成摘要，替换为一条 summary message。与其他微压缩不同，这会改变消息结构。

**可恢复性保证**：
- Summary 消息中包含 `session_transcript_path`
- LLM 可以通过工具重新读取原始对话记录
- 原始数据源（磁盘/数据库）中的完整记录不受影响

#### 压缩上下文管理器

```python
class ContextManager:
    """负责组装和管理送到 LLM 的上下文"""

    def __init__(self, memory_store, transcript_path: str):
        self.memory = memory_store
        self.transcript_path = transcript_path

    async def build_prompt(
        self,
        session: Session,
        active_subtask: Subtask | None = None,
    ) -> list[BaseMessage]:
        """
        组装 LLM 的完整 prompt。
        应用阶段式压缩策略：
        1. Time-based MC：如果距离上次助理回复 > N 分钟，清空旧 tool_result
        2. Count-based MC：如果可压缩工具结果 > 阈值，删除最旧的结果
        3. Token 检查：如果 total > 阈值，触发 LLM 摘要
        """

    async def auto_compact(
        self,
        messages: list[ChatMessage],
        threshold_tokens: int,
    ) -> list[ChatMessage]:
        """LLM 摘要旧消息，替换为 summary"""

    def micro_compress_tool_result(
        self,
        result: ToolResult,
    ) -> ToolResult:
        """单条 tool_result 微压缩（纯代码逻辑，不调 LLM）"""
```

#### 关键设计决策

- **数据源保留保证可恢复**：压缩只是改变了送到 LLM 的消息结构，磁盘/数据库中的完整对话记录始终完整可用
- **多种压缩独立触发**：time-based MC、count-based MC、code-level MC 各自独立判断，可以组合，也可以单独生效
- **不是层级递进**：不按"4K→8K→16K"阶梯触发，每种压缩有自己的触发条件和逻辑
- **执行 All Agent 的子任务时，tool_result 量通常较大**：计划类 Agent 产出的 task plan 也是大块内容，需要纳入微压缩策略范围

### Checkpointer（断线恢复）

#### 抽象接口

```python
class Checkpointer(ABC):
    """图状态持久化 — 用于 Pod 重启后的断线恢复"""
    @abstractmethod
    async def save(self, session_id: str, state: dict) -> None: ...
    @abstractmethod
    async def load(self, session_id: str) -> dict | None: ...
    @abstractmethod
    async def list_sessions(self) -> list[str]: ...
```

#### 两套实现

```python
# Mock 阶段 — 每个 session 一个本地 JSON 文件
class LocalFileCheckpointer(Checkpointer):
    # $NANOCLAW_HOME/checkpoints/{session_id}/state.json
    # 原子写入：write to temp → rename

# 生产阶段 — PG JSONB 列
class PgCheckpointer(Checkpointer):
    # sessions 表加一列 serialized_state JSONB
```

#### Checkpoint 时机

- 每个 Supervisor 图节点执行完毕后（router / planner / dispatch / await / collect）
- ReAct 图每轮 think/action/observation 后
- Worker 完成一个子任务后

#### 恢复流程（Pod 重启后）

1. 前端重连时传入 `session_id`
2. Load checkpoint → 恢复 SupervisorState（包括 TaskPlan 和 Subtask 状态）
3. 检测 TaskQueue 中 status=RUNNING 的任务（对应 Worker 已死）
4. 重置 RUNNING → PENDING（让新 Worker pick up）
5. 从最近的 checkpoint 节点继续执行

### Memory 系统

#### 模块划分

| 模块 | 存储内容 | 范围 | 持久化 | 实现方式 |
|------|---------|------|--------|---------|
| 短期记忆 (Episodic) | 当前会话的交互记录、工具调用 | 单次 session | checkpoint 即可 | LangGraph State |
| 工作记忆 (Working) | 当前任务上下文、中间结果 | 单次 task | checkpoint 即可 | Subtask.trace |
| 用户画像 (User Profile) | 用户偏好、常用语言/框架、典型模式 | 跨 session | 写入 Memory Store | Chroma + JSON |
| 技能 (Skills) | 已验证的工具组合模式、工作流模板 | 跨 session | 写入 Memory Store | Chroma + JSON |
| 语义记忆 (Semantic) | 项目知识、领域理解 | 跨 session | 写入 Memory Store | Chroma + JSON |

#### 存储后端（抽象）

```python
class MemoryStore(ABC):
    """长期记忆存储 — 跨 session 持久化"""

    # 写入
    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None: ...

    # 检索（混合方式）
    @abstractmethod
    async def search(
        self,
        query: str,
        tags: list[str] | None = None,  # 关键词/标签过滤
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...
```

**MemoryEntry 结构：**

```python
@dataclass
class MemoryEntry:
    id: str
    type: Literal["user_profile", "skill", "semantic", "reflection"]
    tags: list[str]                # 检索标签
    content: str                   # 记忆内容
    embedding: list[float] | None  # Chroma embedding 向量
    source: str                    # 来源（session_id / task_id）
    confidence: float              # [0, 1] 置信度
    created_at: float
    confirmed: bool = False        # 用户已确认固化
```

#### 检索方式

1. **关键词/标签过滤**：通过 `tags` 字段精确匹配，初步筛选候选集
2. **向量语义排序**：对候选集用 Chroma embedding 做语义相似度排序，取 top_k
3. **混合**：先用关键词缩小范围，再用向量排序提高准确性

#### 写入时机

- **任务结束后 Reflection**：Supervisor 图的 Collector 节点挂载 reflection node，自动生成经验草稿
- **Reflection 草稿 → 用户确认 → 固化**：草稿存入 PENDING 状态，用户在前端通过反馈操作确认后才标记 `confirmed=True`
- **用户明确纠正**：用户纠正了 Agent 的某次错误行为，直接写入（标记 `confirmed=True`）

#### 短期 Reflection 流程

```
Collector 节点完成结果汇总
  → Reflection node 启动：
    1. 收集：Subtask DAG、每个 Subask 结果、失败原因、工具调用轨迹
    2. 分析：用 LLM 总结"这次学到了什么"、"哪些行为值得固化"
    3. 写入：生成 MemoryEntry(草稿, confirmed=False)
  → 如果用户在前端确认了经验
    4. 固化：confirmed=True
```

### Evaluation 数据收集

先写日志，后续再迁移到数据库。

#### 日志格式与路径

```
# 文件组织：$NANOCLAW_HOME/eval/{session_id}/events.jsonl

# 每行一个 JSON 事件
```

#### 事件类型

| 事件 | 数据 | 写入时机 |
|------|------|---------|
| `task_start` | task_id, 描述, subtask DAG, session_id | Planner 产出计划后 |
| `task_end` | task_id, 结果, 耗时, 成功/失败 | Collector 汇总后 |
| `tool_call` | 工具名, 入参摘要, 出参摘要, 执行时长 | 每次工具调用后 |
| `user_feedback` | session_id, 反馈类型, 内容 | 用户提供反馈时 |
| `context_stats` | token 数, 压缩触发次数, 压缩前后 token | ContextManager 操作后 |
| `llm_call` | 模型, input_tokens, output_tokens, 耗时 | 每次 LLM 调用后 |

```python
class EventLogger:
    """Evaluation 数据收集 — 先写 JSONL 日志"""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    async def log_event(
        self,
        session_id: str,
        event_type: str,
        data: dict,
    ) -> None:
        """追加一行 JSON 到对应的 session 事件文件"""
        # $NANOCLAW_HOME/eval/{session_id}/events.jsonl
```

### Evolution（进化）

#### 短期 — 交互中学习

- 借助上述 Memory 的 Reflection 流程，Agent 自动从任务执行中提取经验
- 经验以 `skill` 和 `user_profile` 类型存入 MemoryStore
- 后续对话中 ContextManager 自动检索相关经验注入 prompt

#### 长期 — GEPA 范式（后续阶段）

GEPA: Generate → Evaluate → Pruning → Adapt

1. **Generate**：基于 Evaluation 数据，生成 alternative prompt 变体
2. **Evaluate**：用 A/B test 对比不同 prompt 的效果（成功率、token 效率）
3. **Pruning**：淘汰效果差的变体
4. **Adapt**：将最优变体纳入 system prompt

> 注：GEPA 和 prompt 优化在此阶段暂不实现，先完成数据收集和 Memory 基础。

### Dreaming（梦境处理）

每天定时后台任务，对用户一天的行为数据进行清理和重组，形成可复用的技能和用户画像。

#### 触发方式

- **定时触发**：每天凌晨（低负载时段）自动运行
- **手动触发**：用户可通过命令 `/dream` 或 API 手动触发

#### 处理流程

```
触发 Dreaming
  │
  ├── 1. 收集今日全量 Evaluation 数据
  │   ├── 从 eval/ 目录读取今日所有 session 的 events.jsonl
  │   └── 加载未确认的 MemoryEntry 草稿
  │
  ├── 2. 技能提取 (Skill Mining)
  │   ├── 分析 tool_call 事件：是否存在重复出现的工具调用链
  │   │   例：用户经常先 read_file → grep → file_edit，可能是一个固定工作流
  │   ├── 提取高频模式：{(read_file, grep, file_edit): 出现 8 次}
  │   ├── LLM 验证：这个模式是否有意义，是否可以泛化为技能
  │   └── 写入 MemoryStore(type="skill", confirmed=True)
  │
  ├── 3. 用户画像提炼 (Profile Extraction)
  │   ├── 分析 task 类型分布：用户最常做哪类任务？
  │   ├── 分析 tool 使用偏好：用户偏爱哪些工具？
  │   ├── 分析错误模式：哪些场景 Agent 容易失败？
  │   └── 更新 MemoryStore(type="user_profile")
  │
  ├── 4. 记忆固化 (Memory Consolidation)
  │   ├── 对未确认的 reflection 草稿：
  │   │   ├── 去重：多条相似的取置信度最高的
  │   │   ├── 合并：相关草稿归并为一条综合记忆
  │   │   └── 降噪：低频/无价值的丢弃
  │   └── 固化后标记 confirmed=True
  │
  ├── 5. 评估摘要 (Evaluation Summary)
  │   ├── 生成今日统计：任务数、成功率、平均耗时、token 消耗等
  │   └── 写入 $NANOCLAW_HOME/eval/daily/{YYYY-MM-DD}/summary.json
  │
  └── 6. 清理 (Cleanup)
      ├── 压缩已处理的 raw events（保留摘要，可丢弃原始详情）
      └── 清理过期的临时文件
```

#### Dreaming 执行（作为后台 Worker）

Dreaming 本身也是一个 Agent 任务：
```
Dreaming = 一个带有以下工具的特殊 Agent：
  - read_eval_logs：读取 Evaluation 日志
  - write_memory：写入 MemoryStore
  - read_memory：检索现有 Memory
  - llm_analyze：调用 LLM 分析模式
```

它被放入 Worker Pool 执行，走普通的任务调度逻辑：

```
1. 每天 02:00 cron 触发 → 创建一个新的 Session
2. 生成一个 Subtask("执行每日梦境处理")
3. 推入 TaskQueue
4. Worker 拿到后执行 Dreaming Agent
5. 完成 → 写入结果 → Collector 汇总
```

### 用户定时任务 (Scheduled Tasks)

用户可定义类似 cron 的定时任务，到时间自动执行。

#### 任务定义

```python
@dataclass
class ScheduledTask:
    id: str
    user_id: str
    description: str                    # "每天早上9点检查邮件"
    prompt: str                         # Agent 执行的 prompt
    schedule: str                       # cron 表达式: "0 9 * * *"
    enabled: bool
    created_at: float
    last_run: str | None                # "2026-06-08T09:00:00"
    agent_id: str | None                # 如果指定了特定的 Agent 配置
    session_id: str | None              # 关联到哪个 session（可选）
```

#### 存储

```python
class ScheduledTaskRepo(ABC):
    @abstractmethod
    async def create(self, task: ScheduledTask) -> ScheduledTask: ...
    @abstractmethod
    async def get_due_tasks(self) -> list[ScheduledTask]: ...    # 查询到点触发的任务
    @abstractmethod
    async def update_last_run(self, task_id: str, ts: str) -> None: ...
    @abstractmethod
    async def list_all(self) -> list[ScheduledTask]: ...
    @abstractmethod
    async def delete(self, task_id: str) -> None: ...
```

Memory 实现 + PG 实现。

#### 调度器

```python
class Scheduler:
    """定时任务调度器 — 守护进程模式"""

    def __init__(
        self,
        task_repo: ScheduledTaskRepo,
        task_queue: TaskQueue,
        eval_logger: EventLogger,
    ):
        ...

    async def run(self):
        """主循环：每分钟检查是否有到期的任务"""
        while True:
            due_tasks = await self.task_repo.get_due_tasks()
            for task in due_tasks:
                # 1. 创建一个新的 Session
                # 2. 生成 Subtask
                # 3. 推入 TaskQueue
                # 4. 记录调度事件到 eval logger
                await self.dispatch_task(task)
                await self.task_repo.update_last_run(task.id, now())
            await asyncio.sleep(60)  # 每分钟检查一次
```

#### 用户管理

通过 TUI 或命令管理定时任务：
- `/schedule list` — 查看所有定时任务
- `/schedule add "0 9 * * *" "检查邮件并总结"` — 添加
- `/schedule remove <id>` — 删除
- `/schedule toggle <id>` — 启用/禁用

#### 执行流程

```
Scheduler 发现到期任务
  → 创建 Session(scheduled_task_id)
  → 推入 TaskQueue
  → Worker 拿到任务：
      ① 加载用户上下文（前几天的对话摘要）
      ② 加载用户画像（MemoryStore）
      ③ 加载相关技能（MemoryStore）
      ④ 执行 ReAct 循环
      ⑤ 结果写入 eval 日志
      ⑥ 更新 last_run
  → 如果执行结果需要通知用户 → 推送到用户的消息列表
```

## 项目文件结构

### 后端新增/改动

```
backend/src/nanoclaw/
├── __init__.py
├── config.py                       # 已有，扩展 LLM/AI 配置项
├── main.py                         # 已有
│
├── context/                        # 新增 — 上下文管理
│   ├── __init__.py
│   ├── manager.py                  # ContextManager：组装 prompt、压缩决策
│   ├── micro_compact.py            # 微压缩：time-based / count-based / code-level
│   └── auto_compact.py             # LLM 完整摘要
│
├── server/
│   ├── __init__.py
│   ├── app.py                      # 已有，扩展 SSE endpoint
│   └── deps.py                     # 新增 — FastAPI 依赖注入
│
├── agent/
│   ├── __init__.py
│   ├── state.py                    # 已有，扩展 SupervisorState
│   ├── supervisor_graph.py         # 新增 — 主图构建
│   ├── nodes/                      # 新增 — 各节点实现
│   │   ├── __init__.py
│   │   ├── router.py               # 简单/复杂分类
│   │   ├── planner.py              # 任务分解
│   │   ├── dispatch.py             # 子任务入队
│   │   ├── await_node.py           # WaitGroup 等待
│   │   ├── react_agent.py          # 基础 ReAct 子图
│   │   └── collector.py            # 结果聚合+补偿
│   └── worker_pool.py              # 新增 — Worker 管理
│
├── tools/                          # 已有，不变
│
├── models/
│   ├── __init__.py
│   ├── chat.py                     # 新增 — ChatMessage, Step, 等
│   └── task.py                     # 新增 — Subtask, TaskPlan, TaskStatus
│
├── memory/                         # 新增 — Memory 系统
│   ├── __init__.py                 # 已有，扩展
│   ├── store.py                    # MemoryStore 抽象 + Chroma 实现
│   ├── reflection.py               # Reflection Node：任务后经验提取
│   └── types.py                    # MemoryEntry, MemoryType
│
├── eval/                           # 新增 — Evaluation 数据收集
│   ├── __init__.py
│   ├── logger.py                   # EventLogger, events.jsonl
│   └── events.py                   # 事件类型定义
│
├── dreaming/                       # 新增 — 梦境处理
│   ├── __init__.py
│   ├── engine.py                   # Dreaming 主流程：技能挖掘、画像提炼、记忆固化
│   └── agent.py                    # Dreaming Agent（作为特殊 Worker）
│
├── scheduler/                      # 新增 — 定时任务
│   ├── __init__.py
│   ├── engine.py                   # Scheduler 主循环
│   └── repo.py                     # ScheduledTaskRepo 抽象 + Memory + PG 实现
│
├── storage/
│   ├── __init__.py
│   ├── session_repo.py             # 新增 — 抽象 + MemorySessionRepo
│   ├── task_repo.py                # 新增 — 抽象 + MemoryTaskRepo
│   ├── task_queue.py               # 新增 — 抽象 + MemoryQueue
│   └── checkpointer.py             # 新增 — 抽象 + LocalFileCheckpointer
```

### 前端新增组件

```
cli/src/components/
├── ThinkingBlock.tsx               # 新增 — agent_think 思考过程（灰色/斜体）
├── ToolCallCard.tsx                # 新增 — agent_action + observation 卡片
├── PlanView.tsx                    # 新增 — 任务列表 DAG 展示
└── TaskStatusBadge.tsx             # 新增 — 子任务状态标识（状态点）
```

## 实施阶段

### Phase 1: 基础 (Foundation)
1. 定义数据模型（Subtask, Step, TaskStatus, ChatMessage, Session）
2. 实现存储抽象层（MemorySessionRepo, MemoryTaskRepo, MemoryQueue）
3. 构建基础 ReAct LangGraph（简单任务 + Worker 复用）
4. 改造 `/chat/stream` 走 ReAct 图（简单任务路径）
5. TUI 展示 ReAct 步骤（think/action/observation）

### Phase 2: 多任务 (Multi-Task)
1. 实现 Router 节点（LLM 简单/复杂分类）
2. 实现 Planner 节点（LLM 拆解为 Subtask DAG）
3. 实现 Dispatch + Await + Collect 节点
4. 构建 Worker Pool 并接入 TaskQueue
5. 完整 Supervisor 图串联：router → plan → execute → collect

### Phase 3: Memory + Evaluation
1. Chroma 集成 + MemoryStore 实现
2. EventLogger + events.jsonl 写入
3. ContextManager 集成 Memory 检索
4. Reflection Node（任务结束后自动生成经验草稿）
5. TUI 反馈交互（确认/拒绝经验）

### Phase 4: Dreaming + Scheduled Tasks
1. Dreaming Engine：技能挖掘、画像提炼、记忆固化
2. Scheduler 守护进程（每分钟检查）
3. ScheduledTaskRepo（Memory + PG）
4. TUI `/schedule` 命令
5. Dreaming 定时触发（cron）

### Phase 5: Docker + 真实存储
1. Docker Compose: PostgreSQL + Redis + Chroma
2. PgSessionRepo / PgTaskRepo / RedisQueue 实现
3. PgCheckpointer / PgScheduledTaskRepo
4. 切换连接串，验证全流程

### Phase 6: 韧性 + 前端优化
1. 子任务失败自动重试
2. 上游失败时下游 CANCELLED + compensation 回滚
3. 结果汇总中的错误处理与用户通知
4. PlanView 组件（任务树 + 状态）
5. ThinkingBlock 组件
6. ToolCallCard 组件
7. 错误展示优化
8. ScheduledTask 管理界面

## 设计决策理由

- **为什么 LangGraph**: 内置图状态机 + reducer 模式（`add_messages`），显式节点/边控制，适合复杂 Agent 编排
- **为什么 Supervisor-Worker**: 规划与执行分离，支持子任务并行，故障隔离清晰
- **为什么 asyncio.Event 做任务等待**: 纯 Python 实现，mock 阶段无需外部依赖；后续切换到 Redis pub/sub 时 agent 逻辑无需变动
- **为什么存储抽象层**: Mock-and-Swap 策略 — mock 阶段全内存，生产换 PG/Redis，代码零改动
- **为什么先简单 ReAct 再叠加 Plan**: 分步验证，每一阶段都有可运行的版本，降低一次性引入过多概念的风险

## 已知限制

- 初期 MemoryQueue 是进程内队列，Worker 数量需控制（3-5 个），避免阻塞事件循环
- SSE 事件单向推送，前端无背压机制
- Planner 的 LLM 调用可能成本较高，后续可考虑缓存或流式生成计划
- Compensation 只覆盖工具管理的副作用（文件、命令），外部副作用（邮件、API 调用）不在范围内
- 会话存储是 append-only 设计，不支持消息编辑或删除
