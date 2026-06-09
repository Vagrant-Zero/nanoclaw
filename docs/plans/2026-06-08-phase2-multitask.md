# Phase 2：多任务（多任务分解与并行执行）实施计划

> **致 Claude：** 必需的子技能：使用 superpowers:executing-plans 逐任务执行此计划。

**目标：** 将 Phase 1 的单路径 ReAct 图扩展为完整的管理器-工作器（Supervisor-Worker）架构，能够将复杂的用户请求分解为子任务的 DAG（有向无环图），分发给并行工作器，并收集结果。

**架构：** Phase 1 的图（router -> react_node -> output）变为"简单"路径。新增一条"复杂"路径：router -> planner -> dispatch -> (通过 TaskQueue 的工作器池) -> await -> collect -> output。Router 节点以启发式关键字匹配为起点，仅在模糊不清时回退到 LLM。TaskQueue 在内部管理 DAG 依赖关系，因此工作器无需检查 `depends_on`。工作器运行独立的 ReAct 子图（复用 Phase 1 的 `create_react_agent`）。完整的管理器图使用 LangGraph 构建，带有路由器条件边和复杂路径的顺序节点。

**前置条件：** 在开始 Phase 2 之前，Phase 1 必须完全实现。具体包括：
- `models/chat.py`, `models/task.py` -- data models (Subtask, TaskStatus, TaskPlan, Step, etc.)
- `storage/session_repo.py` -- MemorySessionRepo
- `storage/task_queue.py` -- MemoryQueue (DAG-aware)
- `storage/task_repo.py` -- MemoryTaskRepo
- `agent/state.py` -- extended AgentState with session/tool fields
- `agent/nodes/react_agent.py` -- `create_react_agent()` factory
- `agent/supervisor_graph.py` -- Phase 1 simple-path supervisor
- `server/deps.py` -- FastAPI dependency injection
- TUI: ThinkingBlock, ToolCallCard, SSE event handling in app.tsx

**技术栈：** Python 3.12+、LangGraph（StateGraph、CompiledStateGraph）、LangChain、asyncio、FastAPI/SSE、Ink（TUI）

**参考：** `docs/plans/2026-06-08-agent-architecture-design.md`——管理器图结构、工作器池设计、TaskQueue DAG 逻辑、SSE 事件协议

---

### Task 0：验证 Phase 1 完成情况

**文件：** 无（仅验证）

**原因：** Phase 2 依赖于 Phase 1 的类、存储和图工厂。如果 Phase 1 不完整，这些导入将在运行时失败。

**第 1 步：验证所有 Phase 1 文件存在**

依次运行以下命令。如果任何命令失败，必须先完成 Phase 1。

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend

# Models
uv run python -c "from nanoclaw.models.chat import ChatMessage, Step; from nanoclaw.models.task import TaskStatus, Subtask, TaskPlan; print('Models OK')"

# Storage
uv run python -c "from nanoclaw.storage.session_repo import MemorySessionRepo; from nanoclaw.storage.task_queue import MemoryQueue; print('Storage OK')"

# Agent state
uv run python -c "from nanoclaw.agent.state import AgentState; print('State OK')"

# ReAct agent factory
uv run python -c "from nanoclaw.agent.nodes.react_agent import create_react_agent; print('ReAct OK')"

# Supervisor simple path
uv run python -c "from nanoclaw.agent.supervisor_graph import create_supervisor; print('Supervisor OK')"
```

预期：所有五个命令打印 "OK"。

**第 2 步：验证 TUI 构建**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw && npx tsc --noEmit
```

预期：无错误。

**第 3 步：Phase 1 基础提交存在**

```bash
git log --oneline -5
```

确认 Phase 1 相关代码已提交（至少包含 models、storage、ReAct、supervisor、SSE 连接）。

---

### Task 1：扩展 AgentState 以支持多任务执行

**文件：**
- 修改：`backend/src/nanoclaw/agent/state.py`

**原因：** Phase 2 的管理器图需要额外的状态字段，而 Phase 1 的简单 `AgentState` 不包含这些字段：当前的 `TaskPlan`、对 `TaskQueue` 的引用、工作器池实例以及收集器结果缓冲区。这些字段被 planner、dispatch、await 和 collect 节点所需。

**第 1 步：定义 SupervisorState**

SupervisorState 扩展 AgentState 的字段并添加多任务管理字段：

```python
class SupervisorState(TypedDict):
    """State for the full Supervisor graph (Phase 2)."""

    # Phase 1 fields (carried forward)
    messages: Annotated[Sequence[AnyMessage], add_messages]
    tool_registry: ToolRegistry | None
    session_id: str | None
    session_repo: SessionRepository | None

    # Phase 2 new fields
    task_queue: TaskQueue | None         # DAG-aware task queue
    plan: TaskPlan | None                # Current execution plan (after planner)
    worker_pool: WorkerPool | None       # Worker pool instance
    worker_results: dict[str, str] | None  # task_id -> result (populated by collect)
    errors: list[str] | None             # Error messages accumulated during execution
```

注意：从 `nanoclaw.tools.registry` 导入 `ToolRegistry`，从 `nanoclaw.storage.session_repo` 导入 `SessionRepository`，从 `nanoclaw.storage.task_queue` 导入 `TaskQueue`，从 `nanoclaw.models.task` 导入 `TaskPlan`，以及 `WorkerPool`（将在 Task 5 中定义 — 使用 `TYPE_CHECKING` 保护实现前向引用）。

这些字段的原因：
- `task_queue` 持有活动的 MemoryQueue 实例，以便 dispatch/await/collect 节点可以调用 `init_plan()`、`dequeue()`、`wait_for_all()`
- `plan` 是 planner 节点的输出，由 dispatch 使用
- `worker_pool` 控制池的生命周期（启动、停止）
- `worker_results` 由 collect 节点在 await 返回后填充
- `errors` 累积失败信息，用于最终输出中的错误报告

**第 2 步：验证**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "from nanoclaw.agent.state import SupervisorState; print('SupervisorState OK')"
```

预期：导入成功。（如果 WorkerPool 导入失败，使用 `TYPE_CHECKING` 保护。）

**第 3 步：提交**

```bash
git add backend/src/nanoclaw/agent/state.py
git commit -m "feat: add SupervisorState with multi-task fields for Phase 2"
```

---

### Task 2：实现 Router 节点（启发式 + LLM 回退）

**文件：**
- 创建：`backend/src/nanoclaw/agent/nodes/router.py`

**原因：** Router 决定用户请求是"简单"的（直接 ReAct 响应）还是"复杂"的（需要通过 planner 进行任务分解）。设计文档要求以启发式方法优先，LLM 回退为辅助，以避免在像"hello"或"what time is it"这样的明显查询上浪费 token。

**第 1 步：定义路由器决策逻辑**

路由器函数签名：

```python
from typing import Literal
from nanoclaw.agent.state import SupervisorState

