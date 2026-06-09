# 阶段 6：弹性与前端优化（更新版）

**日期：** 2026-06-09
**状态：** 草案
**依赖：** 第 1-5 阶段（所有存储 + Docker 基础设施已就位）+ Agent 架构设计的 Checker 子系统

## 概述

通过完整 Checker 子系统（基于轨迹的反馈循环）替代简单的重试逻辑，增加系统弹性。使用专门用于计划可视化、思考块、工具调用卡片和计划任务管理的组件来优化 TUI。

**核心变更**：简单的 max_retries + 指数退避被替换为完整的 Checker 反馈循环：执行 → Check → 失败分类 → 重新入队/重新规划 → 预算检查。每个子任务在执行后都经过校验，失败后根据原因分类（execution vs planning），分别触发重新入队或重新规划。

## 设计决策

- **为什么 Checker 替代简单重试**：简单重试对所有失败一视同仁，但实际需要区分"执行有问题"（重新入队重试）和"任务定义不合理"（需要重新规划）。Checker 闭环提供结构化失败处理。
- **为什么 TrajectoryLogger 写本地文件**：轨迹文件流式追加 O(1)/步，按需读取不截断。仅在 Check 失败时才被 LLM 读取（分类判定），总 IO 开销可控。同时作为未来 Trajectory RL 的数据基础。
- **为什么 RubricValidator 独立于 Checker**：Planner 生成的评分标准可能和任务需求不匹配。校验节点在 rubric 投入使用前独立验证，保证后续 Check 流程的质量。
- **为什么 IterationBudget 使用两层级联上限**：单个子任务可能有自己的重试上限（per_subtask_max），全局也需要总迭代次数限制（global_max）防止无限循环。使用 asyncio.Lock 保证并发安全。
- **为什么失败分类规则优先，LLM 兜底**：timeout 必然是 planning 问题，exit code 非零是 execution 问题，输出为空是 execution。这些规则 100% 确定的事情不需要 LLM。只有规则覆盖不到的模糊情况再调用 LLM，保证控制流确定性并降低成本。
- **为什么在 TaskQueue 级别进行重试**：TaskQueue 已经编排了任务生命周期（出队/完成/失败）。在此处添加重试逻辑是单一控制点——每个工作线程自动获得重试支持，无需单独修改工作线程。
- **为什么补偿与重试分开**：重试尝试重新执行同一个任务。补偿则执行不同的操作（撤销副作用）。达到迭代上限仍然失败的任务需要补偿，而不是另一次重试。
- **为什么 COMPENSATION_FAILED 是终止状态**：补偿本身可能失败（文件被锁定、已删除）。当发生时，系统无法自动恢复——需要人工干预。明确的状态在 UI 中清晰地标识这一点。
- **为什么 PlanView 渲染为缩进树**：Ink 没有原生树控件。使用带 `paddingLeft` 的 `Box` 按深度层级排列是最简单的方法。连接线（`│`、`├──`、`└──`）是附加的优化，而非核心功能。
- **为什么 ThinkingBlock 使用 Ink 的 `dimColor`**：灰色/斜体文本是公认的内部独白与最终输出的区别方式。Ink 的 `<Text dimColor>` 和 `<Text italic>` 无需自定义样式即可处理。
- **为什么 ToolCallCard 使用彩色边框**：Ink 通过 `borderColor` 支持彩色 `Box` 边框。不同的边框颜色用于操作（黄色）与观察（成功为绿色，出错为红色）可提供即时的视觉扫描。在 Checker 集成后可进一步用紫色表示 check 中的标准。

---

