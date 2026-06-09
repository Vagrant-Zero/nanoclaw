# Phase 4: Dreaming + Scheduled Tasks 实施计划

> **目标**：让 Nanoclaw 具备后台"梦境"处理能力和用户定时任务调度能力。Dreaming Engine 每天凌晨自动运行，对一天的交互数据进行技能挖掘、画像提炼和记忆固化。Scheduler 每分钟检查定时任务，到期自动触发执行。

**依赖**：Phase 4 依赖 Phase 1-3 的以下接口（即使 Phase 1-3 未实现也不阻塞 plan 编写）：
- `models/` — ChatMessage, Step, Subtask, TaskPlan, TaskStatus
- `eval/logger.py` — EventLogger, .log_event()
- `eval/events.py` — 事件类型常量
- `memory/store.py` — MemoryStore(ABC), .save(), .search()
- `memory/types.py` — MemoryEntry, MemoryType
- `storage/session_repo.py` — SessionRepository(ABC), .create()
- `storage/task_queue.py` — TaskQueue(ABC), .init_plan(), .enqueue()
- `agent/worker_pool.py` — Worker Pool 抽象（用于 Dreaming Agent 执行）
- `config.py` — Settings（需要扩展 paths）

**前置阅读**：`docs/plans/2026-06-08-agent-architecture-design.md` 的 Dreaming、Scheduled Tasks、Memory 章节

---

### Task 1：Dreaming 数据模型 + Evaluation 基础

**文件**：
- 创建：`backend/src/nanoclaw/eval/__init__.py`
- 创建：`backend/src/nanoclaw/eval/events.py` — 事件类型常量和结构
- 创建：`backend/src/nanoclaw/eval/logger.py` — EventLogger
- 创建：`backend/src/nanoclaw/memory/types.py` — MemoryEntry, MemoryType

**原因**：
Dreaming Engine 直接操作 Evaluation 日志和 Memory。这两个模块是 Phase 3 的内容，但 Phase 4 需要它们的完整形态。即使在代码中 Phase 3 尚未实现，Phase 4 的 Engine 和 Agent 仍然通过标准的接口调用——Mock-and-Swap 策略要求接口先行。

**Step 1: 定义 eval/events.py**

```python
# 事件类型常量（用于 EventLogger 和 Dreaming 的日志分析）

EVENT_TASK_START = "task_start"
EVENT_TASK_END = "task_end"
EVENT_TOOL_CALL = "tool_call"
EVENT_USER_FEEDBACK = "user_feedback"
EVENT_CONTEXT_STATS = "context_stats"
EVENT_LLM_CALL = "llm_call"
```

为什么：Dreaming Engine 需要根据事件类型过滤日志（如只分析 `tool_call` 事件做 skill mining）。事件类型常量集中在一个文件，避免硬编码字符串分散在各处。

**Step 2: 定义 eval/logger.py**

```python
from pathlib import Path
import json

class EventLogger:
    """Evaluation 数据收集 — 先写 JSONL 日志，后续迁移到数据库。"""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def log_event(
        self,
        session_id: str,
        event_type: str,
        data: dict,
    ) -> None:
        """追加一行 JSON 到对应的 session 事件文件。
        文件路径：{base_dir}/{session_id}/events.jsonl
        """
        ...

    async def read_session_events(
        self,
        session_id: str,
        event_type: str | None = None,
    ) -> list[dict]:
        """读取某个 session 的全部事件，可按 event_type 过滤。
        这是 Dreaming Engine 的 read_eval_logs tool 的核心实现。
        """
        ...

    async def list_sessions(self) -> list[str]:
        """列出所有有 eval 数据的 session ID。"""
        ...

    async def daily_summary_path(self, date_str: str) -> Path:
        """返回 {base_dir}/daily/{date_str}/summary.json 路径。"""
```

为什么：`read_session_events()` 和 `list_sessions()` 专门为 Dreaming 设计，普通的交互流程不需要这两个方法。将它们放在 EventLogger 上而非单独拆分，是因为它们直接读取同一套 JSONL 文件，不需要额外的存储抽象。

**Step 3: 定义 memory/types.py**

```python
from dataclasses import dataclass

class MemoryType:
    USER_PROFILE = "user_profile"
    SKILL = "skill"
    SEMANTIC = "semantic"
    REFLECTION = "reflection"

@dataclass
class MemoryEntry:
    id: str
    type: str                    # MemoryType 之一
    tags: list[str]
    content: str
    embedding: list[float] | None
    source: str                  # 来源 session_id
    confidence: float
    created_at: float
    confirmed: bool = False
```

为什么：MemoryEntry 是 Dreaming Engine 的输出目标（写入 MemoryStore）。`MemoryType` 常量和 `confirmed` 字段在 Dreaming 的记忆固化步骤中直接用到——未确认的 reflection 草稿被去重合并然后标记 `confirmed=True`。

**Step 4: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.eval.events import EVENT_TOOL_CALL
from nanoclaw.eval.logger import EventLogger
from nanoclaw.memory.types import MemoryEntry, MemoryType
print('Data models OK')
"
```

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/eval/ backend/src/nanoclaw/memory/types.py
git commit -m "feat: add evaluation logger and memory type definitions for Phase 4"
```

---

### Task 2：ScheduledTask 模型 + Repository

**文件**：
- 创建：`backend/src/nanoclaw/scheduler/__init__.py`
- 创建：`backend/src/nanoclaw/scheduler/repo.py` — ScheduledTaskRepo 抽象 + MemoryScheduledTaskRepo + PgScheduledTaskRepo

**原因**：
ScheduledTask 是 Phase 4 新增的核心数据实体。Repository 模式与 Phase 1 的 SessionRepo/TaskRepo 一致（Mock-and-Swap）。ID 前缀约定 `sched_` 避免与 session 命名空间冲突。

**Step 1: 定义 ScheduledTask 模型**

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ScheduledTask:
    id: str                       # 形如 "sched_001" — sched_ 前缀防命名空间冲突
    user_id: str                  # 预留，单人版用 "default"
    description: str              # "每天早上9点检查邮件"
    prompt: str                   # Agent 执行的 prompt 内容
    schedule: str                 # cron 表达式: "0 9 * * *"
    enabled: bool = True
    created_at: float
    last_run: str | None = None   # ISO 8601: "2026-06-08T09:00:00"
    agent_id: str | None = None   # 预留，特定 Agent 配置
    session_id: str | None = None # 可选，关联到已有 session
```

为什么放在 `scheduler/repo.py` 而非 `models/`：ScheduledTask 与 TaskPlan/Subtask 是不同的领域概念。"定时任务"是用户级配置，不是 Agent 运行时数据。放在 scheduler 包内高内聚，避免 models/ 过载。

为什么要 `sched_` 前缀：Session 的 ID 格式是纯 UUID 或 `session_xxx`，ScheduledTask 也在调度时创建 Session。前缀命名空间隔离避免了不同实体 ID 碰撞的可能性，也方便在 eval 日志中区分"用户主动触发"和"定时任务触发"。

**Step 2: 实现 ScheduledTaskRepo 抽象**

```python
from abc import ABC, abstractmethod