RouteDecision = Literal["react", "plan"]

async def router_node(state: SupervisorState) -> RouteDecision:
    """Determine whether the request is simple or complex.

    Strategy:
    1. Extract last user message from state["messages"]
    2. Run heuristic rules (keywords + message length)
    3. If heuristic is clear (above/below threshold), return immediately
    4. If ambiguous (heuristic result within fuzzy zone), call LLM to classify
    """
```

需实现的启发式规则：
- 简单触发器：问候语、短问题（< 15 字符）、时间/日期查询、是否问题
- 复杂触发器：关键词如 "analyze", "compare", "investigate", "research", "plan", "总结", "分析", "比较", "规划" 且消息长度 > 20 字符
- 模糊区域：消息长度 15-30 字符，无明确复杂关键词 -> LLM 回退
- LLM 回退：调用 `llm.ainvoke()`，使用一次性分类提示，询问"这个请求是简单还是复杂？回答'simple'或'complex'。"

LLM 实例作为参数传递给 `create_router_node()`：

```python
def create_router_node(llm: Any) -> Callable[[SupervisorState], Awaitable[RouteDecision]]:
    async def router_node(state: SupervisorState) -> RouteDecision:
        # ... implementation ...
    return router_node
```

为什么使用工厂函数：LLM 实例在启动时创建（在 deps.py 或 main.py 中），而不是在图内部。工厂将其捕获为闭包，保持节点函数签名与 LangGraph 的 `add_node` 兼容。

**第 2 步：实现简单/复杂关键词列表和启发式检查**

定义为模块级常量，便于维护：

```python
_SIMPLE_KEYWORDS = ["hello", "hi", "hey", "good morning", "good evening",
                    "what time", "what's the time", "date", "who are you",
                    "thanks", "thank you", "bye", "goodbye", "yes", "no"]

_COMPLEX_KEYWORDS = ["analyze", "analyse", "compare", "investigate", "research",
                     "survey", "explore", "plan", "design", "build", "create",
                     "develop", "implement", "refactor", "debug", "fix",
                     "optimize", "migrate", "总结", "分析", "比较", "规划",
                     "设计", "实现", "调查", "研究", "explain in detail"]
```

启发式逻辑：
```
content = last_message.content.strip().lower()
if any(content.startswith(kw) or content == kw for kw in _SIMPLE_KEYWORDS):
    return "react"
if len(content) < 15:
    return "react"
if any(kw in content for kw in _COMPLEX_KEYWORDS):
    return "plan"
# Else: fuzzy zone -> LLM fallback
```

**第 3 步：为模糊情况实现 LLM 回退**

当启发式方法无法决定时（消息长度足够但缺少明确的复杂关键词），调用 LLM：

```python
import json
from langchain_core.messages import SystemMessage, HumanMessage

fallback_prompt = SystemMessage(
    content="""You are a request classifier. Determine if the user's request is 'simple' or 'complex'.

Simple: greetings, time/date queries, short factual questions, yes/no questions, thank-yous.
Complex: multi-step tasks, analysis, comparison, research, planning, implementation, debugging.

Respond with ONLY a JSON object: {"decision": "simple"} or {"decision": "complex"}. No other text."""
)

response = await llm.ainvoke([fallback_prompt, HumanMessage(content=content)])
try:
    result = json.loads(response.content)
    return result["decision"]
except (json.JSONDecodeError, KeyError):
    # Fallback: if LLM can't parse, default to simple (safe path)
    return "react"
```

为什么仅在模糊时使用 LLM 回退：避免在琐碎分类上花费 token。启发式方法能处理约 80% 的情况。

**第 4 步：验证**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "
from nanoclaw.agent.nodes.router import create_router_node, _SIMPLE_KEYWORDS, _COMPLEX_KEYWORDS
print(f'Simple keywords: {len(_SIMPLE_KEYWORDS)}, Complex keywords: {len(_COMPLEX_KEYWORDS)}')
print('Router OK')
"
```

预期：打印关键词数量及 "Router OK"。

**第 5 步：提交**

```bash
git add backend/src/nanoclaw/agent/nodes/router.py
git commit -m "feat: add router node with heuristic-first, LLM-fallback strategy"
```

---

### Task 3：实现 Planner 节点（LLM 任务分解）

**文件：**
- 创建：`backend/src/nanoclaw/agent/nodes/planner.py`

**原因：** Planner 是多任务系统的核心。它将复杂的用户请求分解为结构化的子任务 DAG，包含明确的依赖关系、工具需求和补偿操作。

**第 1 步：定义 planner 节点函数**

Planner 接收用户请求（来自消息）和可用工具列表，并生成一个 `TaskPlan`：

```python
from nanoclaw.models.task import TaskPlan, Subtask, TaskStatus

def create_planner_node(
    llm: Any,
    tool_registry: ToolRegistry,
) -> Callable[[SupervisorState], Awaitable[dict]]:
    """Factory: creates a planner node that decomposes requests into subtask DAGs."""

    async def planner_node(state: SupervisorState) -> dict:
        # 1. Extract last user message
        # 2. Get available tool descriptions from tool_registry.list()
        # 3. Build system prompt instructing the LLM to output a JSON plan
        # 4. Call llm.ainvoke()
        # 5. Parse JSON response into TaskPlan
        # 6. Call validate_plan() (Task 4) -- return errors if invalid
        # 7. Return {"plan": validated_plan}
        ...
    return planner_node
```

输出字典通过 reducer 模式（LangGraph 合并字典）合并到 SupervisorState 中。

**第 2 步：设计用于计划生成的 LLM 提示**

系统提示必须仔细指导 LLM 输出结构化的 JSON 计划。关键指令：

```
You are a task planner. Decompose the user's request into subtasks.

Rules:
1. Each subtask MUST have: id ("task_001", "task_002", ...), description, depends_on list, tools_needed list
2. Dependencies: if task_B needs task_A's output, task_B.depends_on = ["task_001"]
3. First subtasks have empty depends_on (run immediately)
4. Maximum 8 subtasks per plan
5. Each subtask SHOULD be self-contained (one logical unit of work)
6. tools_needed lists tool names from the available tool list (provide it)
7. compensation: a shell command or action to UNDO the subtask's side effects (e.g., "rm -f path/to/created/file")
   - Can be null if the subtask has no side effects (e.g., read-only operations)
8. Output format: valid JSON array of subtask objects

Available tools:
{list tool specs here}
```