## 任务 6.1：TrajectoryLogger + RubricValidator + IterationBudget（Checker 基础设施）

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/checker.py`（新建）

**原因：**
Checker 子系统需要三个基础组件。TrajectoryLogger 记录每个子任务的完整执行轨迹到本地文件；RubricValidator 验证 Planner 生成的评分标准质量；IterationBudget 管控两层级联的迭代次数上限。三者是后续所有 Checker 功能的前置依赖。

### 步骤

1. **实现 TrajectoryLogger 类**——将执行轨迹流式追加写入本地 JSONL 文件：

   ```python
   from pathlib import Path

   class TrajectoryLogger:
       """将执行轨迹流式写入本地 JSONL 文件"""

       def __init__(self, base_dir: str) -> None:
           self.base_dir = Path(base_dir) / "trajectories"

       async def append_step(
           self, session_id: str, subtask_id: str, step: dict
       ) -> None:
           """追加一步到轨迹文件（O(1) 磁盘操作）"""
           path = self.base_dir / session_id
           path.mkdir(parents=True, exist_ok=True)
           file_path = path / f"{subtask_id}.jsonl"
           line = json.dumps(step, ensure_ascii=False) + "\n"
           async with aiofiles.open(file_path, mode="a") as f:
               await f.write(line)

       async def read_full(
           self, session_id: str, subtask_id: str
       ) -> list[dict]:
           """读取完整轨迹。用于失败分类时 LLM 直接读取。"""
           path = self.base_dir / session_id / f"{subtask_id}.jsonl"
           if not path.exists():
               return []
           async with aiofiles.open(path, mode="r") as f:
               content = await f.read()
           return [json.loads(line) for line in content.strip().split("\n") if line]

       async def cleanup(self, session_id: str, ttl_days: int = 30) -> None:
           """清理超过 TTL 的轨迹文件"""
           # ... 删除 session_id 下所有早于 ttl_days 的轨迹文件
   ```

   轨迹文件格式（`.nanoclaw/trajectories/{session_id}/{subtask_id}.jsonl`）：

   ```jsonl
   {"step": 1, "type": "think", "content": "..."}
   {"step": 1, "type": "action", "tool": "read_file", "args": {"path": "..."}}
   {"step": 1, "type": "observation", "result": "..."}
   ```

2. **实现 RubricValidator 类**——验证 Rubric 是否合理：

   ```python
   class RubricValidator:
       """验证 Rubric 是否合理、是否覆盖了任务的关键方面"""

       def validate(
           self,
           subtask: Subtask,
           rubric: Rubric,
           user_request: str,
       ) -> list[str]:
           """返回需要修正的问题列表，空列表表示通过"""
           issues: list[str] = []

           # 检查：rubric 是否为空
           if not rubric.criteria:
               issues.append("评分标准为空，请至少提供一条标准")

           # 检查：非纯工具操作类 subtask 不应全是 [rule]
           if not subtask.tools_needed and rubric.is_rule_only:
               issues.append("该子任务涉及推理/判断，建议至少包含一条 [llm] 标准")

           # 检查：标准描述是否可判定（避免模糊表述）
           vague_keywords = ["好", "正确", "合理", "适当"]
           for c in rubric.criteria:
               for kw in vague_keywords:
                   if kw in c.text:
                       issues.append(f"标准描述可能过于模糊：'{c.text}'（含'{kw}'）")
                       break

           return issues
   ```

3. **实现 IterationBudget 类**——两层级联的迭代次数管控：

   ```python
   class IterationBudget:
       """两层级联的迭代次数管控。使用锁保护并发访问。"""

       def __init__(
           self,
           per_subtask_max: int = 3,     # 单个 subtask 最多重试 3 次
           global_max: int = 10,         # 全局所有 retry+replan 最多 10 次
       ) -> None:
           self.per_subtask_max = per_subtask_max
           self.global_max = global_max
           self._per_subtask_counts: dict[str, int] = {}
           self._global_count = 0
           self._lock = asyncio.Lock()

       async def try_consume(self, subtask_id: str) -> bool:
           """尝试消耗一次迭代机会，返回是否允许继续。
           此方法为权威决策者，不提供外部 exhaust 检查。"""
           async with self._lock:
               if self._global_count >= self.global_max:
                   return False           # 全局上限已到
               subtask_count = self._per_subtask_counts.get(subtask_id, 0)
               if subtask_count >= self.per_subtask_max:
                   return False           # subtask 上限已到
               self._per_subtask_counts[subtask_id] = subtask_count + 1
               self._global_count += 1
               return True
   ```

   当 `try_consume()` 返回 `False` 时，SSE 推送 `iteration_exhausted` 事件，用户收到通知后选择放弃或调整参数继续。

### 验证

```python
# 1. TrajectoryLogger: 创建实例，追加 3 步轨迹，读取完整轨迹验证内容
# 2. RubricValidator: 传入空 rubric → 返回问题列表；传入合理 rubric → 空列表
# 3. IterationBudget: per_subtask_max=1, global_max=2 → 两个不同 subtask 各消耗 1 次 → 第三次返回 False
```

### 提交信息

```
feat: add Checker infrastructure — TrajectoryLogger, RubricValidator, IterationBudget
```

---

## 任务 6.2：Checker 路由 + Worker check_node 集成

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/checker.py`（续写）
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/worker_pool.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/models/task.py`（新增 Rubric/Criterion/CheckResult 模型）

**原因：**
Worker 执行完子任务后，需要经过 Checker 校验。Checker 根据 Rubric 的 check_type 路由到规则检查或 LLM 检查。Check 结果是后续失败分类和重试/重规划决策的输入。

### 步骤

1. **向 models/task.py 添加评分标准相关模型**（如果尚未存在）：

   ```python
   @dataclass
   class Criterion:
       """单个评判标准"""
       text: str                           # 标准描述，如 "README.md 文件已创建"
       check_type: Literal["rule", "llm"]  # "rule" 用代码校验，"llm" 走 LLM 评判

   @dataclass
   class Rubric:
       """评分标准 — 判断 subtask 是否完成的标准"""
       criteria: list[Criterion]           # 评判标准列表
       require_all_pass: bool = True       # True=全部通过才算通过，False=多数通过即可

       @property
       def is_rule_only(self) -> bool:
           return all(c.check_type == "rule" for c in self.criteria)

   @dataclass
   class CheckResult:
       """检查结果"""
       passed: bool
       criteria_results: list[tuple[str, bool, str]]  # (标准描述, PASS/FAIL, 反馈)
       check_feedback: str                            # 总体 check 反馈文本
   ```

2. **在 checker.py 中实现 Checker 类**——按 Rubric 路由检查：

   ```python
   class Checker:
       """按 Rubric 的 check_type 路由到对应的 check 方式"""

       def __init__(
           self,
           trajectory_logger: TrajectoryLogger,
           llm: Any,  # LLM 实例，用于 rubric LLM check
       ) -> None:
           self.trajectory_logger = trajectory_logger
           self.llm = llm

       async def check(
           self,
           subtask: Subtask,
           result: str,
           session_id: str,
       ) -> CheckResult:
           if subtask.rubric.is_rule_only:
               return await self._rule_check(subtask, result)
           else:
               return await self._rubric_llm_check(subtask, result, session_id)

       async def _rule_check(
           self,
           subtask: Subtask,
           result: str,
       ) -> CheckResult:
           """规则检查：exit code、文件存在、非空等硬约束。不调 LLM。"""
           criteria_results: list[tuple[str, bool, str]] = []
           all_passed = True

           for criterion in subtask.rubric.criteria:
               passed, feedback = self._eval_rule(criterion, subtask, result)
               if not passed:
                   all_passed = False
               criteria_results.append((criterion.text, passed, feedback))

           check_feedback = "全部通过" if all_passed else f"{sum(1 for _, p, _ in criteria_results if not p)} 条标准未通过"
           return CheckResult(
               passed=all_passed,
               criteria_results=criteria_results,
               check_feedback=check_feedback,
           )

       def _eval_rule(
           self,
           criterion: Criterion,
           subtask: Subtask,
           result: str,
       ) -> tuple[bool, str]:
           """执行单条规则检查。可扩展的规则库。"""
           text = criterion.text
           # 内置规则库（可扩展）
           if "文件已创建" in text or "文件存在" in text:
               # 解析文件名，检查文件是否存在
               import re
               match = re.search(r'[\w/.-]+\.[a-z]+', text)
               if match:
                   path = Path(match.group())
                   if path.exists():
                       return True, "文件存在"
                   return False, f"文件不存在: {path}"
           if "非空" in text or "不为空" in text:
               if result and result.strip():
                   return True, "结果非空"
               return False, "结果为空"
           if "exit code" in text.lower() or "退出码" in text:
               if subtask.error is None:
                   return True, "退出码为 0"
               return False, f"退出码非零: {subtask.error}"
           # 未知规则 → 默认通过（依赖 Planner 生成可判定的规则）
           return True, "规则无法判定（跳过）"

       async def _rubric_llm_check(
           self,
           subtask: Subtask,
           result: str,
           session_id: str,
       ) -> CheckResult:
           """Rubric + LLM 检查：把 subtask 描述 + rubric + result 喂给 LLM。
           对每条标准评分：PASS / FAIL。"""
           # 读取完整轨迹作为上下文
           trajectory = await self.trajectory_logger.read_full(session_id, subtask.id)
           trajectory_str = json.dumps(trajectory, ensure_ascii=False, indent=2)

           prompt = f"""请根据以下评分标准判断子任务是否成功完成。

   子任务描述：{subtask.description}
   执行结果：{result[:2000]}
   执行轨迹：{trajectory_str[:4000]}

   评分标准：
   {chr(10).join(f'- [{i+1}] {c.text}（{"规则检查" if c.check_type == "rule" else "LLM 评判"}）' for i, c in enumerate(subtask.rubric.criteria))}

   对每条标准，输出 PASS 或 FAIL，并给出简短理由。
   格式：标准序号: PASS/FAIL - 理由"""
           # ... 调用 LLM，解析返回结果 ...

           # 解析 LLM 返回，构建 criteria_results
           criteria_results: list[tuple[str, bool, str]] = []
           all_passed = True
           # ... 解析逻辑 ...

           return CheckResult(
               passed=all_passed,
               criteria_results=criteria_results,
               check_feedback="...",
           )
   ```

3. **修改 Worker 内部执行流程**——集成 Checker：

   ```
   Worker 从 TaskQueue dequeue() → Subtask
     → ReAct 循环执行（写 trace 到 Step 和 TrajectoryLogger）
     → 执行完毕 → 得到 result
     → [Checker.check()]：
         ├─ PASS → TaskQueue.complete(id, result)
         └─ FAIL → 进入失败分类 → 重新入队或重新规划
   ```

   在 `worker_pool.py` 中：

   ```python
   class Worker:
       def __init__(self, checker: Checker, trajectory_logger: TrajectoryLogger, ...):
           self.checker = checker
           self.trajectory_logger = trajectory_logger

       async def run(self) -> None:
           while True:
               subtask = await self.queue.dequeue()
               if subtask is None:
                   continue

               # 执行 ReAct 循环
               result = await self._execute_react(subtask)

               # Check 步骤
               check_result = await self.checker.check(subtask, result, self.session_id)

               if check_result.passed:
                   await self.queue.complete(subtask.id, result)
               else:
                   # Check 失败 → 失败分类 + 重试/重规划
                   await self._handle_check_failure(subtask, result, check_result)
   ```

### 验证

```python
# 1. 创建一个纯规则 rubric（如"文件存在"）→ 验证 Checker 路由到 _rule_check
# 2. 创建一个混合 rubric（含 LLM 标准）→ 验证 Checker 路由到 _rubric_llm_check
# 3. 规则检查通过 → CheckResult.passed=True
# 4. 规则检查不通过 → CheckResult.passed=False + 具体反馈
```

### 提交信息

```
feat: add Checker routing and Worker check_node integration
```

---

## 任务 6.3：失败分类（规则优先，LLM 兜底）

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/checker.py`（续写）