class ScheduledTaskRepo(ABC):
    """定时任务仓库 — 支持 Mock-and-Swap 两套实现。"""

    @abstractmethod
    async def create(self, task: ScheduledTask) -> ScheduledTask:
        """创建一个定时任务。返回的实例包含生成的 id。"""
        ...

    @abstractmethod
    async def get_due_tasks(self) -> list[ScheduledTask]:
        """查询到点触发的任务：
           SELECT * WHERE enabled = true
             AND (last_run IS NULL OR next_trigger(last_run, schedule) <= now())
        逻辑：每次调用都重新计算 cron 表达式，将 last_run 代入确认是否需要触发。
        不依赖外部 cron 库的持久化 schedule 状态——每次都是无状态计算。
        """
        ...

    @abstractmethod
    async def update_last_run(self, task_id: str, timestamp: str) -> None:
        """更新任务的 last_run 字段。触发成功后由 Scheduler 调用。"""
        ...

    @abstractmethod
    async def list_all(self) -> list[ScheduledTask]:
        """返回全部定时任务（用于 TUI 展示）。"""
        ...

    @abstractmethod
    async def get(self, task_id: str) -> ScheduledTask | None:
        """按 ID 查询。用于 TUI 的 toggle/remove 操作。"""
        ...

    @abstractmethod
    async def delete(self, task_id: str) -> None:
        """删除指定任务。"""
        ...

    @abstractmethod
    async def update(self, task_id: str, updates: dict) -> ScheduledTask | None:
        """部分更新（用于 toggle enabled 等操作）。"""
        ...
```

为什么强调无状态 cron 计算：不存储"下一次触发时间"字段，每次 `get_due_tasks` 都现场计算。这避免了时间同步问题（手动修改系统时间不会导致漏触发或重复触发）。计算逻辑：

```python
def is_due(task: ScheduledTask, now: datetime) -> bool:
    if not task.enabled:
        return False
    if task.last_run is None:
        return True  # 从未运行过，立即触发
    # 将 last_run 代入 cron 表达式，计算 next trigger
    next_run = cron_next(task.schedule, parse_iso(task.last_run))
    return next_run <= now
```

**Step 3: 实现 MemoryScheduledTaskRepo**

```python
class MemoryScheduledTaskRepo(ScheduledTaskRepo):
    """进程内 dict 实现 — mock 阶段使用。"""

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._counter = 0

    async def create(self, task: ScheduledTask) -> ScheduledTask:
        self._counter += 1
        task.id = f"sched_{self._counter:04d}"
        self._tasks[task.id] = task
        return task

    async def get_due_tasks(self) -> list[ScheduledTask]:
        now = datetime.now()
        return [
            t for t in self._tasks.values()
            if is_due(t, now)
        ]
    ...
```

为什么需要计数器：Memory 实现需要生成唯一 ID。使用递增计数器 + `sched_` 前缀简单可靠。生产环境 PG 实现则用数据库序列或 UUID。

**Step 4: 实现 PgScheduledTaskRepo（Stub）**

```python
class PgScheduledTaskRepo(ScheduledTaskRepo):
    """PG 实现 — Phase 5 完成，Phase 4 只做接口声明。"""

    async def create(self, task: ScheduledTask) -> ScheduledTask:
        raise NotImplementedError("PG implementation in Phase 5")

    async def get_due_tasks(self) -> list[ScheduledTask]:
        raise NotImplementedError("PG implementation in Phase 5")
    ...
```

为什么含 Stub：Mock-and-Swap 策略要求所有实现都在接口定义时占位。Phase 4 的 Scheduler 依赖 ScheduledTaskRepo，但只需要 Memory 实现。PG stub 的存在保证类型检查通过且明确标记未实现。

**Step 5: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.scheduler.repo import MemoryScheduledTaskRepo, ScheduledTask, is_due
from datetime import datetime
repo = MemoryScheduledTaskRepo()
task = ScheduledTask(id='sched_0001', user_id='default', description='test',
                     prompt='hello', schedule='* * * * *', created_at=datetime.now().timestamp())
assert is_due(task, datetime.now()) == True
print('ScheduledTaskRepo OK')
"
```

**Step 6: 提交**

```bash
git add backend/src/nanoclaw/scheduler/
git commit -m "feat: add ScheduledTask model and repository with Memory implementation"
```

---

### Task 3：Dreaming Agent 工具集

**文件**：
- 创建：`backend/src/nanoclaw/dreaming/__init__.py`
- 创建：`backend/src/nanoclaw/dreaming/tools.py` — Dreaming Agent 的四个工具

**原因**：
设计文档规定 Dreaming 是一个特殊的 Agent Worker，拥有四个专用工具：`read_eval_logs`、`write_memory`、`read_memory`、`llm_analyze`。这些工具必须与 Nanoclaw 的通用工具（BaseTool/ToolRegistry）遵循相同的接口协议，以便 Dreaming Agent 可以像普通 Agent 一样被 Worker Pool 调度。

**Step 1: 实现 read_eval_logs 工具**

```python
class ReadEvalLogsTool(BaseTool):
    """读取 Evaluation 日志。按日期/事件类型/关键词过滤。"""

    spec = ToolSpec(
        name="read_eval_logs",
        description="Read evaluation logs for a given date range or session. Filters by event type, session ID, or keyword.",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date string YYYY-MM-DD. Reads all sessions for that day."},
                "session_id": {"type": "string", "description": "Specific session ID to read."},
                "event_type": {"type": "string", "description": "Filter by event type: task_start, task_end, tool_call, etc."},
                "limit": {"type": "integer", "description": "Max events to return."},
            },
            "required": [],
        },
    )

    def __init__(self, eval_logger: EventLogger) -> None:
        self.eval_logger = eval_logger
```

为什么设计参数可选：Dreaming 的不同阶段需要不同的查询。Skill Mining 需要所有 session 的 `tool_call` 事件，Profile Extraction 需要 `task_start`/`task_end` 事件。提供多维度过滤而不是让 LLM 暴力读取。

**Step 2: 实现 write_memory 工具**

```python
class WriteMemoryTool(BaseTool):
    """写入 MemoryStore。Dreaming Engine 用此工具写入挖掘出的技能和画像。"""

    spec = ToolSpec(
        name="write_memory",
        description="Write an entry to the long-term memory store. Used to persist skills, user profile data, or reflections.",
        parameters={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["skill", "user_profile", "semantic", "reflection"]},
                "content": {"type": "string", "description": "The memory content to store."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for retrieval filtering."},
                "confidence": {"type": "number", "description": "Confidence score 0.0-1.0"},
            },
            "required": ["type", "content"],
        },
    )

    def __init__(self, memory_store: MemoryStore) -> None:
        self.memory_store = memory_store
```

为什么需要有 `confidence`：Skill Mining 检测到的模式有置信度问题——某个工具调用链出现 3 次 vs 30 次，置信度不同。写入时标记置信度，后续 ContextManager 在注入 prompt 时可以只选取高置信度的技能。