为什么需要明确的 DAG 约束：没有这些约束，LLM 倾向于生成没有依赖关系的扁平列表，从而错过并行化的机会。

**第 3 步：将 LLM 响应解析为 TaskPlan**

```python
import json, uuid

async def planner_node(state: SupervisorState) -> dict:
    content = state["messages"][-1].content
    tools_info = state["tool_registry"].list()

    # Build prompt
    system_msg = _build_planner_prompt(tools_info)
    human_msg = HumanMessage(content=content)

    response = await llm.ainvoke([system_msg, human_msg])

    # Parse and validate
    try:
        subtasks_data = json.loads(response.content)
        if isinstance(subtasks_data, dict) and "subtasks" in subtasks_data:
            subtasks_data = subtasks_data["subtasks"]
    except json.JSONDecodeError as e:
        return {"plan": None, "errors": [f"Planner JSON parse error: {e}"]}

    subtasks = []
    for item in subtasks_data:
        subtasks.append(Subtask(
            id=item["id"],
            description=item["description"],
            status=TaskStatus.PENDING,
            depends_on=item.get("depends_on", []),
            tools_needed=item.get("tools_needed", []),
            trace=[],
            compensation=item.get("compensation"),
            max_retries=3,
            retry_count=0,
            result=None,
            output_files=[],
            error=None,
        ))

    plan = TaskPlan(
        session_id=state.get("session_id") or "unknown",
        subtasks=subtasks,
    )
    return {"plan": plan}
```

**第 4 步：验证**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "
from nanoclaw.agent.nodes.planner import create_planner_node
from nanoclaw.tools.registry import ToolRegistry
print('Planner module OK')
"
```

预期：导入成功。

**第 5 步：提交**

```bash
git add backend/src/nanoclaw/agent/nodes/planner.py
git commit -m "feat: add planner node with LLM task decomposition"
```

---

### Task 4：添加计划验证工具（`validate_plan`）

**文件：**
- 创建：`backend/src/nanoclaw/agent/nodes/validate.py`（或附加到 planner.py）

**原因：** LLM 生成的计划可能存在缺陷：循环依赖（A 依赖 B，B 依赖 A）、`depends_on` 中的无效任务 ID，或格式错误的 JSON。这些必须在调度之前捕获，以避免运行时死锁或崩溃。

**第 1 步：实现 validate_plan()**

```python
from nanoclaw.models.task import TaskPlan, Subtask
from nanoclaw.models.task import TaskStatus

def validate_plan(plan: TaskPlan) -> list[str]:
    """Validate a TaskPlan and return a list of error messages.
    Returns empty list if valid.

    Checks:
    1. At least one subtask exists
    2. All subtask IDs are unique
    3. Every depends_on reference points to an existing subtask ID
    4. No cycles in dependency graph (DFS-based cycle detection)
    5. No subtask depends on itself
    """
    errors: list[str] = []

    if not plan.subtasks:
        return ["Plan has no subtasks"]

    # Check unique IDs
    ids = [s.id for s in plan.subtasks]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate subtask IDs found")

    id_set = set(ids)

    # Check reference integrity
    for s in plan.subtasks:
        for dep in s.depends_on:
            if dep not in id_set:
                errors.append(f"Subtask {s.id} depends on unknown task {dep}")
            if dep == s.id:
                errors.append(f"Subtask {s.id} depends on itself")

    # Cycle detection using DFS
    # Build adjacency list: task_id -> list of task_ids that depend on it (reverse graph)
    # Or: for cycle detection, we check if there's a path from a node back to itself
    # Use standard DFS with three-color marking (white/gray/black)
    adj: dict[str, list[str]] = {s.id: [] for s in plan.subtasks}
    for s in plan.subtasks:
        for dep in s.depends_on:
            if dep in id_set:
                adj[dep].append(s.id)  # dep -> s (dep must complete before s)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in id_set}

    def dfs_cycle(node: str) -> bool:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if color[neighbor] == GRAY:
                return True  # Back edge = cycle
            if color[neighbor] == WHITE and dfs_cycle(neighbor):
                return True
        color[node] = BLACK
        return False

    for tid in id_set:
        if color[tid] == WHITE and dfs_cycle(tid):
            errors.append(f"Cycle detected involving task {tid}")
            break  # One cycle found is enough to fail validation

    return errors
```

为什么使用 DFS 循环检测：标准算法，O(V+E) 复杂度，足以处理小于 100 个子任务的计划。

**第 2 步：将 validate_plan() 集成到 planner 节点中**

在 `planner.py` 的 planner 节点内部，解析 LLM 响应之后：

```python
from nanoclaw.agent.nodes.validate import validate_plan

# After creating plan object
validation_errors = validate_plan(plan)
if validation_errors:
    return {"plan": None, "errors": validation_errors}

return {"plan": plan}
```

**第 3 步：验证**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "
from nanoclaw.agent.nodes.validate import validate_plan
from nanoclaw.models.task import TaskPlan, Subtask, TaskStatus

# Valid plan (no dependencies)
p1 = TaskPlan(session_id='s1', subtasks=[
    Subtask(id='task_001', description='a', status=TaskStatus.PENDING, depends_on=[], tools_needed=[], trace=[]),
    Subtask(id='task_002', description='b', status=TaskStatus.PENDING, depends_on=['task_001'], tools_needed=[], trace=[]),
])
assert validate_plan(p1) == [], f'Expected no errors, got {validate_plan(p1)}'

# Cycle
p2 = TaskPlan(session_id='s1', subtasks=[
    Subtask(id='task_001', description='a', status=TaskStatus.PENDING, depends_on=['task_002'], tools_needed=[], trace=[]),
    Subtask(id='task_002', description='b', status=TaskStatus.PENDING, depends_on=['task_001'], tools_needed=[], trace=[]),
])
assert len(validate_plan(p2)) > 0, 'Expected cycle error'

# Missing reference
p3 = TaskPlan(session_id='s1', subtasks=[
    Subtask(id='task_001', description='a', status=TaskStatus.PENDING, depends_on=['task_999'], tools_needed=[], trace=[]),
])
assert len(validate_plan(p3)) > 0, 'Expected reference error'

print('validate_plan tests passed')
"
```

预期：所有断言通过。

**第 4 步：提交**

```bash
git add backend/src/nanoclaw/agent/nodes/validate.py
git commit -m "feat: add validate_plan with cycle detection and reference integrity"
```

---

### Task 5：实现 Dispatch + Await + Collect 节点