**原因：**
Check 失败后，需要判断失败类型以决定下一步。规则优先保证控制流确定性，LLM 兜底处理规则覆盖不到的模糊情况。

### 步骤

1. **实现失败分类函数**——规则优先，LLM 兜底：

   ```python
   FAILURE_TYPE_EXECUTION = "execution"
   FAILURE_TYPE_PLANNING = "planning"

   class FailureClassifier:
       """失败分类——规则优先，LLM 兜底"""

       def __init__(self, llm: Any | None = None) -> None:
           self.llm = llm

       async def classify(
           self,
           subtask: Subtask,
           result: str,
           check_result: CheckResult,
           trajectory: list[dict],
       ) -> str:
           """返回失败类型：'execution' 或 'planning'"""

           # 规则 1：timeout → planning（任务定义不合理）
           if subtask.error and "timeout" in subtask.error.lower():
               return FAILURE_TYPE_PLANNING

           # 规则 2：exit code 非零 → execution（执行出问题了）
           if subtask.error and "exit code" in subtask.error.lower():
               return FAILURE_TYPE_EXECUTION

           # 规则 3：输出为空 → execution
           if not result or not result.strip():
               return FAILURE_TYPE_EXECUTION

           # 规则 4：LLM rubric check 全部未通过 → planning
           passed_count = sum(1 for _, p, _ in check_result.criteria_results if p)
           if passed_count == 0 and len(check_result.criteria_results) > 1:
               return FAILURE_TYPE_PLANNING

           # 兜底：LLM 判断（当 LLM 可用时）
           if self.llm is not None:
               return await self._llm_classify(subtask, result, check_result, trajectory)

           # 无 LLM 时的默认降级
           return FAILURE_TYPE_EXECUTION

       async def _llm_classify(
           self,
           subtask: Subtask,
           result: str,
           check_result: CheckResult,
           trajectory: list[dict],
       ) -> str:
           """用 LLM 判断失败类型"""
           trajectory_str = json.dumps(trajectory, ensure_ascii=False, indent=2)[:3000]
           prompt = f"""子任务执行失败，请判断失败原因类型。

   子任务描述：{subtask.description}
   执行结果：{result[:1000]}
   Check 反馈：{check_result.check_feedback}
   执行轨迹：{trajectory_str}

   失败类型：
   - execution：执行出问题了（命令写错、参数不对、环境问题等），修正后可以重新执行
   - planning：任务定义不合理（范围太大、缺少关键信息等），需要重新规划该子任务

   请只返回类型名称：execution 或 planning"""
           # ... 调用 LLM，解析返回 ...
   ```

