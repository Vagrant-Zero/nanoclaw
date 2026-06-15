# 已发现问题修复计划

**日期：** 2026-06-15
**背景：** 从 `docs/TODO.md` 和日常开发中发现的问题汇总，不绑定任何特定 Phase。
**原则：** 不修改已有 plan 文件（Phase 1-6），新增此独立文件追踪。

---

## 任务总览

| # | 优先级 | 问题 | 状态 |
|---|--------|------|------|
| 1 | P1 | 吞异常路径补日志 | ✅ 已完成 |
| 2 | P1 | 前端 StreamingChat SSE 解析鲁棒性 | ✅ 已完成 |
| 3 | P1 | RedisQueue 全面审查 | ✅ 已完成 |
| 4 | P2 | 健康检查端点增强 | ✅ 已完成 |
| 5 | P1 | ContextManager 完整压缩实现（重构） | ✅ 已完成 |
| 6 | P2 | PG 连接池 warm-up + 多 worker 安全 | ✅ 已完成 |
| 7 | P3 | 抽取 `_deserialize_jsonb()` 工具函数 | ✅ 已完成 |

---

## 任务 1：吞异常路径补日志 ✅

**来源：** TODO 实现风险第2条
**状态：** 已完成

**改动要点：** `worker_pool.py`、`app.py`、`engine.py`、`manager.py`、`reflection.py` 中 8 处 `except Exception: pass` 替换为 `logger.warning(exc_info=True)`，并在模块头添加 `import logging` + `logger = logging.getLogger(__name__)`。

---

## 任务 2：前端 StreamingChat SSE 解析鲁棒性

**来源：** TODO 实现风险第3、4、5、6、7、8条
**涉及文件：** `cli/src/components/StreamingChat.tsx`、`cli/src/client.ts`

1. **SSE buffer**: 按 chunk 拆线改为按空行分隔完整事件，消除跨 chunk 丢事件
2. **AbortController**: 组件卸载时主动 `abort()` fetch，不只设 `cancelled = true`
3. **`done` 事件**: 明确处理语义，不依赖连接关闭作结束信号
4. **`thread_id`**: 补齐传递，支持多轮对话复用 session
5. **`useEffect` 依赖**: 消除 `onThink` / `onAction` 等回调陈旧闭包
6. **统一 SSE 解析**: 消除 `client.ts` 与 `StreamingChat.tsx` 的重复实现

---

## 任务 3：RedisQueue 全面审查

**来源：** TODO 实现风险第1条
**涉及文件：** `backend/src/nanoclaw/storage/redis_queue.py`

1. ZSET 租约 5 分钟回收：评估缩短或加心跳续约
2. `_cascade_cancel` 递归深度上限
3. `wait_for_all` pubsub 死锁风险评估
4. `init_plan` 幂等性
5. `restore` 多次调用安全

---

## 任务 4：健康检查端点增强

**来源：** TODO Phase 5/6 P2 第4条
**涉及文件：** `backend/src/nanoclaw/server/app.py`

`/health` 当前只返回 `{"status": "ok", "version": "..."}`，扩展为：
```json
{
  "status": "ok",
  "version": "0.1.0",
  "checks": {
    "postgres": "ok",
    "redis": "ok",
    "chroma": "ok"
  }
}
```
各 `checks.*` 字段为 `"ok"` / `"unreachable"` / `"disabled"`。

---

## 任务 5：ContextManager 完整压缩实现（重构）

**来源：** TODO 实现风险第9条 + TODO 实现风险第10条
**技术方案引用：** `docs/plans/2026-06-08-agent-architecture-design.md` 第 807-905 行 —— 三层压缩策略

**当前问题：** `context/manager.py` 只做了 prompt 拼接（system prompt / user profile / skill injection / thread context / task state），完全没有实现架构文档中的压缩机制。`auto_compact()`、`micro_compress_tool_result()`、time-based/count-based/token-based 三种触发策略全部缺失。

**设计引用（架构文档）：** 数据源保留保证可恢复。三种压缩独立触发，不按固定阶梯递进。执行类 Agent 的 tool_result 量通常较大，需纳入微压缩范围。