**文件：**
- 创建：`backend/src/nanoclaw/agent/nodes/dispatch.py`
- 创建：`backend/src/nanoclaw/agent/nodes/await_node.py`
- 创建：`backend/src/nanoclaw/agent/nodes/collector.py`
- 创建：`backend/src/nanoclaw/agent/worker_pool.py`

**原因：** 这三个节点构成了复杂路径的执行阶段。Dispatch 将验证后的子任务加载到 TaskQueue 中。Await 阻塞直到所有子任务达到终止状态。Collect 汇总结果并处理部分失败（对副作用的补偿）。

---

#### 第 1 步：Dispatch 节点

Dispatch 节点接收已验证的计划，初始化 TaskQueue，并启动 WorkerPool。

文件：`backend/src/nanoclaw/agent/nodes/dispatch.py`

```python
async def dispatch_node(state: SupervisorState) -> dict:
    """Initialize the TaskQueue with the validated plan and start workers.

    1. Verify plan is not None
    2. Call task_queue.init_plan(plan) to load the DAG
    3. Ensure worker_pool is started
    4. Return updated state (no new fields needed — task_queue holds the state)
    """
    plan = state.get("plan")
    if plan is None:
        return {"errors": ["dispatch_node: no plan to dispatch"]}

    task_queue = state.get("task_queue")
    if task_queue is None:
        return {"errors": ["dispatch_node: no task_queue in state"]}

    await task_queue.init_plan(plan)

    # Worker pool is started separately (before graph execution or inside dispatch)
    worker_pool = state.get("worker_pool")
    if worker_pool and not worker_pool._running:
        await worker_pool.start()

    return {}  # No new state fields — queue is mutated in-place
```

原因：Dispatch 是一个"发射后不管"的节点——它设置队列和工作器，然后图移动到 await 节点。工作器在 asyncio 任务中并发运行。

---

#### 第 2 步：Await 节点

此节点阻塞，直到 TaskQueue 中的所有子任务完成（达到 SUCCEEDED、FAILED 或 CANCELLED）。

文件：`backend/src/nanoclaw/agent/nodes/await_node.py`

```python
async def await_node(state: SupervisorState) -> dict:
    """Wait for all subtasks to complete.

    Calls task_queue.wait_for_all(), which blocks until every subtask
    reaches a terminal state. Returns the collected results dict.
    """
    task_queue = state.get("task_queue")
    if task_queue is None:
        return {"errors": ["await_node: no task_queue in state"]}

    results = await task_queue.wait_for_all()
    return {"worker_results": results}
```

关键设计说明：`wait_for_all()` 使用 `asyncio.gather()` 等待队列中所有 `asyncio.Event` 对象。每个事件在对应的子任务完成（SUCCEEDED、FAILED 或 CANCELLED）时被设置。这意味着 `await_node` 会阻塞图执行，但事件循环继续并行运行工作器。

为什么需要一个专门的 await 节点：将"等待"的关注点与"调度"和"收集"分离。每个节点有单一职责，使图更易于调试和扩展。

---

#### 第 3 步：Collector 节点

Await 返回后，collector 检查结果，为失败子任务运行补偿，并构建最终输出。

文件：`backend/src/nanoclaw/agent/nodes/collector.py`

```python
async def collector_node(state: SupervisorState) -> dict:
    """Aggregate subtask results, handle partial failures, build final output.

    Logic:
    1. Get worker_results and task_queue from state
    2. Iterate over all subtasks in the plan:
       a. SUCCEEDED -> gather successful results
       b. FAILED -> run compensation action (if compensation field is set)
       c. CANCELLED -> note the cancellation reason
    3. Build a summary message
    4. Return the summary as a new message in state["messages"]
    """
    plan = state.get("plan")
    task_queue = state.get("task_queue")
    if not plan or not task_queue:
        return {"errors": ["collector: missing plan or task_queue"]}

    successful: list[str] = []
    failed: list[str] = []
    cancelled: list[str] = []

    for subtask in plan.subtasks:
        if subtask.status == TaskStatus.SUCCEEDED:
            successful.append(f"- {subtask.id}: {subtask.description}\n  Result: {subtask.result or '(no result)'}")
        elif subtask.status == TaskStatus.FAILED:
            failed.append(f"- {subtask.id}: {subtask.description}\n  Error: {subtask.error or 'unknown'}")
            # Run compensation
            if subtask.compensation:
                await _run_compensation(subtask)
        elif subtask.status == TaskStatus.CANCELLED:
            cancelled.append(f"- {subtask.id}: {subtask.description}")

    # Build summary message
    summary_parts = []
    if successful:
        summary_parts.append("## Completed\n" + "\n".join(successful))
    if failed:
        summary_parts.append("## Failed\n" + "\n".join(failed))
    if cancelled:
        summary_parts.append("## Cancelled (upstream failure)\n" + "\n".join(cancelled))

    summary = "\n\n".join(summary_parts) if summary_parts else "No subtasks executed."
    return {"messages": [AIMessage(content=summary)]}


async def _run_compensation(subtask: Subtask) -> None:
    """Execute compensation action. Updates subtask status accordingly.

    Uses asyncio.create_subprocess_shell to run the compensation command.
    If the command fails, the subtask status is set to COMPENSATION_FAILED.
    This is intentionally simple — it runs synchronously in the collector
    node's coroutine. For production, this could be delegated to a dedicated
    compensation worker.
    """
    try:
        subtask.status = TaskStatus.COMPENSATING
        proc = await asyncio.create_subprocess_shell(
            subtask.compensation,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        retcode = await proc.wait()
        subtask.status = TaskStatus.COMPENSATED if retcode == 0 else TaskStatus.COMPENSATION_FAILED
    except Exception:
        subtask.status = TaskStatus.COMPENSATION_FAILED
```

为什么 collector 与 await 分离：await 节点返回扁平的结果字典。collector 具有复杂的逻辑（补偿、状态检查、消息构建），这些会使得组合节点变得臃肿。

---

#### 第 4 步：构建 WorkerPool

文件：`backend/src/nanoclaw/agent/worker_pool.py`

WorkerPool 管理固定数量的并发工作器，这些工作器从 TaskQueue 拉取子任务，并使用 ReAct 代理执行它们。