2. **打包 CheckerFeedback**——失败时传递给后续流程的完整上下文：

   ```python
   @dataclass
   class CheckerFeedback:
       """check 失败时打包给 LLM 的全部上下文"""
       subtask: Subtask
       rubric: Rubric
       result: str
       check_result: CheckResult          # 哪些标准没通过 + 为什么
       trace_path: str                    # 轨迹文件路径（LLM 自行读取）
       user_request: str                  # 用户原始需求
   ```

3. **完整失败处理流程**——在 Worker 的 `_handle_check_failure` 中串联：

   ```python
   async def _handle_check_failure(
       self,
       subtask: Subtask,
       result: str,
       check_result: CheckResult,
   ) -> None:
       # 1. 失败分类
       trajectory = await self.trajectory_logger.read_full(self.session_id, subtask.id)
       failure_type = await self.failure_classifier.classify(subtask, result, check_result, trajectory)

       # 2. 打包反馈上下文
       feedback = CheckerFeedback(
           subtask=subtask,
           rubric=subtask.rubric,
           result=result,
           check_result=check_result,
           trace_path=str(self.trajectory_logger.base_dir / self.session_id / f"{subtask.id}.jsonl"),
           user_request=self.user_request,
       )

       # 3. 检查迭代预算
       if not await self.budget.try_consume(subtask.id):
           # 预算耗尽 → 通知用户介入
           await self.sse_manager.emit("iteration_exhausted", {
               "session_id": self.session_id,
               "failed_subtask_ids": [subtask.id],
               "trajectory_paths": [feedback.trace_path],
           })
           return

       # 4. 根据失败类型路由
       if failure_type == FAILURE_TYPE_EXECUTION:
           # 打包 CheckerFeedback + 修正指导 → 重新入队
           await self.queue.fail(subtask.id, check_result.check_feedback)
       elif failure_type == FAILURE_TYPE_PLANNING:
           # 打包 CheckerFeedback → 触发 Planner 重新生成该 subtask
           await self._trigger_replan(subtask, feedback)
   ```

### 验证

```python
# 1. subtask.error="timeout" → classify 返回 "planning"
# 2. subtask.error="exit code 1" → classify 返回 "execution"
# 3. result="" → classify 返回 "execution"
# 4. 模糊情况 → LLM classify（或默认降级 "execution"）
```

### 提交信息

```
feat: add rule-first failure classification with LLM fallback
```

---

## 任务 6.4：子任务重试逻辑（Checker 驱动）

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py` (MemoryQueue)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py` (RedisQueue)

**原因：**
当 Checker 判定失败类型为 "execution" 时，子任务需要重新入队重试。与旧方案不同，重试不再由 `fail()` 方法内的简单计数驱动，而是由 Checker 子系统外部驱动——任务先被标记为 FAILED，然后 Checker 根据失败分类结果调用 `_requeue_after_delay` 或 `_zrequeue_after_delay`。

### 步骤

1. **向 Subtask 模型添加重试字段**（应已存在于阶段 1）：

   ```python
   @dataclass
   class Subtask:
       # ... 现有字段
       max_retries: int = 3
       retry_count: int = 0
       retry_delay_base: float = 1.0  # 秒——指数退避基数
   ```

2. **修改 MemoryQueue.fail()**——取消内置重试逻辑，只保留标记失败和级联取消：

   ```python
   async def fail(self, task_id: str, error: str) -> None:
       task = self._tasks[task_id]
       task.error = error
       task.status = TaskStatus.FAILED
       self._completed_count += 1
       self._events[task_id].set()
       # 级联取消下游
       await self._cascade_cancel(task_id)
       await self._check_all_done()
   ```

   `fail()` 不再内部管理重试。重试由外部的 Checker 子系统驱动。

3. **添加公共重入队方法**（Checker 在判定为 "execution" 后调用）：

   ```python
   async def requeue_for_retry(self, task_id: str) -> None:
       """Checker 驱动：将任务重置为 PENDING 并重新入队。"""
       task = self._tasks[task_id]
       task.status = TaskStatus.PENDING
       task.retry_count += 1
       # 使用指数退避延迟重新入队
       delay = task.retry_delay_base * (2 ** (task.retry_count - 1))
       asyncio.create_task(self._requeue_after_delay(task_id, delay))
   ```

4. **添加 `_requeue_after_delay` 辅助方法**：

   ```python
   async def _requeue_after_delay(self, task_id: str, delay: float) -> None:
       await asyncio.sleep(delay)
       task = self._tasks[task_id]
       if task.status != TaskStatus.PENDING:
           return  # 状态已被外部更改（如取消）
       # 重新检查依赖（延迟期间可能已变化）
       if all(self._tasks[d].status == TaskStatus.SUCCEEDED for d in task.depends_on):
           await self._ready.put(task)  # 重新入队到就绪队列
       else:
           # 依赖已不满足
           task.status = TaskStatus.FAILED
           self._completed_count += 1
           self._events[task_id].set()
           await self._cascade_cancel(task_id)
           await self._check_all_done()
   ```

5. **为 RedisQueue 添加对应的 `requeue_for_retry` 和 `_zrequeue_after_delay`**：

   ```python
   async def requeue_for_retry(self, task_id: str) -> None:
       task = self._tasks[task_id]
       task.retry_count += 1
       delay = task.retry_delay_base * (2 ** (task.retry_count - 1))
       # 重置状态
       redis = await get_redis()
       await redis.hset(self._task_key(task_id), "status", TaskStatus.PENDING.value)
       # 延迟重新入队
       await self._zrequeue_after_delay(task_id, delay)
   ```

