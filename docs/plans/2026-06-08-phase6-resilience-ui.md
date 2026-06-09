# 阶段 6：弹性与前端优化

**日期：** 2026-06-08
**状态：** 草案
**依赖：** 第 1-5 阶段（所有存储 + Docker 基础设施已就位）

## 概述

通过重试逻辑、级联取消、补偿回滚和错误聚合来增加系统弹性。使用专门用于计划可视化、思考块、工具调用卡片和计划任务管理的组件来优化 TUI。

## 设计决策

- **为什么在 TaskQueue 级别进行重试**：TaskQueue 已经编排了任务生命周期（出队/完成/失败）。在此处添加重试逻辑是单一控制点——每个工作线程自动获得重试支持，无需单独修改工作线程。
- **为什么使用指数退避**：没有退避，快速重试可能会冲击同一个失败的依赖项（例如 API 速率限制）。从 1 秒开始并加倍给系统恢复的时间。
- **为什么补偿与重试分开**：重试尝试重新执行同一个任务。补偿则执行不同的操作（撤销副作用）。达到 `max_retries` 仍然失败的任务需要补偿，而不是另一次重试。
- **为什么 COMPENSATION_FAILED 是终止状态**：补偿本身可能失败（文件被锁定、已删除）。当发生时，系统无法自动恢复——需要人工干预。明确的状态在 UI 中清晰地标识这一点。
- **为什么 PlanView 渲染为缩进树**：Ink 没有原生树控件。使用带 `paddingLeft` 的 `Box` 按深度层级排列是最简单的方法。连接线（`│`、`├──`、`└──`）是附加的优化，而非核心功能。
- **为什么 ThinkingBlock 使用 Ink 的 `dimColor`**：灰色/斜体文本是公认的内部独白与最终输出的区别方式。Ink 的 `<Text dimColor>` 和 `<Text italic>` 无需自定义样式即可处理。
- **为什么 ToolCallCard 使用彩色边框**：Ink 通过 `borderColor` 支持彩色 `Box` 边框。不同的边框颜色用于操作（黄色）与观察（成功为绿色，出错为红色）可提供即时的视觉扫描。

## 任务 6.1：子任务重试逻辑——max_retries + 指数退避

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py` (MemoryQueue)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py` (RedisQueue)

**原因：**
当子任务因瞬时错误（网络超时、速率限制、临时文件锁定）而失败时，工作线程应重试，而不是立即将整个计划标记为失败。重试是自动的，在耗尽之前对用户不可见。

### 步骤

1. **向 Subtask 模型添加重试字段**

   ```python
   @dataclass
   class Subtask:
       # ... 现有字段
       max_retries: int = 3
       retry_count: int = 0
       retry_delay_base: float = 1.0  # 秒——指数退避基数
   ```

   这些应该已存在于设计文档中。如果不存在，请立即添加。

2. **修改 MemoryQueue.fail()**——在级联取消之前添加重试逻辑：

   ```python
   async def fail(self, task_id: str, error: str) -> None:
       task = self._tasks[task_id]
       task.error = error

       if task.retry_count < task.max_retries:
           # 使用指数退避进行重试
           task.retry_count += 1
           task.status = TaskStatus.RETRYING
           delay = task.retry_delay_base * (2 ** (task.retry_count - 1))
           # 调度延迟后重新入队
           asyncio.create_task(self._requeue_after_delay(task_id, delay))
           return  # 先不级联取消——任务可能在重试时成功

       # 最大重试次数耗尽——标记为永久失败
       task.status = TaskStatus.FAILED
       self._completed_count += 1
       self._events[task_id].set()
       # 级联取消下游（在第 2 阶段实现）
       await self._cascade_cancel(task_id)
       await self._check_all_done()
   ```

3. **添加 `_requeue_after_delay` 辅助方法**：

   ```python
   async def _requeue_after_delay(self, task_id: str, delay: float) -> None:
       await asyncio.sleep(delay)
       task = self._tasks[task_id]
       task.status = TaskStatus.PENDING  # 重置为 PENDING 以便重新出队
       # 重新检查依赖（延迟期间可能已变化）
       if all(self._tasks[d].status == TaskStatus.SUCCEEDED for d in task.depends_on):
           await self._ready.put(task)  # 重新入队到就绪队列
       else:
           # 依赖仍未满足——正常情况下不应发生，但优雅处理
           task.status = TaskStatus.FAILED
           self._completed_count += 1
           self._events[task_id].set()
           await self._cascade_cancel(task_id)
           await self._check_all_done()
   ```

4. **添加 `_check_all_done` 辅助方法**：

   ```python
   async def _check_all_done(self) -> None:
       if self._completed_count >= self._total_count:
           self._all_done.set()
   ```

5. **修改 RedisQueue.fail()**——使用相同的重试逻辑模式，但基于 Redis 存储：

   ```python
   async def fail(self, task_id: str, error: str) -> None:
       task = self._tasks[task_id]
       task.error = error
       redis = await get_redis()

       if task.retry_count < task.max_retries:
           task.retry_count += 1
           task.status = TaskStatus.RETRYING
           delay = task.retry_delay_base * (2 ** (task.retry_count - 1))
           await redis.hset(self._task_key(task_id), mapping={
               "status": TaskStatus.RETRYING.value,
               "error": error,
               "retry_count": str(task.retry_count),
           })
           await self._zrequeue_after_delay(task_id, delay)
           return

       # 最大重试次数耗尽——永久失败
       task.status = TaskStatus.FAILED
       self._completed_count += 1
       await redis.zrem(self._leases, task_id)
       await redis.hset(self._task_key(task_id), mapping={
           "status": TaskStatus.FAILED.value,
           "error": error,
       })
       # 级联取消下游
       await self._cascade_cancel(task_id)
       if self._completed_count >= self._total_count:
           await redis.publish(self._pubsub, "ALL_DONE")
   ```