```python
import asyncio
import logging
from typing import Callable

from langgraph.graph.state import CompiledStateGraph

from nanoclaw.agent.state import SupervisorState
from nanoclaw.storage.task_queue import TaskQueue
from nanoclaw.models.task import Subtask, TaskStatus

logger = logging.getLogger(__name__)


class WorkerPool:
    """Manages N concurrent workers that pull from TaskQueue.

    Each worker runs a ReAct subgraph (from react_agent.create_react_agent).
    Workers are asyncio.Tasks that loop: dequeue -> execute -> complete.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        react_agent: CompiledStateGraph,
        num_workers: int = 3,
        max_execution_timeout: float = 300.0,  # 5 minutes default
        sse_callback: Callable | None = None,
    ) -> None:
        self._queue = task_queue
        self._react_agent = react_agent
        self._num_workers = num_workers
        self._max_timeout = max_execution_timeout
        self._sse_callback = sse_callback
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        """Start the worker pool: spawn N asyncio.Task workers."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(self._num_workers)
        ]
        logger.info(f"WorkerPool started with {self._num_workers} workers")

    async def stop(self) -> None:
        """Stop the worker pool: cancel all worker tasks."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("WorkerPool stopped")

    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop: dequeue -> execute -> complete."""
        while self._running:
            try:
                subtask = await self._queue.dequeue()
                if subtask is None:
                    # No tasks ready — wait briefly before polling again
                    await asyncio.sleep(0.1)
                    continue

                await self._execute_subtask(worker_id, subtask)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                await asyncio.sleep(1)

    async def _execute_subtask(self, worker_id: int, subtask: Subtask) -> None:
        """Execute a single subtask using the ReAct agent.

        1. Mark subtask as RUNNING (push SSE event)
        2. Run the ReAct agent with the subtask description as input
        3. On success: call task_queue.complete()
        4. On timeout: mark FAILED with timeout error
        5. On exception: mark FAILED with error message
        """
        subtask.status = TaskStatus.RUNNING
        await self._emit("task_status", {"task_id": subtask.id, "status": "RUNNING"})

        try:
            async with asyncio.timeout(self._max_timeout):
                # The ReAct agent receives the subtask description as user message
                result = await self._react_agent.ainvoke({
                    "messages": [HumanMessage(content=subtask.description)],
                })
                last_msg = result["messages"][-1]
                output = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

                subtask.result = output
                await self._queue.complete(subtask.id, output)
                await self._emit("task_status", {"task_id": subtask.id, "status": "SUCCEEDED"})

        except asyncio.TimeoutError:
            subtask.status = TaskStatus.FAILED
            subtask.error = f"Timeout after {self._max_timeout}s"
            await self._queue.fail(subtask.id, subtask.error)
            await self._emit("task_status", {"task_id": subtask.id, "status": "FAILED", "error": subtask.error})

        except Exception as e:
            subtask.status = TaskStatus.FAILED
            subtask.error = str(e)
            await self._queue.fail(subtask.id, subtask.error)
            await self._emit("task_status", {"task_id": subtask.id, "status": "FAILED", "error": subtask.error})

    async def _emit(self, event: str, data: dict) -> None:
        """Emit SSE event via callback if configured."""
        if self._sse_callback:
            await self._sse_callback(event, data)
```

关键设计决策：
- `asyncio.timeout()` 用于最大执行超时——避免卡住的工作器阻塞池
- 工作器使用 `dequeue()` 轮询——当没有任务就绪时，它们短暂休眠后重试
- 当调用 `complete()` 时，TaskQueue 内部触发下游子任务的入队，由下一个可用工作器获取
- ReAct 代理是共享的（不是每个工作器一个）——它是一个编译图，可以并发调用，因为每次调用都有自己的状态

为什么没有 ThreadPool：工作器是 asyncio 任务，而不是线程。所有 LLM 调用使用 `.ainvoke()`，这是异步且非阻塞的。多个工作器可以并发"运行"，因为它们在每个 `await` 点都会让出控制权。

---

#### 第 5 步：验证所有新节点和工作器池

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "
from nanoclaw.agent.nodes.dispatch import dispatch_node
from nanoclaw.agent.nodes.await_node import await_node
from nanoclaw.agent.nodes.collector import collector_node
from nanoclaw.agent.worker_pool import WorkerPool
print('Dispatch/Await/Collect/WorkerPool OK')
"
```

预期：所有导入成功。

**第 6 步：提交**

```bash
git add backend/src/nanoclaw/agent/nodes/dispatch.py \
      backend/src/nanoclaw/agent/nodes/await_node.py \
      backend/src/nanoclaw/agent/nodes/collector.py \
      backend/src/nanoclaw/agent/worker_pool.py
git commit -m "feat: add dispatch, await, collect nodes and WorkerPool"
```

---

### Task 6：完整管理器图——复杂路径集成

**文件：**
- 修改：`backend/src/nanoclaw/agent/supervisor_graph.py`

**原因：** Phase 1 的管理器仅支持简单路径（router -> react_node -> END）。Phase 2 添加了复杂路径：router -> planner -> dispatch -> await -> collect -> output。必须扩展管理器图构建器以接受新的节点工厂并连接条件边。

**第 1 步：扩展 create_supervisor() 以支持复杂路径**

更新后的工厂函数签名：

```python
from nanoclaw.agent.state import SupervisorState
from nanoclaw.agent.nodes.router import RouteDecision
from nanoclaw.agent.nodes.planner import create_planner_node
from nanoclaw.agent.nodes.dispatch import dispatch_node
from nanoclaw.agent.nodes.await_node import await_node
from nanoclaw.agent.nodes.collector import collector_node
from nanoclaw.agent.worker_pool import WorkerPool
from nanoclaw.storage.task_queue import TaskQueue

def create_supervisor_graph(
    llm: Any,
    tool_registry: ToolRegistry,
    session_repo: SessionRepository,
    task_queue: TaskQueue,
    react_agent_factory: Callable,
    num_workers: int = 3,
) -> CompiledStateGraph:
```

图结构：

```
entry: router
  router -> "react" -> react_node -> END
  router -> "plan" -> planner -> dispatch -> await -> collect -> END
```

```python
def create_supervisor_graph(
    llm: Any,
    tool_registry: ToolRegistry,
    session_repo: SessionRepository,
    task_queue: TaskQueue,
    react_agent_factory: Callable,
    num_workers: int = 3,
) -> CompiledStateGraph:
    builder = StateGraph(SupervisorState)

    # Router
    router = create_router_node(llm)
    builder.add_node("router", router)

    # Simple path
    react_agent = react_agent_factory(llm, tool_registry)
    builder.add_node("react_node", react_agent)

    # Complex path nodes
    planner = create_planner_node(llm, tool_registry)
    builder.add_node("planner", planner)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("await", await_node)
    builder.add_node("collect", collector_node)

    # Entry point
    builder.set_entry_point("router")

    # Conditional edges from router
    builder.add_conditional_edges(
        "router",
        lambda state, router_result: router_result,
        {
            "react": "react_node",
            "plan": "planner",
        },
    )

    # Simple path: react_node -> END
    builder.add_edge("react_node", END)

    # Complex path: sequential nodes
    builder.add_edge("planner", "dispatch")
    builder.add_edge("dispatch", "await")
    builder.add_edge("await", "collect")
    builder.add_edge("collect", END)

    return builder.compile()