6. **`dequeue()` 轮询延迟队列**（与旧方案一致）：

   ```python
   async def dequeue(self) -> Subtask | None:
       redis = await get_redis()
       # 首先，检查延迟队列中是否有就绪的重试任务
       delayed_key = f"{self.KEY_PREFIX}{self.session_id}:delayed"
       now = time.time()
       ready = await redis.zrangebyscore(delayed_key, "-inf", now)
       for task_id in ready:
           await redis.zrem(delayed_key, task_id)
           await redis.lpush(self._q, task_id)
       # 然后正常出队
       result = await redis.brpop(self._q, timeout=5)
       # ... 出队的其余部分
   ```

### Checker 驱动重试流程

```
Worker 执行完毕 → Checker.check() 失败
  → 失败分类判定 "execution"
  → IterationBudget.try_consume() 检查
  → Queue.requeue_for_retry(task_id)
    → status = PENDING
    → retry_count += 1
    → 指数退避延迟
    → 重新入队 ready 队列
  → Worker dequeue 继续执行
```

对比旧流程（`fail()` 内部自动重试去除后，`fail()` 只负责标记失败和级联取消）：

```
PENDING → RUNNING → FAILED
  → Checker 判定 execution → PENDING → RUNNING（重试循环）
  → Checker 判定 planning → 触发 Planner 重新生成
  → IterationBudget 耗尽 → iteration_exhausted SSE → 用户介入
```

### 验证

```python
# 1. 创建一个 subtask，Checker 判定 "execution"
# 2. 调用 queue.requeue_for_retry()
# 3. 验证 task.retry_count += 1
# 4. 验证 task.status == PENDING
# 5. 延迟后验证 task 回到就绪队列
```

### 提交信息

```
feat: replace built-in retry with Checker-driven requeue (MemoryQueue + RedisQueue)
```

---

## 任务 6.5：级联失败时的补偿回滚

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/models/task.py`

**原因：**
当子任务在耗尽迭代预算后失败时，任何已经执行并产生副作用（如文件写入或 API 调用）的下游任务都需要补偿/回滚。EffectLog 跟踪这些副作用，而收集器节点在所有任务完成后触发补偿。

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

           # 阶段 2：构建错误摘要（含 check 结果，见任务 6.7）
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
                   tool = self.tool_registry.get(subtask.compensation)
                   if tool:
                       await tool.run(subtask)
                   else:
                       import subprocess
                       subprocess.run(subtask.compensation, shell=True, check=True)
                   subtask.status = TaskStatus.COMPENSATED
                   return
               except Exception as e:
                   subtask.compensation_attempts += 1
                   if attempt < subtask.compensation_max_attempts - 1:
                       await asyncio.sleep(1.0 * (2 ** attempt))
                   else:
                       subtask.status = TaskStatus.COMPENSATION_FAILED
                       subtask.error = f"补偿在 {attempt+1} 次尝试后失败: {e}"

       def _is_downstream_of_any(
           self, plan, task_id: str, failed_tasks: list
       ) -> bool:
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
               for st in plan.subtasks:
                   if current.id in st.depends_on:
                       stack.append(st)
           return False
   ```

### 补偿流程

```
Task A 在迭代预算耗尽后失败（Checker 判定 planning 但预算不足）
  → Queue.fail() 将 Task A 标记为 FAILED
  → Queue.fail() 级联：Task B（A 的下游）→ CANCELLED
  → Task C（B 的下游，已 SUCCEEDED 并创建文件）→ 保持 SUCCEEDED
  → 等待所有任务完成
  → Collector node:
      ✓ 识别出任务 C 位于失败任务 A 的下游
      ✓ 任务 C 的补偿为 "rm -rf output/"
      ✓ Collector 执行补偿
      ✓ 任务 C 状态 → COMPENSATED
  → 最终摘要包含 Check 失败信息和补偿状态
```

### 验证

```python
# 1. 创建计划：Task A → Task B → Task C
# 2. Task A 的 budget 消耗完后仍失败
# 3. Task C 的 compensation="cleanup_command"
# 4. 运行计划
# 5. 验证：
#    - Task A: FAILED
#    - Task B: CANCELLED
#    - Task C: COMPENSATED
```

### 提交信息

```
feat: add compensation rollback for downstream tasks on cascading failure
```

---

## 任务 6.6：上游失败时 CANCELLED 传播

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_queue.py` (MemoryQueue)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py` (RedisQueue)

**原因：**
当子任务失败（迭代预算耗尽后）时，所有依赖它的下游任务应立即标记为 CANCELLED。否则，下游工作线程会浪费时间执行输入已无效的任务。

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
               continue
           task.status = TaskStatus.CANCELLED
           task.error = f"已取消：上游任务 {task_id} 失败"
           self._completed_count += 1
           self._events[downstream].set()
           queue.extend(self._rdag.get(downstream, []))
   ```

   关键点：
   - 使用 BFS（迭代式，非递归）以避免 Python 递归深度限制。
   - 仅将 PENDING 状态的任务标记为 CANCELLED。如果任务已经在 RUNNING 状态，它将继续运行——但其结果将由 collector 丢弃。
   - 每个取消的任务设置其 Event，以便 `wait_for_all()` 正常推进。

2. **在 RedisQueue 中实现 `_cascade_cancel`**：

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

3. **确保 `complete()` 不会重新入队已取消任务的下游任务**：

   ```python
   async def complete(self, task_id: str, result: str) -> None:
       # ... 现有代码 ...
       for downstream in self._rdag.get(task_id, []):
           deps = self._dag[downstream]
           dep_statuses = [self._tasks[d].status for d in deps]
           if any(s == TaskStatus.FAILED for s in dep_statuses):
               await self._cascade_cancel(task_id)
           elif all(s == TaskStatus.SUCCEEDED for s in dep_statuses):
               await self._ready.put(self._tasks[downstream])
   ```

### 取消流程

```
1. Worker 执行 subtask → Checker FAIL → 预算耗尽 → Queue.fail()
2. Queue.fail() 设置 Task A → FAILED
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
# 2. 让任务 A 在预算耗尽后失败
# 3. 验证 B、C、D 均为 CANCELLED
# 4. 确认 completed_count == 4
```

### 提交信息

```
feat: add transitive CANCELLED propagation on upstream task failure
```

---

## 任务 6.7：Collector 节点中的错误聚合（含 Check 结果）

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py`

