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
- redisQueue中有问题，重新review一下。 
- 排查并整改大量 `except Exception:pass` 或吞异常不记录日志的代码路径；至少应补充结构化日志或调试日志，否则线上排障和问题归因成本过高。
- 修复前端 `StreamingChat`的 SSE 解析逻辑；当前按 chunk直接拆行，没有按 SSE 的“空行分隔完整事件”协议做 buffer和组装，遇到跨 chunk的‘event：’/data：’或半截 JS 时不稳
- 为前端流式请求补充`AbortController`或等价机制，组件卸载或请求取消时应主动中止底层 `fetch`，而不只是设置 `canceLled = true`。
- 明确前端对`done`事件的处理语义，避免仅依赖连接关闭作为流结束信号。
- 为前端流式对话补齐 `thread_id` 、传递，保证多轮对话能复用同一 `session`。
- 评估并修复 `StreamingChat`、的 `useEffect`、依赖问题，避免 `baseUrL`、和各类回调形成陈旧闭包。
- 抽取并统一 CLI 侧SSE 解析逻辑，避免 `client.ts` 与 `StreamingChat.tsx`, 各自维护一套不一致的协议实现。
- 按照技术文档与方案，补齐 `ContextManager` 的 `context compact` / compression 实现；当前只有 prompt 拼装。
- 将 `eval/events.py` 中已定义但未接入的 `ContextStatsEvent` 真正打通，记录压缩次数、压缩前后 token 数和总上下文规模
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
- [ ] `storage/db.py` `init_db()` 中 `SELECT 1` 能验证连接，但 `create_async_engine()` 是懒连接——`pool_size` 未必在首次查询时全部建立；评估是否需要 warm-up pool
- [ ] `storage/redis_client.py` `get_redis()` 的 `Redis.from_url(decode_responses=True)` 创建的 client 是懒连接，`ping()` 能验证一次但非长活保活；评估心跳/重连策略
- [ ] 多 worker（uvicorn `--workers > 1`）场景下 `storage/db.py` / `redis_client.py` 的模块级全局变量存在竞态；评估 per-worker 初始化或进程级单例

#### P1 — 数据一致性与错误处理
- [ ] `pg_session_repo.py` `append_message()` 的读-改-写模式非原子：读 `history` → 追加 → 写回，并发写入时互相覆盖；评估 `jsonb_insert()` 或行级锁
- [ ] `storage/redis_queue.py` `ZSET` 租约机制在 worker 崩溃后等待 5 分钟才能被 `restore()` 回收；评估缩短默认租期或添加心跳续约
- [ ] 排查所有 `except Exception: pass` 路径（已列在实现风险中），至少补 warn 级别日志；重点路径：`worker_pool.py` 的 `_emit()`、`app.py` 的 `event_generator` try/except
- [ ] `scheduler/engine.py` 后台轮询 task 无超时保护，`get_due_tasks()` 或 `run_dreaming()` 卡住时整个 scheduler loop 阻塞

#### P2 — 可观测性与运维
- [ ] `backend/.nanoclaw/dreams/.last_dreaming` 已加入 gitignore，但 eval 日志 (`eval_dir`) 和 trajectory 文件仍在仓库目录下，缺少统一清理策略（TTL、磁盘配额）
- [ ] Docker Compose 的 PostgreSQL 容器升级 schema 需重建 volume；评估在 `init_db()` 中引入版本号迁移（[Alembic](https://alembic.sqlalchemy.org/) 或轻量自建）
- [ ] `scheduler/pg_repo.py` `_row_to_task()` 使用 `row.*` 属性访问，依赖 SQLAlchemy 的 `Row` 映射；缺字段或类型不匹配时异常被吞，无反馈
- [ ] 无统一健康检查端点验证 DB / Redis / Chroma 全部可达（当前 `/health` 只返回 `ok`）

#### P3 — 代码整洁与技术债
- [ ] `pg_session_repo.py` 中 `isinstance(row.history, str)` / `isinstance(row.history, list)` 检查遍布三处方法；抽取为 `_deserialize_jsonb()` 工具函数
- [ ] `redis_queue.py` `_check_all_done` 在 `complete()` / `fail()` / `compensate()` 三处调用，其中 `complete()` 和 `fail()` 的调用方是 `worker_pool.py`——考虑统一到单个事件出口
- [ ] `storage/db.py` migration 的 SQL split 逻辑过于简单（分号分割），无法处理函数定义或 PL/pgSQL 中含分号的语句；评估改用逐语句执行或专用 migration 工具