**Step 3: 实现 read_memory 工具**

```python
class ReadMemoryTool(BaseTool):
    """检索现有 Memory。Dreaming Engine 用此工具查询已有技能/画像做去重对比。"""

    spec = ToolSpec(
        name="read_memory",
        description="Search existing memory entries. Used to avoid duplicate skill/profile entries.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for semantic matching."},
                "type": {"type": "string", "description": "Filter by memory type."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "description": "Max results."},
            },
            "required": ["query"],
        },
    )
```

为什么需要这个工具：Dreaming 的"记忆固化"步骤需要对现有记忆做去重——查询已有记忆避免重复写入同一个技能。这个工具让 LLM 可以自主判断"这个模式是否已经存过了"。

**Step 4: 实现 llm_analyze 工具**

```python
class LlmAnalyzeTool(BaseTool):
    """调用 LLM 分析数据模式。这是 Dreaming 的核心工具——LLM 不是直接参与对话，
    而是被 Dreaming Agent 当作"分析引擎"调用。"""

    spec = ToolSpec(
        name="llm_analyze",
        description="Analyze a dataset using an LLM. Used for pattern detection, summarization, and insight extraction.",
        parameters={
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "The data to analyze (eval logs, tool traces, etc.)."},
                "instruction": {"type": "string", "description": "What to look for or analyze."},
                "format": {"type": "string", "description": "Output format: summary, json, bullet_points"},
            },
            "required": ["data", "instruction"],
        },
    )

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run(self, **kwargs) -> str:
        """将 data + instruction 发给 LLM，返回分析结果。"""
        ...
```

为什么这个工具存在而不是让 Dreaming Agent 直接通过 LLM 对话分析：Dreaming Agent 本身的 ReAct 循环已经使用 LLM 了——`llm_analyze` 是"内部 LLM 嵌套调用"，专门用于分析大型数据块。这解耦了"决策用什么工具"和"分析数据内容"两个不同的 LLM 调用，避免一次 LLM 调用中处理太多信息。

**Step 5: 注册工具工厂**

```python
def register_dreaming_tools(
    registry: ToolRegistry,
    eval_logger: EventLogger,
    memory_store: MemoryStore,
    llm: Any,
) -> None:
    """创建一个包含四个 Dreaming 工具的 ToolRegistry。"""
    registry.register(ReadEvalLogsTool(eval_logger))
    registry.register(WriteMemoryTool(memory_store))
    registry.register(ReadMemoryTool(memory_store))
    registry.register(LlmAnalyzeTool(llm))
```

**Step 6: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.dreaming.tools import ReadEvalLogsTool, WriteMemoryTool, ReadMemoryTool, LlmAnalyzeTool
from nanoclaw.tools.base import ToolSpec
print('Dreaming tools OK')
"
```

**Step 7: 提交**

```bash
git add backend/src/nanoclaw/dreaming/
git commit -m "feat: add Dreaming Agent tools (read_eval_logs, write_memory, read_memory, llm_analyze)"
```

---

### Task 4：Dreaming Engine — 主流程编排

**文件**：
- 创建：`backend/src/nanoclaw/dreaming/engine.py` — DreamingEngine 主类

**原因**：
Dreaming Engine 是 Phase 4 最复杂的组件。它是一个 Python 类，管理整个"梦境处理"流程，从数据收集到清理共 6 个步骤。它不直接是 Agent——它负责编排 Agent 的执行（创建 Session、生成 Subtask、推入 TaskQueue）。Engine 的核心是 `run_dreaming()` 方法，可以被 cron 触发也可以手动触发。

**Step 1: 定义 DreamingEngine 类结构**

```python
import asyncio
from datetime import datetime, timedelta

class DreamingEngine:
    """梦境处理引擎。每天凌晨（或手动）触发，执行技能挖掘、画像提炼、记忆固化。"""

    def __init__(
        self,
        eval_logger: EventLogger,
        memory_store: MemoryStore,
        task_queue: TaskQueue,
        session_repo: SessionRepository,
        llm: Any,
        dreaming_tools: ToolRegistry,
    ) -> None:
        self.eval_logger = eval_logger
        self.memory_store = memory_store
        self.task_queue = task_queue
        self.session_repo = session_repo
        self.llm = llm
        self.dreaming_tools = dreaming_tools  # 包含 read_eval_logs, write_memory 等

    async def run_dreaming(self, date_str: str | None = None) -> dict:
        """
        执行完整的梦境处理流程。
        date_str: 处理哪一天的数据，None = 今天。
        返回处理摘要（写入 summary.json）。
        """
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")

        # 1. 创建 Dreaming Session（不干扰用户会话）
        dreaming_session = await self._create_dreaming_session(date_str)

        # 2. 创建 Dreaming TaskPlan（包含单个 Subtask）
        plan = await self._create_dreaming_plan(dreaming_session.id, date_str)

        # 3. 推入 TaskQueue → Worker 将作为 Dreaming Agent 执行
        await self.task_queue.init_plan(plan)
        results = await self.task_queue.wait_for_all()

        # 4. 写入每日摘要
        summary = await self._write_daily_summary(date_str, results)
        return summary