6. **为 RedisQueue 添加 `_zrequeue_after_delay`**（使用基于时间戳分数的有序集合进行延迟重新入队）：

   ```python
   async def _zrequeue_after_delay(self, task_id: str, delay: float) -> None:
       """使用 ZSET 作为延迟队列。后台轮询器重新入队已过期的项目。"""
       redis = await get_redis()
       requeue_at = time.time() + delay
       delayed_key = f"{self.KEY_PREFIX}{self.session_id}:delayed"
       await redis.zadd(delayed_key, {task_id: requeue_at})
       # 可选：触发立即轮询，而不是等待出队
       asyncio.create_task(self._poll_delayed(delayed_key))
   ```

   实际上，更简单的方法：让 `dequeue()` 轮询延迟 ZSET：

   ```python
   async def dequeue(self) -> Subtask | None:
       redis = await get_redis()
       # 首先，检查延迟队列中是否有就绪的重试任务
       delayed_key = f"{self.KEY_PREFIX}{self.session_id}:delayed"
       now = time.time()
       ready = await redis.zrangebyscore(delayed_key, "-inf", now)
       for task_id in ready:
           await redis.zrem(delayed_key, task_id)
           # 重新入队到就绪队列
           await redis.lpush(self._q, task_id)
       # 然后正常执行 BRPOP
       result = await redis.brpop(self._q, timeout=5)
       # ... 出队的其余部分
   ```

   这避免了一个单独的后台线程。每次 `dequeue()` 调用首先清空延迟 ZSET。尚未就绪的任务等待下一次轮询。

### 重试状态流转

```
PENDING → RUNNING → FAILED → RETRYING → PENDING → RUNNING (重试循环)
                                         → FAILED (最大重试次数耗尽 → 补偿)
```

### 验证

```python
# 1. 创建一个 max_retries=2 的子任务
# 2. 使用瞬时错误调用 fail()
# 3. 延迟后，任务应回到就绪队列，retry_count=1
# 4. 再次调用 fail()（模拟第二次尝试失败）
# 5. 延迟后，任务应回到队列，retry_count=2
# 6. 第三次调用 fail()——任务应永久标记为 FAILED
```

### 提交信息

```
feat: add subtask retry with exponential backoff (MemoryQueue + RedisQueue)
```

---

## 任务 6.2：级联失败时的补偿回滚

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py`（新建或已有）
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/models/task.py`

**原因：**
当子任务在耗尽重试次数后失败时，任何已经执行并产生副作用（如文件写入或 API 调用）的下游任务都需要补偿/回滚。EffectLog 跟踪这些副作用，而收集器节点在级联取消后触发补偿。

### 步骤

1. **向 Subtask 模型添加补偿字段**（如果尚未存在）：

   ```python
   @dataclass
   class Subtask:
       compensation: str | None = None
       # "rm -rf output/" 或工具名称 + 补偿操作的参数
       compensation_max_attempts: int = 3
       compensation_attempts: int = 0
   ```

2. **向 TaskStatus 枚举添加 COMPENSATING 和 COMPENSATED 状态**（如果尚未存在）：

   ```python
   class TaskStatus(str, Enum):
       PENDING = "PENDING"
       RUNNING = "RUNNING"
       SUCCEEDED = "SUCCEEDED"
       FAILED = "FAILED"
       RETRYING = "RETRYING"
       CANCELLED = "CANCELLED"
       COMPENSATING = "COMPENSATING"
       COMPENSATED = "COMPENSATED"
       COMPENSATION_FAILED = "COMPENSATION_FAILED"
   ```