**原因：**
当多个子任务失败时，用户需要查看结构化的错误报告。错误聚合收集所有失败原因、Check 结果和补偿状态。同时更新 PlanView 以展示每个子任务的检查状态（PASS/FAIL per criterion）。

### 步骤

1. **在 CollectorNode 中实现 `_build_summary`——含 Check 结果统计**：

   ```python
   def _build_summary(self, plan: TaskPlan, errors: list[str]) -> str:
       total = len(plan.subtasks)
       succeeded = sum(1 for s in plan.subtasks if s.status == TaskStatus.SUCCEEDED)
       compensated = sum(1 for s in plan.subtasks if s.status == TaskStatus.COMPENSATED)
       failed = sum(1 for s in plan.subtasks if s.status == TaskStatus.FAILED)
       cancelled = sum(1 for s in plan.subtasks if s.status == TaskStatus.CANCELLED)
       comp_failed = sum(1 for s in plan.subtasks if s.status == TaskStatus.COMPENSATION_FAILED)
       # 新增：Check 结果统计
       check_passed = sum(1 for s in plan.subtasks
                          if hasattr(s, 'check_result') and s.check_result and s.check_result.passed)
       check_failed = sum(1 for s in plan.subtasks
                          if hasattr(s, 'check_result') and s.check_result and not s.check_result.passed)

       lines = []
       lines.append(f"计划完成：{succeeded} 成功，{failed} 失败，"
                    f"{cancelled} 已取消，{compensated} 已补偿，"
                    f"{comp_failed} 补偿失败（共 {total} 个）")

       # 新增：Check 统计
       if check_passed or check_failed:
           lines.append(f"质量检查：{check_passed} 通过，{check_failed} 未通过")
       lines.append("")

       # 显示每个子任务的 Check 详情
       if check_failed > 0:
           lines.append("### 检查失败详情")
           for task in plan.subtasks:
               if hasattr(task, 'check_result') and task.check_result and not task.check_result.passed:
                   lines.append(f"  [{task.id}] {task.description}")
                   for criterion_text, passed, feedback in task.check_result.criteria_results:
                       icon = "✓" if passed else "✗"
                       lines.append(f"    {icon} {criterion_text}")
                       if not passed:
                           lines.append(f"       → {feedback}")

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

2. **为 collector 输出接入 SSE 事件**——针对 Check 状态、补偿状态发出 SSE：

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
       # 新增：推送 Check 结果
       if hasattr(task, 'check_result') and task.check_result:
           await sse_manager.emit("check_result", {
               "task_id": task.id,
               "passed": task.check_result.passed,
               "criteria": [
                   {"text": ct, "passed": cp, "feedback": cf}
                   for ct, cp, cf in task.check_result.criteria_results
               ],
           })
   ```

### 验证

```python
# 1. 运行一个计划，其中 5 个子任务中有 2 个 Check 失败
# 2. 检查 collector 输出：
#    - 包含"计划完成：..."统计
#    - 包含"质量检查：3 通过，2 未通过"
#    - 列出每个失败的 Check 标准及反馈
# 3. 验证 SSE 事件包含 check_result 事件
```

### 提交信息

```
feat: add error aggregation with Check results in CollectorNode
```

---