```

为什么 Engine 将 Dreaming 作为普通 Agent 任务调度而不是直接执行：
- Dreaming 涉及的四个工具（read_eval_logs 等）不是 Python 函数直接调用，而是通过 LLM ReAct 循环来决策"什么时候用什么工具"。
- 这样做的好处：Skill Mining 的决策（"这些 tool_call 链条是否构成一个可泛化的技能"）由 LLM 判断，不需要硬编码模式匹配规则。
- 代价：依赖 Worker Pool 可用。如果所有 Worker 都在处理用户任务，Dreaming 任务会排队等待。

**Step 2: 实现流程子步骤**

```python
class DreamingEngine:
    # ...

    async def _collect_eval_data(self, date_str: str) -> list[dict]:
        """收集指定日期的全部 Evaluation 事件。
        遍历该日期下所有 session 的 events.jsonl。
        """
        sessions = await self.eval_logger.list_sessions()
        all_events = []
        for sid in sessions:
            events = await self.eval_logger.read_session_events(sid)
            all_events.extend(events)
        return all_events

    async def _skill_mining(self, tool_call_events: list[dict]) -> list[MemoryEntry]:
        """技能挖掘：分析 tool_call 链，检测高频模式。
        实现策略有两种方案，LLM-based 和 rule-based：
        - Rule-based 优先：收集连续 tool_call 的 (tool_name, args) 序列作为模式指纹
        - LLM-based 辅助：对统计前 N 的高频模式让 LLM 验证"是否有意义"
        """
        # 1. 按 session 分组 tool_call 事件，保持时间顺序
        # 2. 提取连续的工具调用链（2-4 个连续调用）
        # 3. 统计相同链的出现频率
        # 4. 频率 > 阈值（如 3 次）→ 提交给 LLM 验证
        # 5. 验证通过 → 构建 MemoryEntry(type="skill")
        ...

    async def _profile_extraction(self, task_events: list[dict]) -> list[MemoryEntry]:
        """用户画像提取：分析任务类型分布、工具偏好、错误模式。"""
        ...

    async def _memory_consolidation(
        self,
        unconfirmed_reflections: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """记忆固化：去重、合并、降噪。"""
        ...

    async def _write_daily_summary(self, date_str: str, results: dict) -> dict:
        """写入每日评估摘要。"""
        ...
```

为什么 Rule-based 优先 + LLM 辅助：纯 LLM 调用成本高，每天扫描几百条 tool_call 事件都过 LLM 分析不现实。先用代码逻辑做频率统计（O(n)），只让 LLM 分析 top-K 候选模式。这样 token 消耗可控，而且 LLM 的判断质量更高（因为数据已经预处理了）。

**Step 3: 实现工具调用链指纹提取算法**

```python
from collections import defaultdict
import hashlib

def _extract_tool_chains(events: list[dict], chain_length: int = 3) -> dict[str, int]:
    """从 tool_call 事件流中提取连续工具调用链模式。
    返回 {(tool1, tool2, ...): count} 的频率统计。

    指纹算法：对每个 session 按时间排序 tool_call 事件，
    取连续 N 个的 (tool_name) 元组作为模式指纹。
    """
    chains: dict[tuple[str, ...], int] = defaultdict(int)

    # 按 session 分组
    by_session: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") == "tool_call":
            by_session[ev.get("session_id", "unknown")].append(ev)

    for session_id, session_events in by_session.items():
        session_events.sort(key=lambda e: e.get("timestamp", 0))
        tool_names = [e.get("data", {}).get("tool_name", "?") for e in session_events]

        for i in range(len(tool_names) - chain_length + 1):
            chain = tuple(tool_names[i:i + chain_length])
            chains[chain] += 1

    return dict(chains)
```

为什么用 `(tool_name,)` 元组而不是包括 args：args 变化太大，不同文件名但相同操作序列应该被认为是同一个模式。比如 `read_file("a.py") → grep("class")` 和 `read_file("b.py") → grep("Handler")` 应该匹配同一个模式 `(read_file, grep)`。

**Step 4: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.dreaming.engine import DreamingEngine
print('DreamingEngine class loaded')
"
```

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/dreaming/engine.py
git commit -m "feat: implement DreamingEngine with skill mining, profile extraction, memory consolidation"
```

---

### Task 5：Scheduler 守护进程

**文件**：
- 创建：`backend/src/nanoclaw/scheduler/engine.py` — Scheduler 主循环

**原因**：
Scheduler 是一个 asyncio 后台任务，每分钟检查 `ScheduledTaskRepo.get_due_tasks()`，对到期的任务创建 Session + Subtask 并推入 TaskQueue。它作为守护协程在应用启动时运行，与 FastAPI 服务器共存。

**Step 1: 实现 Scheduler 类**

```python
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class Scheduler:
    """定时任务调度器 — 作为 asyncio 后台任务运行。"""

    POLL_INTERVAL = 60  # 秒

    def __init__(
        self,
        task_repo: ScheduledTaskRepo,
        session_repo: SessionRepository,
        task_queue: TaskQueue,
        eval_logger: EventLogger,
    ) -> None:
        self.task_repo = task_repo
        self.session_repo = session_repo
        self.task_queue = task_queue
        self.eval_logger = eval_logger
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """启动调度器后台任务。"""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Scheduler started (poll interval: %ds)", self.POLL_INTERVAL)

    async def stop(self):
        """停止调度器。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def _run_loop(self):
        """主循环：每分钟检查到期任务。"""
        while self._running:
            try:
                due_tasks = await self.task_repo.get_due_tasks()
                for scheduled_task in due_tasks:
                    await self._dispatch(scheduled_task)
            except Exception as e:
                logger.exception("Scheduler error: %s", e)
            await asyncio.sleep(self.POLL_INTERVAL)
```

为什么需要 `start()`/`stop()` 生命周期：Scheduler 不是一个"调用就返回"的函数，它是一个常年运行的后台协程。应用启动时创建实例并 `start()`，关闭时 `stop()`。这遵循 FastAPI 的 `lifespan` 模式。

**Step 2: 实现 _dispatch 方法**

```python
class Scheduler:
    # ...

    async def _dispatch(self, scheduled_task: ScheduledTask) -> None:
        """执行一个定时任务。

        流程：
        1. 创建 Session(id=f"sched_{scheduled_task.id}")
        2. 生成 Subtask（包含 scheduled_task.prompt 作为指令）
        3. 推入 TaskQueue
        4. 记录 eval 事件
        5. 更新 last_run
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. 创建 Session — ID 带 sched_ 前缀隔离命名空间
        from nanoclaw.models.chat import Session, ChatMessage
        session = Session(
            id=f"sched_{scheduled_task.id}_{int(datetime.now().timestamp())}",
            created_at=datetime.now().timestamp(),
            messages=[
                ChatMessage(role="user", content=scheduled_task.prompt),
            ],
            active_plan=None,
        )
        await self.session_repo.create(session)

        # 2. 创建 Subtask
        from nanoclaw.models.task import Subtask, TaskStatus, TaskPlan
        subtask = Subtask(
            id=f"{session.id}_task_001",
            description=scheduled_task.description,
            status=TaskStatus.PENDING,
            depends_on=[],
            tools_needed=[],
            trace=[],
        )
        plan = TaskPlan(
            session_id=session.id,
            subtasks=[subtask],
        )

        # 3. 推入 TaskQueue
        await self.task_queue.init_plan(plan)

        # 4. 记录调度事件
        await self.eval_logger.log_event(
            session_id=session.id,
            event_type="task_start",
            data={
                "scheduled_task_id": scheduled_task.id,
                "description": scheduled_task.description,
            },
        )

        # 5. 更新 last_run
        await self.task_repo.update_last_run(scheduled_task.id, now_iso)

        logger.info("Dispatched scheduled task %s: %s", scheduled_task.id, scheduled_task.description)
```

为什么每次创建新 Session：定时任务的执行环境和用户聊天环境完全隔离。每次触发创建独立的 Session，避免状态污染。Session ID 使用 `sched_{id}_{timestamp}` 确保唯一性。

为什么只创建单个 Subtask：定时任务是一个独立的 Agent 执行（走 ReAct 简单路径），不需要 Planner 拆解。如果未来需要规划能力，可以在 prompt 中让 Agent 自主拆解。

**Step 3: 实现 cron 表达式计算**

Scheduler 需要判断一个任务的 `last_run + schedule` 是否 <= now。需要一个轻量级的下次触发时间计算函数：

```python
import re

def _parse_cron(expr: str) -> dict:
    """解析 cron 表达式为 {minute, hour, day_of_month, month, day_of_week}。"""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expr}")
    return {
        "minute": _parse_cron_part(parts[0], 0, 59),
        "hour": _parse_cron_part(parts[1], 0, 23),
        "day_of_month": _parse_cron_part(parts[2], 1, 31),
        "month": _parse_cron_part(parts[3], 1, 12),
        "day_of_week": _parse_cron_part(parts[4], 0, 6),
    }