3. **在 collector.py 中实现补偿执行**：

   ```python
   """Collector 节点——聚合结果，触发失败子任务的补偿。"""

   class CollectorNode:
       def __init__(self, tool_registry: ToolRegistry, effect_log: EffectLog) -> None:
           self.tool_registry = tool_registry
           self.effect_log = effect_log

       async def collect(self, state: SupervisorState) -> dict:
           plan = state.active_plan
           if plan is None:
               return {"messages": [AIMessage(content="未执行任何计划。")]}

           failed_tasks = [s for s in plan.subtasks if s.status == TaskStatus.FAILED]
           succeeded_with_effects = [s for s in plan.subtasks
                                     if s.status == TaskStatus.SUCCEEDED
                                     and s.compensation is not None]

           # 阶段 1：如果有任何任务失败，标记已成功的下游任务进行补偿
           if failed_tasks:
               for task in succeeded_with_effects:
                   # 检查此成功任务是否位于失败任务的下游
                   if self._is_downstream_of_any(plan, task.id, failed_tasks):
                       await self._execute_compensation(task)

           # 阶段 2：构建错误摘要
           errors = []
           for task in plan.subtasks:
               if task.status == TaskStatus.FAILED:
                   errors.append(f"  [{task.id}] {task.description}: {task.error}")
               elif task.status == TaskStatus.COMPENSATION_FAILED:
                   errors.append(f"  [{task.id}] 补偿失败: {task.error}")
               elif task.status == TaskStatus.CANCELLED:
                   errors.append(f"  [{task.id}] {task.description}: CANCELLED (上游失败)")

           summary = self._build_summary(plan, errors)
           return {"messages": [AIMessage(content=summary)]}

       async def _execute_compensation(self, subtask: Subtask) -> None:
           if subtask.compensation is None:
               return

           subtask.status = TaskStatus.COMPENSATING
           for attempt in range(subtask.compensation_max_attempts):
               try:
                   # 执行补偿：可以是 shell 命令或工具调用
                   tool = self.tool_registry.get(subtask.compensation)
                   if tool:
                       await tool.run(subtask)
                   else:
                       # 回退：作为 shell 命令执行
                       import subprocess
                       subprocess.run(subtask.compensation, shell=True, check=True)
                   subtask.status = TaskStatus.COMPENSATED
                   return
               except Exception as e:
                   subtask.compensation_attempts += 1
                   if attempt < subtask.compensation_max_attempts - 1:
                       await asyncio.sleep(1.0 * (2 ** attempt))  # 指数退避
                   else:
                       subtask.status = TaskStatus.COMPENSATION_FAILED
                       subtask.error = f"补偿在 {attempt+1} 次尝试后失败: {e}"

       def _is_downstream_of_any(self, plan, task_id: str, failed_tasks: list) -> bool:
           """检查 task_id 是否（传递性地）位于任何失败任务的下游。"""
           visited = set()
           stack = list(failed_tasks)
           while stack:
               current = stack.pop()
               if current.id in visited:
                   continue
               visited.add(current.id)
               if current.id == task_id:
                   return True
               # 检查依赖当前任务的子任务
               for st in plan.subtasks:
                   if current.id in st.depends_on:
                       stack.append(st)
           return False
   ```

4. **将补偿接入 Queue 的 fail() 流程**（已在任务 6.1 中涉及——当 `_cascade_cancel` 运行时，它只在下游任务上设置 CANCELLED 状态。实际的补偿执行发生在所有任务完成后的 collector 节点中。）

### 补偿流程

```
Task A 在 3 次重试后失败
  → Queue.fail() 将 Task A 标记为 FAILED
  → Queue.fail() 级联：Task B（A 的下游）→ CANCELLED
  → Task C（B 的下游，已 SUCCEEDED 并创建文件）→ 保持 SUCCEEDED
  → 等待所有任务完成（包括已取消的任务）
  → Collector node:
      ✓ 识别出任务 C 位于失败任务 A 的下游
      ✓ 任务 C 的补偿为 "rm -rf output/"
      ✓ Collector 执行补偿
      ✓ 任务 C 状态 → COMPENSATED
  → 最终摘要包含："任务 C 文件已清理（补偿: rm -rf output/）"
```

### 验证

```python
# 1. 创建计划：Task A → Task B → Task C
# 2. Task A 的 max_retries=0，将立即失败
# 3. Task C 的 compensation="cleanup_command"
# 4. 运行计划
# 5. 验证：
#    - Task A: FAILED
#    - Task B: CANCELLED（从未运行）
#    - Task C: COMPENSATED（已运行，然后回滚）
```

### 提交信息

```
feat: add compensation rollback for downstream tasks on cascading failure
```

---

## 任务 6.3：上游失败时 CANCELLED 传播

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py` (MemoryQueue)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py` (RedisQueue)

**原因：**
当子任务失败（耗尽重试次数后）时，所有依赖它的下游任务应立即标记为 CANCELLED。否则，下游工作线程会浪费时间执行输入已无效的任务。`_cascade_cancel` 方法已存在于基础设计中——本任务确保其健壮且能传递性地标记所有后续任务。

### 步骤

1. **在 MemoryQueue 中实现 `_cascade_cancel`**：

   ```python
   async def _cascade_cancel(self, task_id: str) -> None:
       """将所有传递性下游任务标记为 CANCELLED。
       在反向 DAG 上使用 BFS 以避免深度递归问题。"""
       queue = list(self._rdag.get(task_id, []))
       while queue:
           downstream = queue.pop(0)
           task = self._tasks.get(downstream)
           if task is None or task.status != TaskStatus.PENDING:
               # 已完成或已取消——跳过
               continue
           task.status = TaskStatus.CANCELLED
           task.error = f"已取消：上游任务 {task_id} 失败"
           self._completed_count += 1
           self._events[downstream].set()
           # 添加此任务自身的下游任务
           queue.extend(self._rdag.get(downstream, []))
   ```

   关键点：
   - 使用 BFS（迭代式，非递归）以避免 Python 递归深度限制。
   - 仅将 PENDING 状态的任务标记为 CANCELLED。如果任务已经在 RUNNING 状态，它将继续运行——但其结果将由 collector 丢弃。
   - 每个取消的任务设置其 Event，以便 `wait_for_all()` 正常推进。

2. **在 RedisQueue 中实现 `_cascade_cancel`**（已在第 5 阶段中草拟）：

   ```python
   async def _cascade_cancel(self, task_id: str) -> None:
       redis = await get_redis()
       queue = list(self._rdag.get(task_id, []))
       while queue:
           downstream = queue.pop(0)
           task = self._tasks.get(downstream)
           if task is None or task.status != TaskStatus.PENDING:
               continue
           task.status = TaskStatus.CANCELLED
           task.error = f"已取消：上游任务 {task_id} 失败"
           self._completed_count += 1
           await redis.hset(self._task_key(downstream), mapping={
               "status": TaskStatus.CANCELLED.value,
               "error": task.error,
           })
           queue.extend(self._rdag.get(downstream, []))
   ```

