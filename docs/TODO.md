# TODO
## 架构TODO
1. 按职责拆分LLM client，不再复用单一全局 get_tm（）。
2. 为不同阶段使用不同的 temperature 配置：
   - router / planner / checker ：低温
   executor: 低到中低温
   reflection/dreaming：中到高温 
3. 将 backend/src/nanoclaw/server/deps.py 从单—LLM provider 重构为多 provider 或 多 profile设计。
   - 明确 routing、planning、execution、evaluation、reflection 各阶段的LLM 职责边界。
   - 评估是否不仅要拆 temperature，还要按阶段拆不同 model。
4. Agent 链路缺少可观测性，需要接入专门的 tracing/ observability 平台，优先评估 LangSmith或 Langfuse，覆盖LLM调用、工具调用、子任务DAG、重试、checker、SSE事件等关键链路。

## 实现风险
- [x] redisQueue 问题已修复（Task 3：lease 缩短、iterative cascade cancel、幂等 init/restore）

- [x] 吞异常路径已补日志（Task 1：worker_pool、context/manager、memory/reflection、scheduler/engine、app.py）
- 修复前端 `StreamingChat`的 SSE 解析逻辑；当前按 chunk直接拆行，没有按 SSE 的“空行分隔完整事件”协议做 buffer和组装，遇到跨 chunk的‘event：’/data：’或半截 JS 时不稳
- [x] AbortController 已实现（Task 2）
- [x] done 事件处理已明确（Task 2）
- [x] thread_id 传递已补齐（Task 2）
- [x] useEffect 陈旧闭包已修复（Task 2：全部 callback 改为 ref 存储）
- [x] SSE 解析已统一到共享 `sse-parser.ts`（Task 2）
- [x] ContextManager 三级压缩已实现（Task 5：time-based MC、count-based MC、LLM auto-compact）
- [x] ContextStatsEvent 已接入（Task 5：通过 EventLogger 写入，fallback logger.info()）
## 优化项
- 后端 SSE `message_chunk` 改为逐 token 流式输出，而非一次性发送；当前 `run_graph()` 在 `supervisor.ainvoke()` 完成后才发送完整 content，需将 agent 的 `ainvoke` 改为 `astream` 并在 LLM streaming 回调中逐 token yield
## Phase 5/6 回检问题

### 已修复（记录在案，防止回退）
- [x] `pg_session_repo.py` — JSONB 列 `row.history` 已被 asyncpg 自动反序列化为 Python list，`json.loads()` 报 `TypeError`
- [x] `pg_session_repo.py` / `pg_task_repo.py` / `pg_checkpointer.py` — `::jsonb` 类型转换语法与 SQLAlchemy `:param` 命名参数冲突，替换为 `CAST(... AS JSONB)`
- [x] `storage/db.py` — migration SQL 未 commit 导致 DDL 可能不持久化，改为 `async with _conn.begin()`
- [x] `storage/redis_queue.py` — `asyncio.create_task()` 无引用，GC 可能在执行前回收；添加 `_pending_tasks` set 保存引用 + `done_callback` 清理
- [x] `storage/redis_queue.py` — `wait_for_all()` 无超时保护；添加 `timeout` 参数（默认 300s）+ `asyncio.timeout` 包裹
- [x] `scheduler/pg_repo.py` — `update()` 字段名 f-string 拼 SQL，未参数化；添加 `_ALLOWED_COLUMNS` 白名单校验
- [x] `server/app.py` — lifespan 启动时 `init_db()` / `get_redis()` 失败应 fail fast 而非静默降级

### 待修复（按优先级排序）

#### P0 — 启动与连接可靠性
- [x] PG 连接池已加入 warm-up（Task 6：init_db 末尾创建 pool_size 个连接）
- [x] Redis 已加入心跳 + 自动重连（30s ping，3 次失败后重建连接）
- [x] 多 worker 安全已文档说明（Task 6：fork-after-init，每个 worker 独立副本）

#### P1 — 数据一致性与错误处理
- [x] append_message 已改为原子 UPDATE（JSONB || 运算符）
- [x] ZSET 租约已缩短至 120s + 新增 renew_lease() 心跳方法（Task 3）
- [x] 所有 except Exception: pass 路径已补日志（Task 1 + Task 3）
- [x] scheduler dispatch 已加入 120s asyncio.timeout 保护

#### P2 — 可观测性与运维
- [ ] `backend/.nanoclaw/dreams/.last_dreaming` 已加入 gitignore，但 eval 日志 (`eval_dir`) 和 trajectory 文件仍在仓库目录下，缺少统一清理策略（TTL、磁盘配额）
- [ ] Docker Compose 的 PostgreSQL 容器升级 schema 需重建 volume；评估在 `init_db()` 中引入版本号迁移（[Alembic](https://alembic.sqlalchemy.org/) 或轻量自建）
- [ ] `scheduler/pg_repo.py` `_row_to_task()` 使用 `row.*` 属性访问，依赖 SQLAlchemy 的 `Row` 映射；缺字段或类型不匹配时异常被吞，无反馈
- [x] 健康检查已扩展为 per-service status（Task 4：postgres/redis/chroma 各返回 ok/unreachable/disabled）

#### P3 — 代码整洁与技术债
- [x] 已抽取为 `_deserialize_jsonb()` / `_deserialize_jsonb_list()`（Task 7）
- [ ] `redis_queue.py` `_check_all_done` 在 `complete()` / `fail()` / `compensate()` 三处调用，其中 `complete()` 和 `fail()` 的调用方是 `worker_pool.py`——考虑统一到单个事件出口
- [x] SQL split 已改为状态机解析，跳过 $$ 内的分号