def _parse_cron_part(part: str, min_val: int, max_val: int) -> set[int]:
    """解析 cron 表达式的一部分（支持 *、数字、逗号列表）。
    如: "*" → set(range(min_val, max_val+1))
        "1,3,5" → {1, 3, 5}
    """
    if part == "*":
        return set(range(min_val, max_val + 1))
    values = set()
    for item in part.split(","):
        item = item.strip()
        if "-" in item:
            start, end = item.split("-")
            values.update(range(int(start), int(end) + 1))
        else:
            values.add(int(item))
    return values

def cron_next(expr: str, after: datetime) -> datetime:
    """计算 cron 表达式在 after 之后的下一次触发时间。
    如果 after 之后已经到触发时间，返回 after 本身。
    否则找到最近的下一个触发时刻。
    """
    parsed = _parse_cron(expr)
    # 简化的实现：检查 after 之后最近 24*60 分钟
    # 生产环境可用 croniter 库
    current = after.replace(second=0, microsecond=0)
    for _ in range(24 * 60):  # 最多扫描 24 小时
        if (current.minute in parsed["minute"]
                and current.hour in parsed["hour"]
                and current.day in parsed["day_of_month"]
                and current.month in parsed["month"]
                and current.weekday() in parsed["day_of_week"]):
            return current
        current += timedelta(minutes=1)
    return after + timedelta(days=1)  # 兜底

def is_due(task: ScheduledTask, now: datetime | None = None) -> bool:
    """判断任务是否应该触发。"""
    if not task.enabled:
        return False
    now = now or datetime.now()
    if task.last_run is None:
        return True
    last_run = datetime.fromisoformat(task.last_run)
    return cron_next(task.schedule, last_run) <= now
```

为什么自己实现而不直接用 croniter 库：减少外部依赖。Phase 4 中用纯 Python 实现基础 cron 解析（支持 `*`、数字、逗号列表、范围），后续 Phase 5 再考虑切换到 croniter 支持更复杂的表达式（步进 `*/5`、星期简写等）。当前需求（如 `"0 9 * * *"` 每天 9 点）都能处理。

**Step 4: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.scheduler.engine import Scheduler, is_due, cron_next
from nanoclaw.scheduler.repo import MemoryScheduledTaskRepo, ScheduledTask
from datetime import datetime, timedelta
import asyncio

# 验证 cron 计算
async def test():
    repo = MemoryScheduledTaskRepo()
    task = await repo.create(ScheduledTask(
        id='', user_id='default',
        description='test', prompt='hello',
        schedule='0 9 * * *',  # 每天 9 点
        created_at=datetime.now().timestamp(),
        last_run=(datetime.now() - timedelta(hours=2)).isoformat(),
        enabled=True,
    ))
    now = datetime.now().replace(hour=9, minute=0, second=0)
    assert is_due(task, now) == True
    now2 = now.replace(hour=8, minute=0)
    assert is_due(task, now2) == False
    print('Scheduler logic OK')

asyncio.run(test())
"
```

**Step 5: 提交**

```bash
git add backend/src/nanoclaw/scheduler/engine.py
git commit -m "feat: implement Scheduler daemon with cron expression support"
```

---

### Task 6：Dreaming Cron 触发 + 应用集成

**文件**：
- 创建：`backend/src/nanoclaw/dreaming/cron.py` — Dreaming 定时触发
- 修改：`backend/src/nanoclaw/server/app.py` — 集成 Scheduler + Dreaming 到应用生命周期
- 修改：`backend/src/nanoclaw/main.py` — 启动时初始化依赖

**原因**：
Dreaming Engine 和 Scheduler 都需要在应用启动时初始化并注册为后台任务。Dreaming 本身也是一个"特殊的定时任务"——每天 02:00 触发，但它写死在代码中（不可由用户管理）。

**Step 1: 实现 DreamingCronTrigger**

```python
class DreamingCronTrigger:
    """Dreaming 定时触发 — 每天 02:00 执行梦境处理。

    这不是一个用户可配置的 ScheduledTask，而是系统级内置任务。
    它独立于 Scheduler 运行，因为：
    1. 不需要持久化到 ScheduledTaskRepo
    2. 触发逻辑固定（每天 02:00）
    3. 不需要用户使能/禁用
    """

    def __init__(self, dreaming_engine: DreamingEngine) -> None:
        self.dreaming_engine = dreaming_engine
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """启动 Dreaming 定时器。"""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """每分钟检查一次是否到了 02:00。"""
        while self._running:
            now = datetime.now()
            if now.hour == 2 and 0 <= now.minute < 1:
                # 02:00-02:01 窗口内触发
                yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                logger.info("Starting daily dreaming for %s", yesterday)
                try:
                    summary = await self.dreaming_engine.run_dreaming(yesterday)
                    logger.info("Dreaming complete: %s", summary)
                except Exception as e:
                    logger.exception("Dreaming failed: %s", e)
                # 触发后休眠 1 小时避免同一窗口重复触发
                await asyncio.sleep(3600)
            await asyncio.sleep(60)
```

为什么独立于 Scheduler：两个原因。第一，Dreaming 的触发逻辑只有"每天 02:00"，复杂性远低于通用 cron 调度器，不需要通用化。第二，Dreaming 触发后本身会创建 TaskPlan 并推入 TaskQueue，如果也让 Scheduler 管理它会形成循环依赖。

为什么触发窗口是 `02:00-02:01`：避免在一分钟内多次触发。触发后休眠 3600 秒防止同一小时内重复触发。

**Step 2: 集成到 FastAPI 生命周期**

```python
# backend/src/nanoclaw/server/app.py — 新增生命周期管理

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动 Scheduler + Dreaming 后台任务。"""
    # 启动
    scheduler = app.state.scheduler
    dreaming_trigger = app.state.dreaming_trigger
    await scheduler.start()
    await dreaming_trigger.start()
    yield
    # 关闭
    await scheduler.stop()
    await dreaming_trigger.stop()

def create_app(
    scheduler: Scheduler | None = None,
    dreaming_trigger: DreamingCronTrigger | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Nanoclaw",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.scheduler = scheduler
    app.state.dreaming_trigger = dreaming_trigger
    ...
```

为什么通过 `app.state` 而不是全局变量：FastAPI 最佳实践——生命周期中创建的后台任务绑定到 app 实例，避免全局可变状态的维护问题。`app.state` 是线程安全的。

**Step 3: 添加手动触发 API 端点**

```python
# app.py 中新增

@app.post("/dream")
async def trigger_dreaming():
    """手动触发梦境处理。处理昨天的数据。"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    summary = await app.state.dreaming_engine.run_dreaming(yesterday)
    return {"status": "ok", "date": yesterday, "summary": summary}

@app.post("/dream/{date}")
async def trigger_dreaming_date(date: str):
    """处理指定日期的数据。"""
    summary = await app.state.dreaming_engine.run_dreaming(date)
    return {"status": "ok", "date": date, "summary": summary}
```