3. **确保 `complete()` 不会重新入队已取消任务的下游任务。** 在 `complete()` 方法中，检查依赖时如果任何依赖是 CANCELLED，下游也应通过 `_cascade_cancel` 设置为 CANCELLED，而不是入队：

   ```python
   async def complete(self, task_id: str, result: str) -> None:
       # ... 现有代码 ...
       for downstream in self._rdag.get(task_id, []):
           deps = self._dag[downstream]
           dep_statuses = [self._tasks[d].status for d in deps]
           if any(s == TaskStatus.FAILED for s in dep_statuses):
               # 依赖失败——取消此任务
               await self._cascade_cancel(task_id)
           elif all(s == TaskStatus.SUCCEEDED for s in dep_statuses):
               await self._ready.put(self._tasks[downstream])
   ```

### 取消流程

```
1. Worker 出队 Task A，运行它，调用 fail()
2. Queue.fail() 设置 Task A → FAILED（或 RETRYING → FAILED）
3. Queue.fail() 调用 _cascade_cancel("task_A")
4. _cascade_cancel 发现 Task B 依赖 A → Task B → CANCELLED
5. _cascade_cancel 发现 Task C 依赖 B → Task C → CANCELLED
6. B 和 C 的 Events 都被设置
7. wait_for_all() 发现 completed_count 已达到 → 返回
8. Collector 看到 B 和 C 是 CANCELLED → 包含在错误摘要中
```

### 验证

```python
# 1. 创建一个 4 任务链：A → B → C → D
# 2. 让任务 A 失败
# 3. 验证 B、C、D 均为 CANCELLED
# 4. 确认 completed_count == 4
```

### 提交信息

```
feat: add transitive CANCELLED propagation on upstream task failure
```

---

## 任务 6.4：Collector 节点中的错误聚合

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py`

**原因：**
当多个子任务失败时，用户会收到类似"某些任务失败"的单一消息。错误聚合收集所有失败原因并生成结构化的错误报告，将永久性失败与成功的补偿分开。

### 步骤

1. **在 CollectorNode 中实现 `_build_summary`**：

   ```python
   def _build_summary(self, plan: TaskPlan, errors: list[str]) -> str:
       total = len(plan.subtasks)
       succeeded = sum(1 for s in plan.subtasks if s.status == TaskStatus.SUCCEEDED)
       compensated = sum(1 for s in plan.subtasks if s.status == TaskStatus.COMPENSATED)
       failed = sum(1 for s in plan.subtasks if s.status == TaskStatus.FAILED)
       cancelled = sum(1 for s in plan.subtasks if s.status == TaskStatus.CANCELLED)
       comp_failed = sum(1 for s in plan.subtasks if s.status == TaskStatus.COMPENSATION_FAILED)

       lines = []
       lines.append(f"计划完成：{succeeded} 成功，{failed} 失败，"
                    f"{cancelled} 已取消，{compensated} 已补偿，"
                    f"{comp_failed} 补偿失败（共 {total} 个）")
       lines.append("")

       if errors:
           lines.append("### 错误")
           for err in errors:
               lines.append(err)

       if compensated > 0:
           lines.append("")
           lines.append("### 已应用的补偿")
           for task in plan.subtasks:
               if task.status == TaskStatus.COMPENSATED:
                   if task.compensation:
                       lines.append(f"  [{task.id}] 已回滚: {task.compensation}")

       # 收集成功结果
       success_results = [s for s in plan.subtasks
                          if s.status == TaskStatus.SUCCEEDED and s.result]
       if success_results:
           lines.append("")
           lines.append("### 结果")
           for task in success_results:
               lines.append(f"  [{task.id}] {task.description}")
               if task.result:
                   lines.append(f"    → {task.result[:200]}")

       return "\n".join(lines)
   ```

2. **为 collector 输出接入 SSE 事件**。collector 应针对错误摘要和补偿状态发出 SSE 事件：

   ```python
   # 在 collector 节点中，聚合之后：
   for task in plan.subtasks:
       if task.status == TaskStatus.COMPENSATING:
           await sse_manager.emit("task_status", {
               "task_id": task.id,
               "status": "COMPENSATING",
           })
       elif task.status == TaskStatus.COMPENSATED:
           await sse_manager.emit("task_status", {
               "task_id": task.id,
               "status": "COMPENSATED",
           })
   ```

### 验证

```python
# 1. 运行一个计划，其中 5 个子任务中有 2 个失败
# 2. 检查 collector 输出：
#    - 包含"计划完成：2 成功，2 失败，1 已取消，1 已补偿..."
#    - 列出每个错误及其任务 ID
#    - 显示补偿详情
```

### 提交信息

```
feat: add error aggregation and structured summary in CollectorNode
```

---

## 任务 6.5：TUI PlanView 组件——任务依赖树

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/PlanView.tsx`

**原因：**
当执行复杂计划时，用户需要查看任务树、依赖关系和实时状态更新。对于多层 DAG 来说，平面列表令人困惑。带有状态徽章的缩进树提供了即时的态势感知。

### 步骤

1. **创建计划渲染所需的类型**（添加到 `types.ts` 或创建本地接口）：

   ```typescript
   export interface SubtaskStatus {
     id: string
     description: string
     status: TaskStatusValue
     depends_on: string[]
   }

   export type TaskStatusValue =
     | "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED"
     | "RETRYING" | "CANCELLED" | "COMPENSATING" | "COMPENSATED" | "COMPENSATION_FAILED"

   export interface PlanMessage {
     type: "plan"
     session_id: string
     tasks: SubtaskStatus[]
   }

   export interface TaskStatusUpdate {
     type: "task_status"
     task_id: string
     status: TaskStatusValue
   }
   ```