```

关于带异步节点的 `add_conditional_edges` 的注意点：router 节点是异步的（调用 LLM）。LangGraph v0.4+ 支持条件边中的异步节点——`router_result` 在 `await router_node(...)` 完成后被捕获。如果使用不支持此功能的旧版 LangGraph，请使用调用异步函数的同步包装器：

```python
def router_wrapper(state: SupervisorState) -> RouteDecision:
    """Synchronous wrapper — only if LangGraph version doesn't support async conditional edges."""
    import asyncio
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(router_node(state))
```

**第 2 步：在图调用中构建工作器池**

WorkerPool 需要在 dispatch 之前启动，在 collect 之后停止。这可以在图的入口/出口处完成，也可以由调用者管理。最简单的方法：在 `create_supervisor_graph` 工厂中创建并启动 WorkerPool，通过 SupervisorState 传递它，并让 dispatch 启动它。

```python
# Inside create_supervisor_graph, after building nodes:
worker_pool = WorkerPool(
    task_queue=task_queue,
    react_agent=react_agent,
    num_workers=num_workers,
    sse_callback=sse_callback,
)

# The worker pool reference is injected into the state via a pass-through node
# or passed as graph input when invoking:
# graph.invoke({"worker_pool": worker_pool, ...})
```

**第 3 步：验证**

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run python -c "
from nanoclaw.agent.supervisor_graph import create_supervisor_graph
print('Supervisor graph builder OK')
"
```

预期：导入成功。

**第 4 步：提交**

```bash
git add backend/src/nanoclaw/agent/supervisor_graph.py
git commit -m "feat: add complex path to supervisor graph (planner -> dispatch -> await -> collect)"
```

---

### Task 7：将完整管理器接入 SSE 端点

**文件：**
- 修改：`backend/src/nanoclaw/server/app.py`
- 修改（如缺失则创建）：`backend/src/nanoclaw/server/deps.py`

**原因：** 当前 `/chat/stream` 端点使用模拟 SSE 或 Phase 1 的仅简单路径图。它需要使用完整的 Phase 2 管理器图，并发送完整的 SSE 事件集：agent_think、agent_action、agent_observation、agent_plan、task_status、message_chunk、done。

**第 1 步：在 deps.py 中构建依赖项**

```python
from functools import lru_cache
from nanoclaw.config import settings
from nanoclaw.tools.registry import ToolRegistry
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.storage.task_queue import MemoryQueue
from nanoclaw.agent.nodes.react_agent import create_react_agent
from nanoclaw.agent.supervisor_graph import create_supervisor_graph
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

@lru_cache
def get_llm() -> ChatAnthropic | ChatOpenAI:
    if settings.llm_provider == "anthropic":
        return ChatAnthropic(
            model=settings.llm_model,
            anthropic_api_key=settings.anthropic_api_key,
        )
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
    )

@lru_cache
def get_tool_registry() -> ToolRegistry:
    # Register tools — same as Phase 1
    registry = ToolRegistry()
    # ... register tools from nanoclaw.tools ...
    return registry

@lru_cache
def get_session_repo() -> MemorySessionRepo:
    return MemorySessionRepo()

@lru_cache
def get_task_queue() -> MemoryQueue:
    return MemoryQueue()

@lru_cache
def get_supervisor_graph() -> CompiledStateGraph:
    llm = get_llm()
    registry = get_tool_registry()
    session_repo = get_session_repo()
    task_queue = get_task_queue()
    return create_supervisor_graph(
        llm=llm,
        tool_registry=registry,
        session_repo=session_repo,
        task_queue=task_queue,
        react_agent_factory=create_react_agent,
        num_workers=3,
    )
```

**第 2 步：在 /chat/stream 中实现 SSE 事件路由**

复杂路径的 SSE 事件必须包括：
- `agent_think` / `agent_action` / `agent_observation` -- 来自 ReAct 执行（简单路径和工作器路径）
- `agent_plan` -- 当 planner 完成时（包含子任务 DAG）
- `task_status` -- 子任务状态变化（PENDING、RUNNING、SUCCEEDED、FAILED、CANCELLED）
- `message_chunk` -- 最终响应流
- `done` -- 会话完成

主要挑战：LangGraph 的 `astream_events()` 输出 LangGraph 内部事件，而不是我们的 SSE 事件。我们需要一个映射层。

初始实现的最简单方法：使用一个回调，`WorkerPool` 和节点调用它以发送 SSE 事件，再加上末尾的基于生成器的标准输出。

```python
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    async def event_generator():
        # 1. Session management
        session_repo = get_session_repo()
        session = await session_repo.create(Session(...))

        # 2. Create SSE event queue (asyncio.Queue for thread-safe communication)
        sse_queue: asyncio.Queue[dict] = asyncio.Queue()

        async def sse_callback(event: str, data: dict):
            await sse_queue.put({"event": event, "data": data})

        # 3. Build the supervisor graph with SSE callback context
        graph = get_supervisor_graph()
        task_queue = get_task_queue()
        worker_pool = WorkerPool(
            task_queue=task_queue,
            react_agent=create_react_agent(get_llm(), get_tool_registry()),
            num_workers=3,
            sse_callback=sse_callback,
        )

        # 4. Prepare initial state
        initial_state = SupervisorState(
            messages=[HumanMessage(content=req.message)],
            tool_registry=get_tool_registry(),
            session_id=session.id,
            session_repo=session_repo,
            task_queue=task_queue,
            plan=None,
            worker_pool=worker_pool,
            worker_results=None,
            errors=[],
        )

        # 5. Start a background task to run the graph
        async def run_graph():
            try:
                await graph.ainvoke(initial_state)
            finally:
                await worker_pool.stop()
                await sse_queue.put({"event": "done", "data": {"session_id": session.id}})

        graph_task = asyncio.create_task(run_graph())

        # 6. Yield SSE events from the queue
        try:
            while True:
                event_data = await sse_queue.get()
                yield {
                    "event": event_data["event"],
                    "data": json.dumps(event_data["data"]),
                }
                if event_data["event"] == "done":
                    break
        finally:
            graph_task.cancel()
            try:
                await graph_task
            except asyncio.CancelledError:
                pass

    return EventSourceResponse(event_generator())
```

注意：此方法使用 asyncio.Queue 在图执行（在后台任务中）和 SSE 生成器之间进行桥接。事件通过回调推送到队列中，并逐一 yield 出去。

**第 3 步：通过 ReAct 图连接 SSE 事件**