### 子任务 5.1：压缩配置常量

**新建文件：** `backend/src/nanoclaw/context/compression_config.py`

```python
@dataclass(frozen=True)
class CompressionConfig:
    time_mc_max_age_minutes: int = 5   # time-based MC：距上次 assistant 回复 > N 分钟，清空旧 tool_result
    count_mc_max_results: int = 8      # count-based MC：可压缩结果 > 阈值，删除最旧的
    token_threshold: int = 8000        # token-based：总 tokens > 阈值，触发 LLM 摘要
    llm_for_summary: str = "default"   # 摘要用 LLM（可单独配置温度更低）
    keep_last_n_turns: int = 3         # auto_compact 保留最新 N 轮对话
```

### 子任务 5.2：代码级微压缩（不调 LLM）

**新建文件：** `backend/src/nanoclaw/context/micro_compact.py`

```python
class MicroCompact:
    def compress_tool_result(self, result: str, tool_name: str) -> str:
        """按工具名分类压缩。文件读取→保留头尾行；命令→保留 exit code + stdout/stderr 摘要；web→保留标题+前100字"""

    def time_based_compact(self, messages: list, max_age_minutes: int) -> list:
        """距上次 assistant 回复 > N 分钟 → 清空旧的 tool_result 内容"""

    def count_based_compact(self, messages: list, max_results: int) -> list:
        """可压缩 tool_result 超过阈值 → 删除最旧的"""
```

### 子任务 5.3：LLM 级别摘要

**新建文件：** `backend/src/nanoclaw/context/auto_compact.py`

```python
class AutoCompact:
    async def compact(self, messages: list, keep_last_n: int, threshold: int, llm) -> list:
        """低温 LLM 摘要旧消息 → SystemMessage("[Conversation Summary: ...]")。原消息保留到 transcript_path JSON 文件。返回压缩后的 messages 列表。"""
```

### 子任务 5.4：改造 `build_prompt()`

**修改文件：** `backend/src/nanoclaw/context/manager.py`

集成三级压缩为 `build_prompt` 的预处理阶段：

```
build_prompt()
├─ 1. time-based MC  → 清空过期 tool_result
├─ 2. count-based MC → 溢出时删除最旧结果  
├─ 3. token 检查     → 超阈值触发 auto_compact LLM 摘要
└─ 4. prompt 组装    → System + Profile + Skills + Thread + Task state（现有逻辑不变）
```

### 子任务 5.5：打通 `ContextStatsEvent`

**修改文件：** `backend/src/nanoclaw/eval/events.py`（已有定义未接入）、`context/manager.py`

`auto_compact()` 完成后记录：压缩次数、压缩前后 token 数、触发阈值。通过 EventLogger 写入（如果有），否则 fallback `logger.info()`。

**执行顺序：** 5.1 → 5.2 / 5.3（并行）→ 5.4 → 5.5

---

## 任务 6：PG 连接池 warm-up + 多 worker 安全

**来源：** TODO Phase 5/6 P0 第1、3条
**涉及文件：** `backend/src/nanoclaw/storage/db.py`、`storage/redis_client.py`

1. `init_db()` 在 `SELECT 1` 后用 `_engine.pool.connect()` 预热连接池
2. 文档说明多 worker 限制或实现 per-worker 初始化
3. 启动钩子校验连接可用性

---

## 任务 7：抽取 `_deserialize_jsonb()` 工具函数 ✅

**来源：** TODO Phase 5/6 P3 第1条
**状态：** 已完成

新增 `backend/src/nanoclaw/storage/_jsonb.py`，`pg_session_repo.py`、`pg_task_repo.py`、`pg_checkpointer.py` 中所有 `isinstance(row.xxx, ...)` 检查替换为 `deserialize_jsonb()` / `deserialize_jsonb_list()`。


## 任务 8：代码类型标注清理 — 减少 `Any` 使用

**来源：** 代码审查发现 `func` 实现中用 `Any` 过多，要求除非迫不得已尽量不用 `Any`