2. **创建 `PlanView.tsx`**：

   ```tsx
   import { Box, Text } from "ink"
   import { SubtaskStatus, TaskStatusValue } from "../types.js"

   interface Props {
     tasks: SubtaskStatus[]
   }

   // 从 DAG（依赖关系）构建树的层级
   function buildTree(tasks: SubtaskStatus[]): number[] {
     // 拓扑排序 → 为每个任务分配深度层级
     // 根任务（无依赖）→ 层级 0
     // 依赖层级 0 任务的任务 → 层级 1，以此类推
     const depths: Record<string, number> = {}
     const taskMap = new Map(tasks.map(t => [t.id, t]))

     function getDepth(id: string): number {
       if (id in depths) return depths[id]
       const task = taskMap.get(id)
       if (!task || task.depends_on.length === 0) {
         depths[id] = 0
         return 0
       }
       const maxParentDepth = Math.max(...task.depends_on.map(getDepth))
       depths[id] = maxParentDepth + 1
       return depths[id]
     }

     // 按拓扑顺序计算深度（迭代至稳定）
     for (const task of tasks) {
       getDepth(task.id)
     }

     return tasks.map(t => depths[t.id] ?? 0)
   }

   function statusColor(status: TaskStatusValue): string {
     switch (status) {
       case "PENDING":        return "gray"
       case "RUNNING":        return "yellow"
       case "SUCCEEDED":      return "green"
       case "FAILED":         return "red"
       case "RETRYING":       return "yellowBright"
       case "CANCELLED":      return "gray"
       case "COMPENSATING":   return "yellow"
       case "COMPENSATED":    return "green"
       case "COMPENSATION_FAILED": return "red"
     }
   }

   function statusIcon(status: TaskStatusValue): string {
     switch (status) {
       case "PENDING":        return "○"
       case "RUNNING":        return "●"
       case "SUCCEEDED":      return "✓"
       case "FAILED":         return "✗"
       case "RETRYING":       return "↻"
       case "CANCELLED":      return "⊘"
       case "COMPENSATING":   return "⟲"
       case "COMPENSATED":    return "↺"
       case "COMPENSATION_FAILED": return "‼"
     }
   }

   export function PlanView({ tasks }: Props) {
     const depths = buildTree(tasks)

     return (
       <Box flexDirection="column" paddingLeft={0}>
         <Text bold underline>任务计划</Text>
         {tasks.map((task, i) => (
           <Box key={task.id} paddingLeft={depths[i] * 2}>
             <Text>
               <Text color={statusColor(task.status)}>
                 {statusIcon(task.status)}
               </Text>
               {" "}
               <Text dimColor>{task.id}</Text>
               {" "}
               <Text>{task.description}</Text>
             </Text>
           </Box>
         ))}
       </Box>
     )
   }
   ```

3. **将 PlanView 接入 app.tsx**：

   ```tsx
   // import PlanView
   // 当 agent_plan SSE 事件到达时，渲染 PlanView
   // 当 task_status SSE 事件到达时，原地更新计划
   ```

   计划状态应维护为 `Map<string, TaskStatusValue>`，这样 `task_status` 更新可以改变单个任务图标，而无需重新渲染整个树。

### 验证

```bash
# 1. 模拟 SSE 流，发送包含 5 个任务的 agent_plan 事件
# 2. 验证树的缩进是否正确
# 3. 发送 task_status 更新——验证只有受影响的任务图标发生更改
```

### 提交信息

```
feat: add PlanView TUI component with status icon and indented dependency tree
```

---

## 任务 6.6：TUI ThinkingBlock 组件

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ThinkingBlock.tsx`

**原因：**
代理推理文本（来自 SSE `agent_think` 事件）是内部独白，而非最终输出。灰色/斜体渲染使其与结果区分。该块应在思考开始时出现，并在新 token 到达时原地更新。

### 步骤

1. **创建 `ThinkingBlock.tsx`**：

   ```tsx
   import { Text, Box } from "ink"

   interface Props {
     content: string
     taskId?: string
   }

   export function ThinkingBlock({ content, taskId }: Props) {
     // 以灰色/斜体渲染思考文本，与最终输出区分
     const lines = content.split("\n")
     return (
       <Box flexDirection="column" paddingLeft={2}>
         {taskId && (
           <Text dimColor italic>[think:{taskId}]</Text>
         )}
         {lines.map((line, i) => (
           <Text key={i} dimColor italic>
             {line || " "}
           </Text>
         ))}
       </Box>
     )
   }
   ```

2. **接入 StreamingChat 或 app.tsx**：

   在 `StreamingChat.tsx` 中，当 SSE 解析器接收到 `agent_think` 事件时，在主消息输出旁渲染 `<ThinkingBlock content={data.content} taskId={data.task_id} />`。

   ThinkingBlock 应出现在响应区域的顶部（最终答案之上），模拟最终输出之前的思维过程。

3. **与应用状态集成**：

   ```tsx
   // 在 app.tsx 中：
   const [thinkingContent, setThinkingContent] = useState<string>("")

   // 当 SSE agent_think 事件到达时：
   //   setThinkingContent(data.content) — 原地更新块
   // 当 SSE message_chunk 事件到达时：
   //   清除 thinkingContent（思考结束，输出开始）
   ```

### 视觉示意

```
  [think]
  用户想要我分析项目结构。
  我应该先读取 src 目录，然后检查关键文件。
  让我先列出文件...