Phase 1 的 ReAct 代理需要在调用工具和获取结果时推送 SSE 事件。修改或扩展 `create_react_agent()` 以接受可选的 `sse_callback` 参数：

```python
def create_react_agent(
    llm: Any,
    tool_registry: ToolRegistry,
    sse_callback: Callable | None = None,
) -> CompiledStateGraph:
```

在 `call_model_node` 内部，LLM 返回后：
- 如果 LLM 输出有 `content`（推理文本）：`sse_callback("agent_think", {"content": content, "task_id": task_id})`
- 如果 LLM 输出有 `tool_calls`：`sse_callback("agent_action", {"tool": name, "args": args, "task_id": task_id})`

在 `call_tool_node` 内部，工具执行后：
- `sse_callback("agent_observation", {"tool": name, "result": result, "task_id": task_id})`

这是一个不破坏向后兼容性的更改——未传递 `sse_callback` 的 Phase 1 调用者不会收到 SSE 事件（回调为 None，因此调用是无操作的）。

**第 4 步：验证**

```bash
# Start backend
cd /Users/vagrant/dev/code/python/nanoclaw && make backend

# In another terminal, test a complex request
curl -N http://localhost:8420/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyze the code in the src directory and write a summary report"}'
```

预期：SSE 事件流如下：
1. `agent_think`（路由器 LLM 回退）
2. `agent_plan`（planner 输出，包含子任务 DAG）
3. `task_status`（工作器执行时的子任务状态变化）
4. `agent_think`/`agent_action`/`agent_observation`（来自工作器）
5. `done`（会话完成）

**第 5 步：提交**

```bash
git add backend/src/nanoclaw/server/app.py backend/src/nanoclaw/server/deps.py
git commit -m "feat: wire full supervisor graph into /chat/stream with SSE events"
```

---

### Task 8：Phase 2 的 TUI 组件——PlanView 和 TaskStatusBadge

**文件：**
- 创建：`cli/src/components/PlanView.tsx`
- 创建：`cli/src/components/TaskStatusBadge.tsx`
- 修改：`cli/src/types.ts` — 为计划/任务状态添加 SSE 事件类型
- 修改：`cli/src/app.tsx` — 将新组件集成到事件循环中

**原因：** Phase 2 引入了新的 SSE 事件（`agent_plan`、`task_status`），TUI 必须显示这些事件。用户需要在执行期间看到子任务 DAG（"PlanView"）和单个子任务状态徽章（"TaskStatusBadge"）。

---

#### 第 1 步：扩展 types.ts

```typescript
// Add to existing types.ts

export interface SubtaskInfo {
  id: string
  description: string
  status: TaskStatus
  depends_on: string[]
}

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "RETRYING"
  | "CANCELLED"
  | "COMPENSATING"
  | "COMPENSATED"
  | "COMPENSATION_FAILED"

export interface AgentPlanData {
  tasks: SubtaskInfo[]
  session_id: string
}

export interface TaskStatusData {
  task_id: string
  status: TaskStatus
  error?: string
}

// SSE event map
export interface SSEEventMap {
  agent_think: AgentThinkData
  agent_action: AgentActionData
  agent_observation: AgentObservationData
  agent_plan: AgentPlanData
  task_status: TaskStatusData
  message_chunk: { content: string; task_id: string }
  done: { session_id: string }
}
```

---

#### 第 2 步：实现 TaskStatusBadge

文件：`cli/src/components/TaskStatusBadge.tsx`

```tsx
import { Text } from "ink"
import type { TaskStatus } from "../types.js"

const STATUS_COLORS: Record<TaskStatus, string> = {
  PENDING: "gray",
  RUNNING: "cyan",
  SUCCEEDED: "green",
  FAILED: "red",
  RETRYING: "yellow",
  CANCELLED: "dim",
  COMPENSATING: "yellow",
  COMPENSATED: "green",
  COMPENSATION_FAILED: "red",
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  PENDING: "pending",
  RUNNING: "running",
  SUCCEEDED: "done",
  FAILED: "failed",
  RETRYING: "retry",
  CANCELLED: "skip",
  COMPENSATING: "undo",
  COMPENSATED: "undone",
  COMPENSATION_FAILED: "underr",
}

interface Props {
  status: TaskStatus
}

export function TaskStatusBadge({ status }: Props) {
  const color = STATUS_COLORS[status] ?? "white"
  const label = STATUS_LABELS[status] ?? status
  return <Text color={color}>{`[${label}]`}</Text>
}
```

为什么使用紧凑标签：SUCCEEDED -> "done", FAILED -> "failed", COMPENSATION_FAILED -> "underr"（最多 5 个字符）。徽章旨在内联显示在任务描述旁边。

---

#### 第 3 步：实现 PlanView

文件：`cli/src/components/PlanView.tsx`

```tsx
import { Box, Text } from "ink"
import { TaskStatusBadge } from "./TaskStatusBadge.js"
import type { SubtaskInfo } from "../types.js"

interface Props {
  tasks: SubtaskInfo[]
}

export function PlanView({ tasks }: Props) {
  if (!tasks || tasks.length === 0) return null

  // Group tasks by dependency level for visual tree display
  // Level 0: no dependencies (roots)
  // Level N: depends on task in level N-1
  const levels = buildLevels(tasks)

  return (
    <Box flexDirection="column" padding={1} borderStyle="round" borderColor="cyan">
      <Text bold color="cyan">Execution Plan</Text>
      {levels.map((level, i) => (
        <Box key={i} flexDirection="column" marginLeft={i * 2}>
          {level.map((task) => (
            <Box key={task.id}>
              <Text dimColor>{task.id}: </Text>
              <Text>{task.description}</Text>
              <Text> </Text>
              <TaskStatusBadge status={task.status} />
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  )
}

function buildLevels(tasks: SubtaskInfo[]): SubtaskInfo[][] {
  // Build a map: task_id -> SubtaskInfo
  const taskMap = new Map(tasks.map((t) => [t.id, t]))

  // Topological sort by dependency depth
  const depth = new Map<string, number>()
  function getDepth(id: string): number {
    if (depth.has(id)) return depth.get(id)!
    const task = taskMap.get(id)
    if (!task || task.depends_on.length === 0) {
      depth.set(id, 0)
      return 0
    }
    const d = 1 + Math.max(...task.depends_on.map((dep) => getDepth(dep)))
    depth.set(id, d)
    return d
  }

  for (const t of tasks) getDepth(t.id)

  // Group by depth
  const levels: SubtaskInfo[][] = []
  for (const [id, d] of depth) {
    const task = taskMap.get(id)!
    while (levels.length <= d) levels.push([])
    levels[d].push(task)
  }
  return levels
}
```