## 任务 6.8：TUI PlanView 组件——任务依赖树 + Check 状态

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/PlanView.tsx`

**原因：**
当执行复杂计划时，用户需要查看任务树、依赖关系和实时状态更新。同时需要展示每个子任务的 Check 结果（PASS/FAIL 状态及具体哪条标准未通过）。

### 步骤

1. **更新类型定义**——添加 Check 相关接口：

   ```typescript
   export interface CriterionResult {
     text: string
     passed: boolean
     feedback: string
   }

   export interface CheckResult {
     passed: boolean
     criteria: CriterionResult[]
     check_feedback: string
   }

   export interface SubtaskStatus {
     id: string
     description: string
     status: TaskStatusValue
     depends_on: string[]
     check_result?: CheckResult  // 可选，check 完成后填充
   }
   ```

2. **创建 `PlanView.tsx`——含 Check 标注**：

   ```tsx
   import { Box, Text } from "ink"
   import { SubtaskStatus, TaskStatusValue, CheckResult } from "../types.js"

   interface Props {
     tasks: SubtaskStatus[]
   }

   // 从 DAG 构建树的层级（与旧方案相同）
   function buildTree(tasks: SubtaskStatus[]): number[] {
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

   function CheckIndicator({ checkResult }: { checkResult?: CheckResult }) {
     if (!checkResult) return null
     return (
       <Text color={checkResult.passed ? "green" : "red"}>
         {" "}[check: {checkResult.passed ? "PASS" : "FAIL"}]
       </Text>
     )
   }

   export function PlanView({ tasks }: Props) {
     const depths = buildTree(tasks)

     return (
       <Box flexDirection="column" paddingLeft={0}>
         <Text bold underline>任务计划</Text>
         {tasks.map((task, i) => (
           <Box key={task.id} flexDirection="column" paddingLeft={depths[i] * 2}>
             <Box>
               <Text>
                 <Text color={statusColor(task.status)}>
                   {statusIcon(task.status)}
                 </Text>
                 {" "}
                 <Text dimColor>{task.id}</Text>
                 {" "}
                 <Text>{task.description}</Text>
                 <CheckIndicator checkResult={task.check_result} />
               </Text>
             </Box>
             {/* 展开 Check 失败的详细标准 */}
             {task.check_result && !task.check_result.passed && (
               <Box flexDirection="column" paddingLeft={4}>
                 {task.check_result.criteria.map((c, j) => (
                   <Text key={j} color={c.passed ? "green" : "red"}>
                     {"  "}{c.passed ? "✓" : "✗"} {c.text}
                     {!c.passed && <Text color="yellow"> — {c.feedback}</Text>}
                   </Text>
                 ))}
               </Box>
             )}
           </Box>
         ))}
       </Box>
     )
   }
   ```

3. **将 PlanView 接入 app.tsx**：

   ```tsx
   // 当 agent_plan SSE 事件到达时，渲染 PlanView
   // 当 task_status SSE 事件到达时，原地更新计划
   // 当 check_result SSE 事件到达时，更新对应 task 的 check_result 字段
   ```

   计划状态应维护为 `Map<string, SubtaskStatus>`，这样 `task_status` 和 `check_result` 更新可以改变单个任务，而无需重新渲染整个树。

### 视觉示意

```
任务计划
  ○ task_001 分析项目结构 ✓
  ● task_002 生成 README.md
    ✓ README.md 文件已创建
    ✗ 项目架构描述与实际代码一致 — 缺少代码示例
  ○ task_003 运行测试 [check: FAIL]
    ✓ 测试能正常执行
    ✗ 测试覆盖率 ≥ 80% — 当前覆盖率仅 62%
```

### 验证

```bash
# 1. 模拟 SSE 流，发送包含 5 个任务的 agent_plan 事件
# 2. 验证树的缩进是否正确
# 3. 发送 check_result 事件——验证 Check 标注和失败标准的展开
# 4. 发送 task_status 更新——验证状态图标变化
```

### 提交信息

```
feat: add PlanView TUI component with Check result display per subtask
```

---

## 任务 6.9：TUI ThinkingBlock 组件——展示 Check 反馈

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ThinkingBlock.tsx`

**原因：**
代理推理文本（来自 SSE `agent_think` 事件）是内部独白，而非最终输出。灰色/斜体渲染使其与结果区分。同时展示 Check 反馈文本，让用户了解 Checker 评定了什么。

### 步骤

1. **创建 `ThinkingBlock.tsx`**——含 Check 反馈集成：

   ```tsx
   import { Text, Box } from "ink"

   interface CheckFeedback {
     check_feedback: string
     passed: boolean
   }

   interface Props {
     content: string
     taskId?: string
     checkFeedback?: CheckFeedback  // Checker 的反馈文本
   }

   export function ThinkingBlock({ content, taskId, checkFeedback }: Props) {
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
         {/* Check 反馈——在思考结束后，结果输出前展示 */}
         {checkFeedback && (
           <Box flexDirection="column" marginTop={1} paddingLeft={2}
                borderStyle="round" borderColor={checkFeedback.passed ? "green" : "yellow"}>
             <Text bold color={checkFeedback.passed ? "green" : "yellow"}>
               {checkFeedback.passed ? "✓ Check 通过" : "△ Check 反馈"}
             </Text>
             <Text dimColor>{checkFeedback.check_feedback}</Text>
           </Box>
         )}
       </Box>
     )
   }
   ```

2. **接入 StreamingChat 或 app.tsx**——接收 check 事件：

   ```tsx
   // 在 app.tsx 中：
   const [thinkingContent, setThinkingContent] = useState<string>("")
   const [checkFeedback, setCheckFeedback] = useState<CheckFeedback | null>(null)

   // 当 SSE agent_think 事件到达时：
   //   setThinkingContent(data.content)
   // 当 SSE check_result 事件到达时：
   //   setCheckFeedback({
   //     check_feedback: data.check_feedback,
   //     passed: data.passed,
   //   })
   // 当 SSE message_chunk 事件到达时：
   //   清除 thinkingContent 和 checkFeedback
   ```

### 视觉示意

```
  [think:task_001]
  用户想要我分析项目结构。
  我应该先读取 src 目录，然后检查关键文件。
  让我先列出文件...

  ┌─ △ Check 反馈 ─────────────────────────┐
  │  2 条标准未通过                         │
  │  项目架构描述与实际代码存在差异          │
  └─────────────────────────────────────────┘
```

### 验证

```bash
# 1. 发送 agent_think SSE 事件——验证灰色/斜体渲染
# 2. 发送 check_result SSE 事件——验证 Check 反馈框出现
# 3. 发送 message_chunk——验证 ThinkingBlock 和 Check 反馈被移除
```

### 提交信息

```
feat: add ThinkingBlock TUI component with Check feedback display
```

---

## 任务 6.10：TUI ToolCallCard 组件——按标准展示 PASS/FAIL

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ToolCallCard.tsx`

**原因：**
工具调用（操作→观察）是代理交互的核心。一个视觉上独特的卡片，带有彩色边框、工具名称、参数和结果，使其易于扫描。扩展后展示 Checker 的每条标准的 PASS/FAIL 状态。

### 步骤

1. **创建 `ToolCallCard.tsx`**——含 Check 标准展示：

   ```tsx
   import { Box, Text } from "ink"

   interface CriterionResult {
     text: string
     passed: boolean
     feedback?: string
   }

   interface Props {
     toolName: string
     args: Record<string, unknown>
     result?: string
     error?: string
     isRunning?: boolean
     checkCriteria?: CriterionResult[]  // Checker 的逐条标准结果
   }

   export function ToolCallCard({
     toolName, args, result, error, isRunning, checkCriteria
   }: Props) {
     const borderColor = error ? "red" : isRunning ? "yellow" : "green"

     return (
       <Box flexDirection="column" borderStyle="round" borderColor={borderColor}
            padding={1} marginY={1}>
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

         {/* 结果 */}
         {result !== undefined && (
           <Box flexDirection="column" paddingLeft={2} marginTop={1}>
             <Text dimColor>结果：</Text>
             <Text wrap="wrap">{result.slice(0, 500)}</Text>
             {result.length > 500 && <Text dimColor>... (截断，共 {result.length} 字符)</Text>}
           </Box>
         )}

         {/* Check 标准——每一条的 PASS/FAIL */}
         {checkCriteria && checkCriteria.length > 0 && (
           <Box flexDirection="column" paddingLeft={2} marginTop={1}
                borderStyle="single" borderColor="gray">
             <Text bold dimColor>标准检查：</Text>
             {checkCriteria.map((c, i) => (
               <Text key={i} color={c.passed ? "green" : "red"}>
                 {"  "}{c.passed ? "✓" : "✗"} {c.text}
                 {c.feedback && !c.passed && (
                   <Text color="yellow"> — {c.feedback}</Text>
                 )}
               </Text>
             ))}
           </Box>
         )}

         {/* 错误 */}
         {error && (
           <Box flexDirection="column" paddingLeft={2} marginTop={1}>
             <Text color="red">错误：{error}</Text>
           </Box>
         )}
       </Box>
     )
   }
   ```

2. **接入 StreamingChat**——维护 Check 标准状态：

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
     [data.id]: {
       ...prev[data.id],
       result: data.result,
       error: data.error,
       isRunning: false
     }
   }))

   // 当 check_result SSE 事件到达时（含逐条标准）：
   setToolCalls(prev => {
     const existing = prev[data.task_id]
     if (!existing) return prev
     return {
       ...prev,
       [data.task_id]: {
         ...existing,
         checkCriteria: data.criteria,  // 逐条标准的 PASS/FAIL
       }
     }
   })
   ```

### 视觉示意

```
┌─ ✓ read_file ─────────────────────────────────┐
│  参数：                                        │
│    path: src/main.py                            │
│                                                 │
│  结果：                                        │
│  import os                                      │
│  import sys                                     │
│  ...                                            │
│  ┌─ 标准检查 ──────────────────────────────┐   │
│  │  ✓ 文件内容非空                          │   │
│  │  ✗ 包含 main() 函数 — 未找到 main 定义   │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### 验证

```bash
# 1. 发送 agent_action SSE 事件——验证黄色边框
# 2. 发送 agent_observation——验证绿色边框 + 结果
# 3. 发送 check_result——验证标准检查区展开，显示每条 PASS/FAIL
# 4. 测试错误情况——红色边框
```

### 提交信息

```
feat: add ToolCallCard TUI component with per-criterion Check PASS/FAIL display
```

---

## 任务 6.11：TUI 计划任务管理界面

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
     description: string; prompt: string; schedule: string
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

   function AddForm({ description, prompt, schedule, onDescriptionChange,
                      onPromptChange, onScheduleChange, onSubmit }) {
     // ... 渲染三个 TextInput 字段和一个提交按钮
   }

   function ActionsBar({ mode, onAdd, onToggle, onDelete }) {
     // ... 在底部渲染键盘快捷键提示
   }
   ```

4. **将 ScheduledTaskManager 接入 app.tsx**：

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
| 6.1 | Checker 基础设施（TrajectoryLogger + RubricValidator + IterationBudget） | `checker.py`（新建） | — | 阶段 1-5 |
| 6.2 | Checker 路由 + Worker check_node 集成 | `checker.py`, `worker_pool.py` | — | 6.1 |
| 6.3 | 失败分类（规则优先，LLM 兜底） | `checker.py` | — | 6.2 |
| 6.4 | Checker 驱动重试 | `task_queue.py`, `redis_queue.py` | — | 6.3 |
| 6.5 | 级联回滚补偿 | `collector.py`, `task_queue.py` | — | 6.4 |
| 6.6 | CANCELLED 传播 | `task_queue.py`, `redis_queue.py` | — | 6.4 |
| 6.7 | Collector 中的错误聚合（含 Check 结果） | `collector.py` | — | 6.5, 6.6 |
| 6.8 | PlanView 组件（含 Check 状态） | — | `PlanView.tsx` | 阶段 2 SSE 协议 |
| 6.9 | ThinkingBlock 组件（含 Check 反馈） | — | `ThinkingBlock.tsx` | 阶段 1 SSE 协议 |
| 6.10 | ToolCallCard 组件（含逐标准 PASS/FAIL） | — | `ToolCallCard.tsx` | 阶段 1 SSE 协议 |
| 6.11 | 计划任务管理 | `server/app.py` | `ScheduledTaskManager.tsx`, `client.ts` | 阶段 4, 5 |

**新增后端文件总数：** ~1（`checker.py` 新建）
**新增后端文件总数（含工具）：** ~2（`checker.py`, `collector.py` 可能是新建或已有）
**新增前端文件总数：** ~4（`PlanView.tsx`, `ThinkingBlock.tsx`, `ToolCallCard.tsx`, `ScheduledTaskManager.tsx`）
**修改文件总数：** ~8（task_queue, redis_queue, checker(续写), collector, worker_pool, app.py, client.ts, types.ts）

## 执行顺序

任务 6.1-6.7 是后端弹性功能，必须严格按顺序执行（每个任务建立在前一个之上）：

```
6.1 基础设施 → 6.2 Checker → 6.3 失败分类 → 6.4 重试 → 6.5 补偿 → 6.6 取消 → 6.7 错误聚合
```

任务 6.8-6.11 是前端功能，可在 6.7 之后并行执行，但推荐顺序考虑到复杂度的递增：

```
6.8 PlanView → 6.9 ThinkingBlock → 6.10 ToolCallCard → 6.11 ScheduledTaskManager
```

所有 11 个任务在后端和前端层面之间独立，因此后端开发代理和前端开发代理可以在 6.1 完成后并行工作。