```

### 验证

```bash
# 1. 发送包含多行推理文本的 SSE agent_think 事件
# 2. 验证灰色/斜体渲染
# 3. 发送第二个 agent_think 事件——验证之前的内容已被替换
# 4. 发送 message_chunk——验证 ThinkingBlock 已被移除
```

### 提交信息

```
feat: add ThinkingBlock TUI component for agent_think SSE rendering
```

---

## 任务 6.7：TUI ToolCallCard 组件

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ToolCallCard.tsx`

**原因：**
工具调用（操作→观察）是代理交互的核心。一个视觉上独特的卡片，带有彩色边框、工具名称、参数和结果，使其易于扫描。黄色表示操作（进行中），绿色表示成功，红色表示错误。

### 步骤

1. **创建 `ToolCallCard.tsx`**：

   ```tsx
   import { Box, Text } from "ink"

   interface ToolAction {
     name: string
     args: Record<string, unknown>
     id: string
   }

   interface ToolResult {
     name: string
     result: string
     error?: string
     id: string
   }

   interface Props {
     toolName: string
     args: Record<string, unknown>
     result?: string
     error?: string
     isRunning?: boolean
   }

   export function ToolCallCard({ toolName, args, result, error, isRunning }: Props) {
     const borderColor = error ? "red" : isRunning ? "yellow" : "green"

     return (
       <Box flexDirection="column" borderStyle="round" borderColor={borderColor} padding={1} marginY={1}>
         {/* 头部：工具名称 */}
         <Box>
           <Text bold color={borderColor}>
             {isRunning ? "▶" : error ? "✗" : "✓"}
           </Text>
           <Text bold> </Text>
           <Text bold color={borderColor}>{toolName}</Text>
           {isRunning && <Text dimColor> (运行中...)</Text>}
         </Box>

         {/* 参数 */}
         <Box flexDirection="column" paddingLeft={2}>
           <Text dimColor>参数：</Text>
           {Object.entries(args).map(([key, value]) => (
             <Text key={key} dimColor>
               {"  "}{key}: {typeof value === "string" ? value : JSON.stringify(value)}
             </Text>
           ))}
         </Box>

         {/* 结果（如果可用） */}
         {result !== undefined && (
           <Box flexDirection="column" paddingLeft={2} marginTop={1}>
             <Text dimColor>结果：</Text>
             <Text wrap="wrap">{result.slice(0, 500)}</Text>
             {result.length > 500 && <Text dimColor>... (截断，共 {result.length} 字符)</Text>}
           </Box>
         )}

         {/* 错误（如果有） */}
         {error && (
           <Box flexDirection="column" paddingLeft={2} marginTop={1}>
             <Text color="red">错误：{error}</Text>
           </Box>
         )}
       </Box>
     )
   }
   ```

2. **接入 StreamingChat**：

当 SSE `agent_action` 到达时：
```
→ 使用 isRunning=true 渲染 ToolCallCard
```
当 SSE `agent_observation` 到达时：
```
→ 使用 result 和 isRunning=false 更新同一个 ToolCallCard
```
当观察结果包含错误时：
```
→ 使用错误字符串和红色边框渲染 ToolCallCard
```

   这要求 StreamingChat 组件维护一个工具调用 ID 到当前状态的状态映射：

   ```tsx
   const [toolCalls, setToolCalls] = useState<Record<string, ToolCallState>>({})

   // 当 agent_action 时：
   setToolCalls(prev => ({
     ...prev,
     [data.id]: { name: data.name, args: data.args, isRunning: true }
   }))

   // 当 agent_observation 时：
   setToolCalls(prev => ({
     ...prev,
     [data.id]: { ...prev[data.id], result: data.result, error: data.error, isRunning: false }
   }))
   ```

3. **在组件树中渲染 ToolCallCard 列表**：

   ```tsx
   {Object.entries(toolCalls).map(([id, tc]) => (
     <ToolCallCard
       key={id}
       toolName={tc.name}
       args={tc.args}
       result={tc.result}
       error={tc.error}
       isRunning={tc.isRunning}
     />
   ))}
   ```

### 视觉示意

```
┌─ ▶ read_file (运行中...) ─────────────────────┐
│  参数：                                        │
│    path: src/main.py                            │
└─────────────────────────────────────────────────┘

┌─ ✓ read_file ──────────────────────────────────┐
│  参数：                                        │
│    path: src/main.py                            │
│                                                 │
│  结果：                                        │
│  import os                                      │
│  import sys                                     │
│  ...                                            │
└─────────────────────────────────────────────────┘
```

### 验证

```bash
# 1. 发送 agent_action SSE 事件
# 2. 验证 ToolCallCard 以黄色边框和"运行中..."指示符渲染
# 3. 发送对应的 agent_observation
# 4. 验证 ToolCallCard 更新：绿色边框，显示结果
# 5. 测试错误情况：带有 error 字段的 observation → 红色边框
```

### 提交信息

```
feat: add ToolCallCard TUI component with colored border and status transitions
```

---