**原则：** 每个 `Any` 必须能被具体类型替换，否则保留并注明原因

### 当前 Any 分布（37 处）

| 分类 | 文件 | 数量 | 替换类型 |
|------|------|------|----------|
| LLM 参数 | `checker.py`, `worker_pool.py`, `react_agent.py`, `planner.py`, `router.py`, `dreaming/engine.py`, `dreaming/tools.py`, `memory/reflection.py`, `supervisor_graph.py` | 11 | `BaseChatModel` (from `langchain_core.language_models.chat_models`) |
| 编译图返回 | `supervisor_graph.py`, `react_agent.py`, `deps.py` | 3 | `CompiledStateGraph` (from `langgraph.graph.state`) |
| Worker 参数 | `worker_pool.py` (`react_agent`, `check_result`) | 2 | `CompiledStateGraph`, `CheckResult` |
| Redis 参数 | `redis_queue.py` (`_check_all_done`) | 1 | `Redis` (from `redis.asyncio`) |
| SQLAlchemy Row | `scheduler/pg_repo.py` (`_row_to_task`) | 1 | `Row` (from `sqlalchemy.engine`) |
| 上下文/日志 | `react_agent.py` (`context_manager`, `event_logger`) | 2 | `ContextManager`, `EventLogger` |
| 评测工具 | `checker.py` (`_check_file_ops` 等 3 个 helper) | 3 | `task: Any` → `Subtask` |
| DAG task | `checker.py`, `dreaming/tools.py` (`task: Any`) | 2 | `Subtask` |
| 动态 dict | `scheduler/repo.py`, `scheduler/pg_repo.py`, `tools/registry.py`, `dreaming/engine.py`, `eval/logger.py` | 7 | **保留 `dict[str, Any]`** — 动态键值 |
| 工具函数 | `storage/_jsonb.py` (`deserialize_jsonb`) | 2 | **保留 `Any`** — 处理未知 JSONB 输入 |
| 调度器 | `scheduler/repo.py` (`get_scheduled_task_repo`) | 1 | `ScheduledTaskRepo` |
| 签名损坏 | `redis_queue.py` (`_check_all_done` 行损坏) | 1 | 修复语法 |

### 步骤

**阶段 1（P2 — 简单替换）：** 每个 `Any` 有明确的替代类型，直接改 import + signature。

- [ ] `checker.py`: `llm: Any` → `BaseChatModel`, 3 个 helper `task: Any` → `Subtask`
- [ ] `redis_queue.py`: `redis: Any` → `Redis`, 修复损坏的 `_check_all_done` 签名
- [ ] `worker_pool.py`: `react_agent: Any` → `CompiledStateGraph`, `llm: Any` → `BaseChatModel`, `check_result: Any` → `CheckResult`
- [ ] `scheduler/pg_repo.py`: `row: Any` → `Row`
- [ ] `scheduler/repo.py`: `dict[str, Any]` → `dict[str, object]`
- [ ] `deps.py`: `get_llm`, `get_supervisor`, `get_scheduled_task_repo` 返回类型从 `Any` → 具体类型

**阶段 2（P2 — import 修正）：** 确保所有类型导入路径正确。

- [ ] `CompiledStateGraph` import 路径为 `langgraph.graph.state`（非 `langgraph.graph`）
- [ ] `BaseChatModel` import 路径为 `langchain_core.language_models.chat_models`
- [ ] `Row` import 路径为 `sqlalchemy.engine`
- [ ] 避免循环 import（在 `from __future__ import annotations` 下用 `TYPE_CHECKING` 隔离）

**阶段 3（P3 — 动态类型保留审查）：** 遍历所有保留的 `Any`，标注保留原因。

---

## 后续优化

| # | 优先级 | 问题 | 状态 |
|---|--------|------|------|
| 9 | P2 | message_chunk 逐 token 流式输出：当前 run_graph() 在 supervisor.ainvoke() 完成后才发送完整 content，需将 agent 的 ainvoke 改为 astream 并在 LLM streaming 回调中逐 token yield | ⬜ 待执行 |