为什么允许手动指定日期：调试和测试需要。用户可以通过 `/dream 2026-06-07` 重新处理某天的数据，也支持 `/dream today` 处理今天到目前为止的数据。

**Step 4: 更新 main.py 初始化**

```python
# backend/src/nanoclaw/main.py

def create_dependencies() -> None:
    """创建所有全局依赖（Scheduler, DreamingEngine, etc.）。"""
    from nanoclaw.config import settings
    from nanoclaw.eval.logger import EventLogger
    from nanoclaw.scheduler.repo import MemoryScheduledTaskRepo
    from nanoclaw.scheduler.engine import Scheduler
    from nanoclaw.dreaming.engine import DreamingEngine
    from nanoclaw.dreaming.cron import DreamingCronTrigger

    eval_logger = EventLogger(f"{settings.db_path}/eval")  # 暂用 db_path 作为 base
    scheduled_task_repo = MemoryScheduledTaskRepo()
    # ... 其他依赖 ...

def main() -> None:
    app = create_app()
    uvicorn.run(...)
```

**Step 5: 验证**

```bash
# 测试手动触发端点
curl http://localhost:8420/dream
# 期望返回 {"status": "ok", "date": "...", "summary": {...}}
```

**Step 6: 提交**

```bash
git add backend/src/nanoclaw/dreaming/cron.py
git add backend/src/nanoclaw/server/app.py backend/src/nanoclaw/main.py
git commit -m "feat: integrate Dreaming and Scheduler into app lifecycle"
```

---

### Task 7：Scheduled Task API 端点

**文件**：
- 修改：`backend/src/nanoclaw/server/app.py` — 新增 `/schedules` CRUD 端点

**原因**：
TUI 的 `/schedule` 命令需要后端 API 支持。CRUD 操作通过 REST API 暴露，TUI 通过 HTTP 调用。这比直接在 TUI 中操作数据库/仓库更符合分层设计。

**Step 1: 新增 GET /schedules — 列出所有定时任务**

```python
@app.get("/schedules")
async def list_schedules(app: FastAPI = Depends(...)):
    """列出全部定时任务（用于 /schedule list）。"""
    scheduler = app.state.scheduler
    tasks = await scheduler.task_repo.list_all()
    return {
        "tasks": [
            {
                "id": t.id,
                "description": t.description,
                "schedule": t.schedule,
                "enabled": t.enabled,
                "last_run": t.last_run,
                "created_at": t.created_at,
            }
            for t in tasks
        ]
    }
```

**Step 2: 新增 POST /schedules — 创建定时任务**

```python
class CreateScheduleRequest(BaseModel):
    description: str
    prompt: str
    schedule: str       # cron 表达式
    enabled: bool = True

@app.post("/schedules")
async def create_schedule(req: CreateScheduleRequest):
    """创建定时任务（用于 /schedule add）。"""
    task = ScheduledTask(
        id="",  # repo 生成
        user_id="default",
        description=req.description,
        prompt=req.prompt,
        schedule=req.schedule,
        enabled=req.enabled,
        created_at=datetime.now().timestamp(),
    )
    created = await app.state.scheduler.task_repo.create(task)
    return {"status": "ok", "task": created}
```

**Step 3: 新增 DELETE /schedules/{id} — 删除定时任务**

```python
@app.delete("/schedules/{task_id}")
async def delete_schedule(task_id: str):
    """删除定时任务（用于 /schedule remove）。"""
    await app.state.scheduler.task_repo.delete(task_id)
    return {"status": "ok"}
```

**Step 4: 新增 PATCH /schedules/{id}/toggle — 启用/禁用**

```python
@app.patch("/schedules/{task_id}/toggle")
async def toggle_schedule(task_id: str):
    """切换定时任务的启用/禁用状态。"""
    task = await app.state.scheduler.task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    updated = await app.state.scheduler.task_repo.update(
        task_id, {"enabled": not task.enabled}
    )
    return {"status": "ok", "task": updated}
```

为什么用 `PATCH /toggle` 而不是通用 `PUT /schedules/{id}`：TUI 的 `/schedule toggle` 命令只需要切换 enabled。一个专门的 toggle 端点比通用 update 更精确，也不需要考虑部分更新的幂等性问题。未来需要完整编辑时再加 PUT。

**Step 5: 验证**

```bash
# 创建
curl -X POST http://localhost:8420/schedules \
  -H "Content-Type: application/json" \
  -d '{"description": "每日检查", "prompt": "检查今天的时间表", "schedule": "0 9 * * *"}'
# 列出
curl http://localhost:8420/schedules
# 切换
curl -X PATCH http://localhost:8420/schedules/sched_0001/toggle
# 删除
curl -X DELETE http://localhost:8420/schedules/sched_0001
```

**Step 6: 提交**

```bash
git add backend/src/nanoclaw/server/app.py
git commit -m "feat: add /schedules CRUD API endpoints for scheduled tasks"
```

---

### Task 8：TUI `/schedule` 命令

**文件**：
- 修改：`cli/src/types.ts` — 添加 ScheduledTask 类型

**原因**：
用户通过命令行管理定时任务。TUI 模式下通过 `/schedule` 系列命令交互，CLI 模式下（`src/index.ts`）也应该支持同样的命令。

**Step 1: 扩展 types.ts**

```typescript
// 追加到现有类型文件

export interface ScheduledTask {
  id: string
  description: string
  prompt: string
  schedule: string
  enabled: boolean
  last_run: string | null
  created_at: number
}

export interface CreateScheduleRequest {
  description: string
  prompt: string
  schedule: string
  enabled?: boolean
}

export interface ScheduleListResponse {
  tasks: ScheduledTask[]
}
```

**Step 2: 扩展 client.ts**