## 任务 6.8：TUI 计划任务管理界面

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ScheduledTaskManager.tsx`

**原因：**
第 4 阶段引入了调度器，但 TUI 需要一个管理界面。用户需要无需 REST 调用即可列出、添加、切换和删除计划任务。

### 步骤

1. **在 `client.ts` 中创建 API 客户端方法**：

   ```typescript
   export interface ScheduledTask {
     id: string
     description: string
     prompt: string
     schedule: string
     enabled: boolean
     created_at: number
     last_run: string | null
   }

   export async function listScheduledTasks(baseUrl: string): Promise<ScheduledTask[]> {
     const res = await fetch(`${baseUrl}/scheduled-tasks`)
     if (!res.ok) throw new Error(`HTTP ${res.status}`)
     return res.json()
   }

   export async function createScheduledTask(baseUrl: string, task: {
     description: string
     prompt: string
     schedule: string
   }): Promise<ScheduledTask> {
     const res = await fetch(`${baseUrl}/scheduled-tasks`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify(task),
     })
     if (!res.ok) throw new Error(`HTTP ${res.status}`)
     return res.json()
   }

   export async function toggleScheduledTask(baseUrl: string, id: string): Promise<ScheduledTask> {
     const res = await fetch(`${baseUrl}/scheduled-tasks/${id}/toggle`, { method: "POST" })
     if (!res.ok) throw new Error(`HTTP ${res.status}`)
     return res.json()
   }

   export async function deleteScheduledTask(baseUrl: string, id: string): Promise<void> {
     const res = await fetch(`${baseUrl}/scheduled-tasks/${id}`, { method: "DELETE" })
     if (!res.ok) throw new Error(`HTTP ${res.status}`)
   }
   ```

2. **在后端创建 API 端点**（`server/app.py`）：

   ```python
   from nanoclaw.scheduler.repo import ScheduledTask, ScheduledTaskRepo
   from nanoclaw.server.deps import get_scheduled_task_repo

   @app.get("/scheduled-tasks")
   async def list_scheduled_tasks() -> list[dict[str, Any]]:
       repo = get_scheduled_task_repo()
       tasks = await repo.list_all()
       return [asdict(t) for t in tasks]

   @app.post("/scheduled-tasks")
   async def create_scheduled_task(req: CreateScheduledTaskRequest) -> dict[str, Any]:
       repo = get_scheduled_task_repo()
       task = ScheduledTask(
           id=str(uuid.uuid4()),
           description=req.description,
           prompt=req.prompt,
           schedule=req.schedule,
           enabled=True,
           created_at=time.time(),
       )
       return asdict(await repo.create(task))

   @app.post("/scheduled-tasks/{task_id}/toggle")
   async def toggle_scheduled_task(task_id: str) -> dict[str, Any]:
       repo = get_scheduled_task_repo()
       task = await repo.get(task_id)
       if task is None:
           raise HTTPException(404)
       task.enabled = not task.enabled
       await repo.update(task)
       return asdict(task)

   @app.delete("/scheduled-tasks/{task_id}")
   async def delete_scheduled_task(task_id: str) -> dict[str, str]:
       repo = get_scheduled_task_repo()
       await repo.delete(task_id)
       return {"deleted": task_id}
   ```

3. **创建 `ScheduledTaskManager.tsx`**：

   ```tsx
   import { Box, Text, useInput } from "ink"
   import { useState, useEffect } from "react"
   import TextInput from "ink-text-input"
   import { ScheduledTask, listScheduledTasks, createScheduledTask,
            toggleScheduledTask, deleteScheduledTask } from "../client.js"

   interface Props {
     baseUrl: string
     onClose: () => void
   }

   export function ScheduledTaskManager({ baseUrl, onClose }: Props) {
     const [tasks, setTasks] = useState<ScheduledTask[]>([])
     const [mode, setMode] = useState<"list" | "add" | "confirm-delete">("list")
     const [description, setDescription] = useState("")
     const [prompt, setPrompt] = useState("")
     const [schedule, setSchedule] = useState("")
     const [selectedId, setSelectedId] = useState<string | null>(null)
     const [loading, setLoading] = useState(true)
     const [error, setError] = useState<string | null>(null)

     useEffect(() => {
       loadTasks()
     }, [])

     async function loadTasks() {
       try {
         setLoading(true)
         const tasks = await listScheduledTasks(baseUrl)
         setTasks(tasks)
         setError(null)
       } catch (e: any) {
         setError(e.message)
       } finally {
         setLoading(false)
       }
     }

     async function handleAdd() {
       try {
         await createScheduledTask(baseUrl, { description, prompt, schedule })
         setDescription("")
         setPrompt("")
         setSchedule("")
         setMode("list")
         await loadTasks()
       } catch (e: any) {
         setError(e.message)
       }
     }

     async function handleToggle(id: string) {
       try {
         await toggleScheduledTask(baseUrl, id)
         await loadTasks()
       } catch (e: any) {
         setError(e.message)
       }
     }

     async function handleDelete(id: string) {
       try {
         await deleteScheduledTask(baseUrl, id)
         setSelectedId(null)
         setMode("list")
         await loadTasks()
       } catch (e: any) {
         setError(e.message)
       }
     }

     useInput((data, key) => {
       if (key.escape) {
         if (mode === "add") {
           setMode("list")
         } else if (mode === "confirm-delete") {
           setMode("list")
           setSelectedId(null)
         } else {
           onClose()
         }
       }
     })

     if (loading) {
       return <Text>加载中...</Text>
     }

     return (
       <Box flexDirection="column" padding={1}>
         <Text bold underline>计划任务</Text>
         <Text dimColor>按 'a' 添加，'t' 切换，'d' 删除，Esc 关闭</Text>

         {error && <Text color="red">{error}</Text>}

         {mode === "list" && (
           <>
             {tasks.length === 0 && <Text dimColor>暂无计划任务。</Text>}
             {tasks.map(task => (
               <Box key={task.id} marginY={1}>
                 <Text>
                   <Text color={task.enabled ? "green" : "red"}>
                     {task.enabled ? "●" : "○"}
                   </Text>
                   {" "}
                   <Text bold>{task.description}</Text>
                   {" "}
                   <Text dimColor>({task.schedule})</Text>
                   {" — "}
                   <Text dimColor>{task.prompt.slice(0, 60)}</Text>
                 </Text>
                 <LastRunLabel lastRun={task.last_run} />
               </Box>
             ))}
           </>
         )}

         {mode === "add" && (
           <AddForm
             description={description}
             prompt={prompt}
             schedule={schedule}
             onDescriptionChange={setDescription}
             onPromptChange={setPrompt}
             onScheduleChange={setSchedule}
             onSubmit={handleAdd}
           />
         )}

         {mode === "confirm-delete" && selectedId && (
           <Box flexDirection="column">
             <Text color="red">删除任务 {selectedId}？(y/N)</Text>
             <TextInput
               value=""
               onChange={() => {}}
               onSubmit={(value) => {
                 if (value.toLowerCase() === "y") {
                   handleDelete(selectedId)
                 }
                 setMode("list")
                 setSelectedId(null)
               }}
             />
           </Box>
         )}

         <ActionsBar
           mode={mode}
           onAdd={() => setMode("add")}
           onToggle={() => {/* 需要选中的任务 */}}
           onDelete={() => {/* 需要选中的任务 */}}
         />
       </Box>
     )
   }

   function LastRunLabel({ lastRun }: { lastRun: string | null }) {
     if (!lastRun) return <Text dimColor> (从未运行)</Text>
     return <Text dimColor> (上次运行: {lastRun})</Text>
   }

   function AddForm({ description, prompt, schedule, onDescriptionChange, onPromptChange, onScheduleChange, onSubmit }) {
     // ... 渲染三个 TextInput 字段和一个提交按钮
   }

   function ActionsBar({ mode, onAdd, onToggle, onDelete }) {
     // ... 在底部渲染键盘快捷键提示
   }
   ```

4. **将 ScheduledTaskManager 接入 app.tsx**：

   管理器由斜杠命令 `/schedule` 或专用菜单选项触发。激活时，它会替换主聊天视图：

   ```tsx
   // 在 app.tsx 中：
   const [showScheduler, setShowScheduler] = useState(false)

   // 当输入 "/schedule" 时：
   setShowScheduler(true)

   // 渲染时：
   {showScheduler ? (
     <ScheduledTaskManager baseUrl={config.baseUrl} onClose={() => setShowScheduler(false)} />
   ) : (
     // ... 普通聊天渲染
   )}
   ```

### 验证

```bash
# 1. 在 TUI 中，输入 "/schedule"
# 2. 验证 ScheduledTaskManager 出现并显示任务列表
# 3. 按 'a' → 填写描述、提示、调度信息 → 提交
# 4. 验证新任务出现在列表中
# 5. 按 't' → 验证任务切换启用/禁用
# 6. 按 'd' → 确认 → 验证任务消失
# 7. 按 Esc → 验证返回聊天视图
```

### 提交信息

```
feat: add ScheduledTaskManager TUI component with list/add/toggle/delete