为什么按深度使用树形布局：让用户直观感受并行性——同一深度的任务可以并发运行。缩进显示了依赖流程。

---

#### 第 4 步：集成到 app.tsx

Phase 1 的 app.tsx SSE 事件循环需要处理两种新事件类型：

```tsx
// Add state:
const [currentPlan, setCurrentPlan] = useState<SubtaskInfo[]>([])

// In the SSE parsing loop inside StreamingChat or equivalent:
if (currentEvent === "agent_plan") {
  setCurrentPlan(data.tasks)
} else if (currentEvent === "task_status") {
  // Update specific subtask status in currentPlan
  setCurrentPlan((prev) =>
    prev.map((t) =>
      t.id === data.task_id ? { ...t, status: data.status } : t
    )
  )
}
```

在渲染部分，消息列表上方：

```tsx
{currentPlan.length > 0 && (
  <PlanView tasks={currentPlan} />
)}
```

---

#### 第 5 步：验证

```bash
cd /Users/vagrant/dev/code/python/nanoclaw
npx tsc --noEmit
```

预期：无 TypeScript 错误。

**第 6 步：提交**

```bash
git add cli/src/components/PlanView.tsx \
      cli/src/components/TaskStatusBadge.tsx \
      cli/src/types.ts \
      cli/src/app.tsx
git commit -m "feat: add PlanView and TaskStatusBadge TUI components"
```

---

### Task 9：集成测试——端到端多任务流程

**文件：**
- 修改：（仅测试文件，无生产文件）

**原因：** 多任务流程涉及许多移动部件（router、planner、dispatch、工作器池、TaskQueue DAG、SSE 事件）。集成测试确保整个流水线端到端正常工作，然后才能声称 Phase 2 完成。

**第 1 步：使用模拟 LLM 创建测试计划**

关键挑战：集成测试需要确定性的 LLM 响应。使用一个为 planner 返回固定 JSON、为工作器返回简单响应的模拟 LLM。

```python
# backend/tests/test_phase2_flow.py

import pytest
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from nanoclaw.models.task import TaskPlan, Subtask, TaskStatus
from nanoclaw.storage.task_queue import MemoryQueue
from nanoclaw.storage.session_repo import MemorySessionRepo
from nanoclaw.tools.registry import ToolRegistry
from nanoclaw.agent.nodes.router import create_router_node
from nanoclaw.agent.nodes.validate import validate_plan
from nanoclaw.agent.worker_pool import WorkerPool


class MockLLM:
    """Mock LLM that returns predefined responses."""

    def __init__(self, response: str) -> None:
        self.response = response

    async def ainvoke(self, messages, **kwargs):
        return AIMessage(content=self.response)


@pytest.mark.asyncio
async def test_router_heuristic_simple():
    """Router should classify 'hello' as simple without calling LLM."""
    llm = MockLLM('{"decision": "simple"}')  # Should NOT be called
    router = create_router_node(llm)
    # ... test via router(state_with_hello_message)


@pytest.mark.asyncio
async def test_router_heuristic_complex():
    """Router should classify 'analyze project' as complex."""
    # ...


@pytest.mark.asyncio
async def test_validate_plan_valid():
    """Valid DAG plan passes validation."""
    # ...


@pytest.mark.asyncio
async def test_validate_plan_cycle():
    """Cyclic DAG fails validation."""
    # ...


@pytest.mark.asyncio
async def test_planner_parses_llm_output():
    """Planner node correctly parses LLM JSON into TaskPlan."""
    # ...


@pytest.mark.asyncio
async def test_worker_pool_executes_subtasks():
    """Worker pool runs subtasks and marks them complete."""
    # ...


@pytest.mark.asyncio
async def test_full_supervisor_complex_path():
    """Full complex path: router->plan->dispatch->await->collect."""
    # ...
```

运行：

```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run pytest tests/ -v
```

预期：所有测试通过。

**第 2 步：手动端到端验证**

```bash
# Terminal 1: start backend
cd /Users/vagrant/dev/code/python/nanoclaw && make backend

# Terminal 2: start TUI
cd /Users/vagrant/dev/code/python/nanoclaw && make app

# Terminal 3: test with curl for a complex request
curl -N http://localhost:8420/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Research the Python requests library and write a summary"}'
```

验证 curl 输出中的 SSE 事件：
1. `router` 决策（通过 `agent_think`，如果是 LLM 回退，或者隐式）
2. `agent_plan` 包含子任务 DAG
3. 每个子任务的 `task_status: RUNNING`
4. 工作器的 `agent_action`/`agent_observation` 事件
5. 每个子任务的 `task_status: SUCCEEDED` 或 `FAILED`
6. Collector 的 `message_chunk` 包含最终摘要
7. `done` 事件

**第 3 步：提交测试（Phase 2 无提交——测试仅用于验证）**

注意：上述测试用于验证。它们应该存在于项目的测试目录中。如果项目还没有测试目录，创建 `backend/tests/test_phase2_flow.py`。是否提交测试取决于用户的工作流程。

---

### Phase 2 完成检查清单

- [ ] SupervisorState 已定义，包含 plan、task_queue、worker_pool、worker_results、errors 字段
- [ ] Router 节点分类请求：简单关键词 -> "react"，复杂关键词 -> "plan"，模糊 -> LLM 回退
- [ ] Planner 节点调用 LLM 将请求分解为 Subtask DAG，将 JSON 解析为 TaskPlan
- [ ] `validate_plan()` 检查：唯一 ID、引用完整性、循环检测（DFS）
- [ ] Dispatch 节点调用 `task_queue.init_plan()` 并启动 WorkerPool
- [ ] Await 节点使用 asyncio.Event 调用 `task_queue.wait_for_all()`
- [ ] Collector 节点汇总结果，为失败子任务运行补偿
- [ ] WorkerPool 管理 N 个异步工作器，每个从 TaskQueue.dequeue() 拉取
- [ ] 工作器有最大执行超时（默认 5 分钟）
- [ ] 工作器在 TaskQueue 上调用 `complete()` / `fail()`
- [ ] 完整的管理器图：router -> (react | planner -> dispatch -> await -> collect) -> output
- [ ] SSE 事件：agent_plan、task_status（来自节点和工作器）
- [ ] TUI：PlanView 以树形布局显示子任务 DAG
- [ ] TUI：TaskStatusBadge 以颜色编码标签显示状态
- [ ] `/chat/stream` 使用 asyncio.Queue 在图执行和 SSE 生成器之间进行桥接
- [ ] 集成测试覆盖 router、validate_plan、planner 解析、工作器池、完整流程