```typescript
// 追加到现有 client.ts

export async function listSchedules(baseUrl: string): Promise<ScheduleListResponse> {
  const res = await fetch(`${baseUrl}/schedules`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function createSchedule(
  baseUrl: string,
  req: CreateScheduleRequest
): Promise<{status: string; task: ScheduledTask}> {
  const res = await fetch(`${baseUrl}/schedules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function deleteSchedule(baseUrl: string, taskId: string): Promise<void> {
  const res = await fetch(`${baseUrl}/schedules/${taskId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

export async function toggleSchedule(baseUrl: string, taskId: string): Promise<{status: string}> {
  const res = await fetch(`${baseUrl}/schedules/${taskId}/toggle`, { method: "PATCH" })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
```

**Step 3: 修改 app.tsx — 添加命令路由**

```typescript
// app.tsx — 在 handleSubmit 中添加命令路由

const handleSubmit = async (value: string) => {
  if (value === "/exit") {
    setExiting(true)
    setTimeout(() => process.exit(0), 100)
    return
  }

  // 命令路由器
  if (value.startsWith("/schedule")) {
    await handleScheduleCommand(config.baseUrl, value)
    return  // 不触发消息流
  }

  if (value === "/dream") {
    await triggerDreaming(config.baseUrl)
    return
  }

  // 普通消息流不变
  setMessages([...messages, { content: value, role: "user" }])
  setStreamingMsg(value)
  setInput("")
}
```

为什么要拦截 `/schedule` 命令：定时任务管理是配置操作，不是对话交互。不应该触发 ReAct Agent 执行。命令路由器在 handleSubmit 开头判断、处理、返回，不经过消息流。

**Step 4: 实现命令处理函数**

```typescript
async function handleScheduleCommand(baseUrl: string, cmd: string) {
  const parts = cmd.trim().split(/\s+/)
  const subcmd = parts[1]

  switch (subcmd) {
    case "list": {
      const resp = await listSchedules(baseUrl)
      if (resp.tasks.length === 0) {
        console.log("No scheduled tasks.")
        return
      }
      for (const t of resp.tasks) {
        const status = t.enabled ? "ON" : "OFF"
        console.log(
          `${t.id.padEnd(12)} ${status.padEnd(4)} ${t.schedule.padEnd(12)} ${t.description}`
        )
        if (t.last_run) console.log(`  last run: ${t.last_run}`)
      }
      break
    }

    case "add": {
      // /schedule add "0 9 * * *" "检查邮件并总结"  -- 需要解析引号或按顺序
      // 简化：/schedule add <schedule> <description> <prompt>
      // 因为 prompt 可能很长，建议交互式输入或使用 createSchedule API 的 JSON 格式
      if (parts.length < 4) {
        console.log("Usage: /schedule add <cron_expr> <description> <prompt>")
        return
      }
      const schedule = parts[2]
      const description = parts[3]
      const prompt = parts.slice(4).join(" ") || description
      const resp = await createSchedule(baseUrl, { description, prompt, schedule })
      console.log(`Created schedule: ${resp.task.id}`)
      break
    }

    case "remove": {
      if (!parts[2]) {
        console.log("Usage: /schedule remove <id>")
        return
      }
      await deleteSchedule(baseUrl, parts[2])
      console.log(`Removed: ${parts[2]}`)
      break
    }

    case "toggle": {
      if (!parts[2]) {
        console.log("Usage: /schedule toggle <id>")
        return
      }
      const resp = await toggleSchedule(baseUrl, parts[2])
      console.log(`Toggled: ${parts[2]}`)
      break
    }

    default:
      console.log("Commands: list, add, remove, toggle")
  }
}
```

**Step 5: 实现 /dream 命令**

```typescript
async function triggerDreaming(baseUrl: string) {
  console.log("Triggering dreaming...")
  try {
    const res = await fetch(`${baseUrl}/dream`, { method: "POST" })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    console.log(`Dreaming complete for ${data.date}`)
  } catch (e) {
    console.error(`Dreaming failed: ${e}`)
  }
}
```

为什么 `/dream` 命令也放在 app.tsx 而不是 index.ts：TUI 模式下用户直接输入命令，CLI 模式（index.ts）目前是逐行输入循环，也可以增加同样的命令路由。但 Phase 4 优先覆盖 TUI 模式。

**Step 6: 验证**

```bash
make app  # 启动 TUI

# 在 TUI 中输入：
/schedule list
# 期望: "No scheduled tasks."

/schedule add "0 9 * * *" "每日总结" "请总结今天的进展"
# 期望: "Created schedule: sched_0001"

/schedule list
# 期望: 看到 sched_0001

/schedule toggle sched_0001
# 期望: "Toggled: sched_0001"

/dream
# 期望: "Dreaming complete for 2026-06-07"
```

**Step 7: 提交**

```bash
git add cli/src/types.ts cli/src/client.ts cli/src/app.tsx
git commit -m "feat: add /schedule and /dream commands to TUI"
```

---

### Task 9：后端 Config 扩展 + eval/memory Store 集成

**文件**：
- 修改：`backend/src/nanoclaw/config.py` — 扩展路径配置
- 创建：`backend/src/nanoclaw/memory/store.py` — MemoryStore 抽象 + MemoryStore Mock
- 需要确认：`backend/pyproject.toml` — 是否需要添加依赖

**原因**：
Dreaming Engine 需要 `eval_logger` 和 `memory_store` 作为构造参数。Phase 4 之前这些组件不存在或只有空文件。必须实现。

**Step 1: 扩展 Settings**

```python
class Settings(BaseSettings):
    model_config = {"env_prefix": "NANOCLAW_"}

    # ... 现有字段 ...

    # Phase 4: 持久化路径
    nanoclaw_home: str = ".nanoclaw"  # 相对于 cwd

    @property
    def eval_dir(self) -> str:
        return f"{self.nanoclaw_home}/eval"

    @property
    def memory_dir(self) -> str:
        return f"{self.nanoclaw_home}/memory"

    @property
    def dreams_dir(self) -> str:
        return f"{self.nanoclaw_home}/dreams"
```

为什么用 `@property` 而非字段：目录路径是派生字段，从 `nanoclaw_home` 计算得到。用户只配置根路径，子目录名固定。这保持了配置的简洁性。

**Step 2: 实现 MemoryStore 抽象 + Mock 实现**

```python
# backend/src/nanoclaw/memory/store.py

from abc import ABC, abstractmethod
from nanoclaw.memory.types import MemoryEntry

class MemoryStore(ABC):
    """长期记忆存储 — 跨 session 持久化。"""

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None:
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        ...

    @abstractmethod
    async def list_unconfirmed(self) -> list[MemoryEntry]:
        """列出所有未确认的 reflection 草稿（Dreaming 记忆固化使用）。"""
        ...


class JsonFileMemoryStore(MemoryStore):
    """Phase 4 实现：JSON 文件存储。每类记忆一个 JSON 文件。
    后续 Phase 5 替换为 Chroma + PG。"""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[MemoryEntry] = []
        self._load()

    async def save(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)
        self._persist()

    async def search(self, query, tags=None, top_k=5) -> list[MemoryEntry]:
        # 简单实现：按 tag 过滤 + 关键词匹配（不用 Chroma）
        candidates = self._entries
        if tags:
            candidates = [e for e in candidates if any(t in e.tags for t in tags)]
        # 按置信度排序
        candidates.sort(key=lambda e: e.confidence, reverse=True)
        return candidates[:top_k]

    def _persist(self) -> None:
        import json
        path = self.base_dir / "memory.json"
        data = [asdict(e) for e in self._entries]
        path.write_text(json.dumps(data, indent=2, default=str))

    def _load(self) -> None:
        import json
        path = self.base_dir / "memory.json"
        if path.exists():
            data = json.loads(path.read_text())
            self._entries = [MemoryEntry(**d) for d in data]
```

为什么 Phase 4 用 JSON 文件而非 Chroma：Chroma 集成是 Phase 3 的工作。为让 Phase 4 独立可运行，先用 JSON 文件持久化。`search()` 使用关键词匹配 + 置信度排序的简化方案，效果不如 Chroma 的向量语义搜索但功能等价。后续切换到 Chroma 时只需替换 MemoryStore 实现。

**Step 3: 验证**

```bash
cd backend && uv run python -c "
from nanoclaw.config import settings
from nanoclaw.memory.store import JsonFileMemoryStore, MemoryEntry
from nanoclaw.memory.types import MemoryType
import asyncio, tempfile

async def test():
    store = JsonFileMemoryStore('/tmp/nanoclaw_test_memory')
    await store.save(MemoryEntry(
        id='test1', type='skill', tags=['python'],
        content='test skill', embedding=None,
        source='test', confidence=0.9,
        created_at=1234.0, confirmed=True,
    ))
    results = await store.search('test', tags=['python'])
    assert len(results) == 1
    print('MemoryStore OK')

asyncio.run(test())
"
```

**Step 4: 提交**

```bash
git add backend/src/nanoclaw/config.py backend/src/nanoclaw/memory/store.py
git commit -m "feat: extend config with eval/memory paths, implement JsonFileMemoryStore"
```

---

### Task 10：单元测试

**文件**：
- 创建：`backend/tests/test_scheduler_repo.py`
- 创建：`backend/tests/test_scheduler_engine.py`
- 创建：`backend/tests/test_dreaming_engine.py`
- 创建：`backend/tests/test_dreaming_tools.py`

**原因**：
Scheduler 的 cron 计算、Dreaming 的工具链指纹提取算法、ScheduledTaskRepo 的 `get_due_tasks` 都包含复杂的逻辑路径，必须有单元测试覆盖边界条件。

**Step 1: test_scheduler_repo.py**

```python
"""测试 ScheduledTaskRepo 的 get_due_tasks 逻辑。"""

async def test_due_task_never_run() -> None:
    """从未运行过的 enabled 任务应该立即触发。"""
    repo = MemoryScheduledTaskRepo()
    task = await repo.create(ScheduledTask(
        ..., last_run=None, enabled=True,
        schedule="0 9 * * *",
    ))
    due = await repo.get_due_tasks()
    assert len(due) == 1

async def test_due_task_already_run() -> None:
    """刚运行过的任务不应该立即触发。"""
    repo = MemoryScheduledTaskRepo()
    task = await repo.create(ScheduledTask(
        ..., last_run=datetime.now().isoformat(), enabled=True,
        schedule="0 9 * * *",  # 每天 9 点
    ))
    due = await repo.get_due_tasks()
    assert len(due) == 0  # 今天已经运行过了

async def test_disabled_task_not_due() -> None:
    """禁用的任务不应该触发。"""
    ...

async def test_cron_every_minute() -> None:
    """* * * * * 应该每分钟都触发。"""
    ...
```

**Step 2: test_scheduler_engine.py**

```python
"""测试 Scheduler 的 cron 计算。"""

def test_cron_parse_star() -> None:
    result = _parse_cron("* * * * *")
    assert result["minute"] == set(range(60))

def test_cron_parse_specific() -> None:
    result = _parse_cron("0 9 * * *")
    assert result["minute"] == {0}
    assert result["hour"] == {9}

def test_cron_parse_list() -> None:
    result = _parse_cron("0,30 9,18 * * *")
    assert result["minute"] == {0, 30}
    assert result["hour"] == {9, 18}

def test_cron_next_basic() -> None:
    """0 9 * * * 的下一次触发应该在第二天的 09:00。"""
    after = datetime(2026, 6, 8, 10, 0)
    next_time = cron_next("0 9 * * *", after)
    assert next_time == datetime(2026, 6, 9, 9, 0)

def test_cron_next_same_day() -> None:
    """如果 after 在 09:00 之前，应该在同一天触发。"""
    after = datetime(2026, 6, 8, 8, 0)
    next_time = cron_next("0 9 * * *", after)
    assert next_time == datetime(2026, 6, 8, 9, 0)
```

**Step 3: test_dreaming_engine.py**

```python
"""测试 Dreaming Engine 的工具链指纹提取。"""

def test_extract_tool_chains_empty() -> None:
    """空事件列表返回空字典。"""
    assert _extract_tool_chains([]) == {}

def test_extract_tool_chains_single_session() -> None:
    """同一个 session 内连续的工具调用应该被正确提取。"""
    events = [
        {"event_type": "tool_call", "session_id": "s1", "data": {"tool_name": "read_file"}, "timestamp": 1},
        {"event_type": "tool_call", "session_id": "s1", "data": {"tool_name": "grep"}, "timestamp": 2},
        {"event_type": "tool_call", "session_id": "s1", "data": {"tool_name": "file_edit"}, "timestamp": 3},
    ]
    chains = _extract_tool_chains(events, chain_length=2)
    assert ("read_file", "grep") in chains
    assert ("grep", "file_edit") in chains
    assert chains[("read_file", "grep")] == 1

def test_extract_tool_chains_cross_session() -> None:
    """跨 session 同一模式出现多次应该被正确统计。"""
    ...
```

**Step 4: test_dreaming_tools.py**

```python
"""测试 Dreaming 工具的 spec 定义。"""

def test_read_eval_logs_spec() -> None:
    tool = ReadEvalLogsTool.__new__(ReadEvalLogsTool)
    assert tool.spec.name == "read_eval_logs"
    assert "date" in tool.spec.parameters["properties"]

def test_write_memory_spec() -> None:
    tool = WriteMemoryTool.__new__(WriteMemoryTool)
    assert tool.spec.name == "write_memory"
    assert tool.spec.parameters["required"] == ["type", "content"]
```

**Step 5: 运行测试**

```bash
cd backend && uv run pytest tests/ -v
```

**Step 6: 提交**

```bash
git add backend/tests/
git commit -m "test: add unit tests for Phase 4 scheduler, dreaming engine, and tools"
```

---

### Phase 4 完成检查清单

- [ ] Evaluation 数据模型定义（EventLogger, 事件类型常量）
- [ ] MemoryEntry 类型定义（MemoryType, MemoryEntry）
- [ ] ScheduledTask 数据模型定义
- [ ] ScheduledTaskRepo 抽象 + MemoryScheduledTaskRepo 实现
- [ ] PgScheduledTaskRepo stub（接口声明 + NotImplementedError）
- [ ] Dreaming Agent 工具集（read_eval_logs, write_memory, read_memory, llm_analyze）
- [ ] DreamingEngine 主流程（run_dreaming 方法的 6 个步骤）
- [ ] 工具调用链指纹提取算法（_extract_tool_chains）
- [ ] Scheduler 守护循环（60 秒轮询）
- [ ] cron 表达式解析 + `is_due()` 计算
- [ ] DreamingCronTrigger（每天 02:00 自动触发）
- [ ] Scheduler + Dreaming 集成到 FastAPI 生命周期
- [ ] 手动触发 API（POST /dream, POST /dream/{date}）
- [ ] ScheduledTask CRUD API（GET/POST/DELETE/PATCH）
- [ ] TUI `/schedule list/add/remove/toggle` 命令
- [ ] TUI `/dream` 命令
- [ ] JsonFileMemoryStore 实现
- [ ] Config 路径扩展（eval_dir, memory_dir, dreams_dir）
- [ ] 单元测试覆盖（cron 计算、工具链提取、due task 判定）