同时添加后端 API 端点：GET/POST /scheduled-tasks、
POST /scheduled-tasks/:id/toggle、DELETE /scheduled-tasks/:id
```

---

## 阶段 6 总结

| 任务 | 描述 | 后端文件 | 前端文件 | 关键依赖 |
|------|------------|---------------|----------------|----------|
| 6.1 | 带指数退避的子任务重试 | `task_queue.py`, `redis_queue.py` | — | 阶段 1, 5 |
| 6.2 | 级联回滚补偿 | `collector.py`, `task_queue.py` | — | 6.1 |
| 6.3 | CANCELLED 传播 | `task_queue.py`, `redis_queue.py` | — | 6.1 |
| 6.4 | Collector 中的错误聚合 | `collector.py` | — | 6.2, 6.3 |
| 6.5 | PlanView 组件 | — | `PlanView.tsx` | 阶段 2 SSE 协议 |
| 6.6 | ThinkingBlock 组件 | — | `ThinkingBlock.tsx` | 阶段 1 SSE 协议 |
| 6.7 | ToolCallCard 组件 | — | `ToolCallCard.tsx` | 阶段 1 SSE 协议 |
| 6.8 | 计划任务管理 | `server/app.py` | `ScheduledTaskManager.tsx`, `client.ts` | 阶段 4, 5 |

**新增后端文件总数：** ~1（`collector.py` 可能是新建的，也可能已存在于阶段 2）
**新增前端文件总数：** ~4（`PlanView.tsx`, `ThinkingBlock.tsx`, `ToolCallCard.tsx`, `ScheduledTaskManager.tsx`）
**修改文件总数：** ~6（task_queue, redis_queue, collector, app.py, client.ts, types.ts）

## 执行顺序

任务 6.1-6.4 是后端弹性功能，应按顺序执行（每个任务建立在前一个之上）：

```
6.1 重试 → 6.2 补偿 → 6.3 取消 → 6.4 错误聚合
```

任务 6.5-6.8 是前端功能，可在 6.4 之后并行执行，但推荐顺序考虑到了复杂度的递增：

```
6.5 PlanView → 6.6 ThinkingBlock → 6.7 ToolCallCard → 6.8 ScheduledTaskManager
```

所有 8 个任务在后端和前端层面之间独立，因此后端开发代理和前端开发代理可以在 6.1 完成后并行工作。
