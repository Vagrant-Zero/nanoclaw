# Phase 5：Docker + 真实存储

**日期：** 2026-06-08
**状态：** 草案
**依赖：** Phase 1-4（基础、多任务、记忆/评估、梦境/调度器）

## 概述

将所有内存模拟存储实现替换为真正的 PostgreSQL 和 Redis 后端。Docker Compose 提供基础设施。所有存储抽象（SessionRepository、TaskRepository、TaskQueue、Checkpointer、ScheduledTaskRepo）都将获得生产级实现。模拟/生产之间的切换通过环境变量配置驱动。

## 设计决策

- **为什么用 Docker Compose 而非本地安装 PG/Redis**：零设备污染、精确版本锁定、macOS/Linux 一致性、通过 `docker compose down -v` 轻松清理。
- **为什么用 SQLAlchemy 异步**：LangGraph 节点和 FastAPI 处理器均为异步。使用 `sqlalchemy[asyncio]` 下的 `asyncpg` 保持事件循环不被阻塞。
- **为什么用 Redis LPUSH/BRPOP 做队列**：简单、可靠、单路径队列。带超时的 BRPOP 避免了忙轮询。配合 Redis pub/sub 实现 `wait_for_all()`。
- **为什么用 ZSET 做租约**：以过期时间戳为分数的有序集合（Sorted Set），可以在 O(log N + M) 内扫描过期的租约。无需额外的 TTL 键管理。
- **为什么用配置驱动切换**：启动时检查环境变量。如果设置了 `NANOCLAW_DB_URL`，使用 PG 仓库；否则回退到内存仓库。Redis 同理。这样既保持了开发便利性（快速测试无需 Docker），又支持生产环境。
- **为什么用定期 VACUUM**：PG 的 `autovacuum` 能处理大多数情况，但 sessions/tasks 表是 append-heavy 型并包含 JSONB。一个轻量级的维护命令有助于保持查询性能稳定。

## 任务 5.1：Docker Compose — PostgreSQL + Redis + Chroma

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/docker-compose.yml`
- `/Users/vagrant/dev/code/python/nanoclaw/.env.example`

**原因：**
提供一键式基础设施启动，用于所有持久化服务。包含 Chroma 是因为 Phase 3 需要它来存储 MemoryStore 的嵌入向量；现在包含它可以避免后续再次修改 Docker Compose。

### 步骤

1. 在项目根目录创建 `docker-compose.yml`：

   ```yaml
   services:
     postgres:
       image: postgres:16-alpine
       ports: ["5432:5432"]
       environment:
         POSTGRES_USER: nanoclaw
         POSTGRES_PASSWORD: nanoclaw_dev
         POSTGRES_DB: nanoclaw
       volumes:
         - pgdata:/var/lib/postgresql/data
         - ./backend/migrations/init.sql:/docker-entrypoint-initdb.d/init.sql
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U nanoclaw"]
         interval: 5s
         timeout: 3s
         retries: 5

     redis:
       image: redis:7-alpine
       ports: ["6379:6379"]
       volumes:
         - redisdata:/data
       healthcheck:
         test: ["CMD", "redis-cli", "ping"]
         interval: 5s
         timeout: 3s
         retries: 5

     chromadb:
       image: chromadb/chroma:0.6.3
       ports: ["8001:8000"]
       environment:
         IS_PERSISTENT: "TRUE"
         PERSIST_DIRECTORY: /chroma/data
       volumes:
         - chromadata:/chroma/data

   volumes:
     pgdata:
     redisdata:
     chromadata:
   ```

   关键选择：
   - 对 postgres 使用 `16-alpine`：镜像小，适合开发。
   - 对 redis 使用 `7-alpine`：占用空间最小。
   - Healthcheck 确保 Makefile 目标可以 `docker compose up --wait`。
   - 使用命名卷而非绑定挂载，以实现跨平台可靠性。
   - Chroma 使用端口 8001 以避免与后端（8420）冲突。

2. 创建 `backend/migrations/init.sql`：

   ```sql
   CREATE TABLE IF NOT EXISTS sessions (
       id TEXT PRIMARY KEY,
       created_at DOUBLE PRECISION NOT NULL,
       history JSONB NOT NULL DEFAULT '[]'::jsonb,
       active_plan_id TEXT,
       serialized_state JSONB
   );

   CREATE TABLE IF NOT EXISTS task_plans (
       session_id TEXT NOT NULL,
       plan_id TEXT NOT NULL,
       data JSONB NOT NULL,
       created_at DOUBLE PRECISION NOT NULL,
       PRIMARY KEY (session_id, plan_id)
   );

   CREATE TABLE IF NOT EXISTS scheduled_tasks (
       id TEXT PRIMARY KEY,
       user_id TEXT NOT NULL DEFAULT 'default',
       description TEXT NOT NULL,
       prompt TEXT NOT NULL,
       schedule TEXT NOT NULL,
       enabled BOOLEAN NOT NULL DEFAULT TRUE,
       created_at DOUBLE PRECISION NOT NULL,
       last_run TIMESTAMPTZ,
       agent_id TEXT,
       session_id TEXT
   );

   CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);
   CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_last_run ON scheduled_tasks(last_run);
   ```

   为什么采用此模式：
   - `sessions.history` 是 ChatMessage 对象的 JSONB 数组。追加操作使用 `jsonb_insert` 或读取-替换。
   - `sessions.serialized_state` 是 Checkpointer 负载（JSONB）。在存在检查点之前为 Null。
   - `task_plans` 使用复合主键 `(session_id, plan_id)`，因为一个会话在其生命周期内可能有多个计划。
   - `scheduled_tasks` 是一个扁平表——对调度器的定期查询 `WHERE enabled AND last_run < now()` 来说足够简单。

3. 在项目根目录创建 `.env.example`：

   ```
   NANOCLAW_DB_URL=postgresql+asyncpg://nanoclaw:nanoclaw_dev@localhost:5432/nanoclaw
   NANOCLAW_REDIS_URL=redis://localhost:6379/0
   NANOCLAW_CHROMA_URL=http://localhost:8001
   NANOCLAW_LLM_PROVIDER=openai
   NANOCLAW_LLM_MODEL=gpt-4o-mini
   NANOCLAW_OPENAI_API_KEY=
   NANOCLAW_ANTHROPIC_API_KEY=
   NANOCLAW_HOST=127.0.0.1
   NANOCLAW_PORT=8420
   ```

4. 在 Makefile 中添加 `make docker` 目标：

   ```makefile
   docker:
       docker compose up --wait --remove-orphans
   ```

   以及 `make docker-down`：

   ```makefile
   docker-down:
       docker compose down
   ```

5. 添加到 `.gitignore`：`backend/migrations/` 会被跟踪（它是初始化脚本），但 `.env` 文件不会被跟踪。

### 验证

```bash
make docker
docker compose ps
# postgres, redis, chromadb should all be "healthy"
docker compose exec postgres pg_isready -U nanoclaw
docker compose exec redis redis-cli ping
```

### 提交信息

```
feat: add Docker Compose with PostgreSQL 16, Redis 7, Chroma 0.6.3
```

---

## 任务 5.2：PgSessionRepo — PostgreSQL 支持的 SessionRepository

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/db.py` — 共享的 SQLAlchemy 引擎/会话工厂
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/pg_session_repo.py`

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/pyproject.toml` — 添加 `sqlalchemy[asyncio]` 和 `asyncpg`

**原因：**
会话数据必须在进程重启后仍然存活。PG 提供 ACID 保证。通过 `sqlalchemy[asyncio]` 的异步引擎可以与 FastAPI 的异步请求处理器干净地集成。

### 步骤

1. 添加依赖到 `pyproject.toml`：

   ```
   "sqlalchemy[asyncio]>=2.0",
   "asyncpg>=0.30",
   ```

2. 创建 `storage/db.py`：

   ```python
   """Shared async SQLAlchemy engine and session factory."""

   from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

   from nanoclaw.config import settings

   _engine = None
   _sessionmaker = None

   def get_db_url() -> str:
       return settings.db_url or "postgresql+asyncpg://nanoclaw:nanoclaw_dev@localhost:5432/nanoclaw"

   async def init_db() -> None:
       """Initialize the async engine and sessionmaker. Called at startup."""
       global _engine, _sessionmaker
       url = get_db_url()
       _engine = create_async_engine(url, pool_size=5, max_overflow=10)
       _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

   async def close_db() -> None:
       """Dispose the engine. Called at shutdown."""
       global _engine, _sessionmaker
       if _engine:
           await _engine.dispose()
       _engine = None
       _sessionmaker = None

   def get_session() -> AsyncSession:
       if _sessionmaker is None:
           raise RuntimeError("Database not initialized. Call init_db() first.")
       return _sessionmaker()
   ```

   关键选择：
   - 引擎/会话工厂的模块级全局变量。这是异步 FastAPI 应用的标准做法。现阶段不需要完整的依赖注入容器。
   - `pool_size=5` / `max_overflow=10` 对于拥有 3 个工作进程的个人助手来说合理。
   - `expire_on_commit=False` 避免将类 ORM 对象传出会话作用域时的延迟加载问题（尽管我们使用原始 SQL/JSONB，而非 ORM 模型）。

3. 创建 `storage/pg_session_repo.py`：

   ```python
   """PostgreSQL implementation of SessionRepository."""

   import json
   from nanoclaw.models.chat import ChatMessage
   from nanoclaw.storage.db import get_session
   from nanoclaw.storage.session_repo import SessionRepository, Session

   class PgSessionRepo(SessionRepository):
       async def create(self, session: Session) -> Session:
           async with get_session() as s:
               await s.execute(
                   text("""
                       INSERT INTO sessions (id, created_at, history, active_plan_id)
                       VALUES (:id, :created_at, :history::jsonb, :active_plan_id)
                   """),
                   {
                       "id": session.id,
                       "created_at": session.created_at,
                       "history": json.dumps([m.to_dict() for m in session.messages]),
                       "active_plan_id": session.active_plan.id if session.active_plan else None,
                   },
               )
               await s.commit()
           return session

       async def get(self, session_id: str) -> Session | None:
           async with get_session() as s:
               row = (await s.execute(
                   text("SELECT id, created_at, history, active_plan_id FROM sessions WHERE id = :id"),
                   {"id": session_id},
               )).fetchone()
           if row is None:
               return None
           messages = [ChatMessage.from_dict(m) for m in json.loads(row.history)]
           return Session(id=row.id, created_at=row.created_at, messages=messages, ...)

       # append_message: read full history, append, write back (JSONB append is possible
       # but read-modify-write is simpler and safe for single-writer pattern)
       # get_history: read history column, deserialize
   ```

   领域模型 `Session.to_dict()` / `Session.from_dict()` 应添加到 `models/chat.py`（如果尚未存在）。仓库读取/写入 JSON 序列化结构而非使用 SQLAlchemy ORM——这保持了模式的简洁性（JSONB 列）并避免了 ORM 映射开销。

4. 更新 `config.py` 添加 `db_url` 字段：

   ```python
   db_url: str | None = None
   ```

   通过现有的 `env_prefix` 从 `NANOCLAW_DB_URL` 读取。

5. 接入 `server/app.py` 的启动/关闭：

   ```python
   from nanoclaw.storage.db import init_db, close_db

   @app.on_event("startup")
   async def startup():
       if settings.db_url:
           await init_db()

   @app.on_event("shutdown")
   async def shutdown():
       if settings.db_url:
           await close_db()
   ```

6. 如果 `Session.to_dict()` / `from_dict()` 尚未存在，更新 `storage/session_repo.py`（抽象基类）。这些对于 JSONB 序列化是必需的。

### 验证

```python
# pytest or manual:
# 1. Start Docker services
# 2. Set NANOCLAW_DB_URL
# 3. Start backend
# 4. POST /chat with a message
# 5. Verify row appears in sessions table:
#    docker compose exec postgres psql -U nanoclaw -c "SELECT id, created_at FROM sessions;"
```

### 提交信息

```
feat: add PgSessionRepo with SQLAlchemy async engine and JSONB sessions table
```

---

## 任务 5.3：PgTaskRepo — PostgreSQL 支持的 TaskRepository

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/pg_task_repo.py`

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/task_repo.py` — 如果抽象基类需要更新

**原因：**
任务计划和子任务状态必须在重启后仍然存活。`task_plans` 表将完整计划存储为 JSONB。该仓库是写密集型的（每次工作进程更新都会切换子任务状态），因此我们针对单行更新进行了优化。

### 步骤

1. 创建 `storage/pg_task_repo.py`：

   ```python
   """PostgreSQL implementation of TaskRepository."""

   class PgTaskRepo(TaskRepository):
       async def save_plan(self, session_id: str, plan: TaskPlan) -> None:
           async with get_session() as s:
               await s.execute(
                   text("""
                       INSERT INTO task_plans (session_id, plan_id, data, created_at)
                       VALUES (:session_id, :plan_id, :data::jsonb, :created_at)
                       ON CONFLICT (session_id, plan_id) DO UPDATE SET data = :data::jsonb
                   """),
                   {
                       "session_id": session_id,
                       "plan_id": plan.id,
                       "data": json.dumps(asdict(plan)),
                       "created_at": time.time(),
                   },
               )
               await s.commit()

       async def get_plan(self, session_id: str) -> TaskPlan | None:
           async with get_session() as s:
               row = (await s.execute(
                   text("""
                       SELECT data FROM task_plans
                       WHERE session_id = :session_id
                       ORDER BY created_at DESC LIMIT 1
                   """),
                   {"session_id": session_id},
               )).fetchone()
           if row is None:
               return None
           return TaskPlan.from_dict(json.loads(row.data))

       async def update_subtask(self, session_id: str, subtask: Subtask) -> None:
           # Read current plan, find and replace the subtask, write back
           plan = await self.get_plan(session_id)
           if plan is None:
               raise ValueError(f"No plan found for session {session_id}")
           for i, st in enumerate(plan.subtasks):
               if st.id == subtask.id:
                   plan.subtasks[i] = subtask
                   break
           await self.save_plan(session_id, plan)
   ```

   为什么 `update_subtask` 采用读-修改-写模式：
   - task_plans 表将完整计划存储为一行 JSONB。
   - 更新单个子任务需要 读取 → 修改 → 写入。
   - 对于单用户个人 AI 助手，写入争用可以忽略不计。
   - 如果后续争用成为问题，可以在特定路径上切换为 `jsonb_set()`，但这会增加复杂性。

2. 如果尚未存在，将 `TaskPlan.from_dict()` 和 `Subtask.from_dict()` 添加到 `models/task.py`。这些对于从 JSONB 反序列化是必需的。

3. 确保 `storage/task_repo.py` 中的抽象 `TaskRepository` 接口与这些方法签名一致。

### 验证

```python
# After a task runs:
# docker compose exec postgres psql -U nanoclaw -c "SELECT session_id, plan_id FROM task_plans;"
# The row should exist with the full plan JSON.
```

### 提交信息

```
feat: add PgTaskRepo with JSONB task_plans table (read-modify-write pattern)
```

---

## 任务 5.4：RedisQueue — Redis 支持的任务队列与租约机制

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/redis_queue.py`

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/pyproject.toml` — 添加 `redis[hiredis]`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/config.py` — 添加 `redis_url`

**原因：**
MemoryQueue 受限于进程，重启后会丢失状态。RedisQueue 使用 Redis 列表作为任务队列、pub/sub 作为等待组、有序集合作为工作进程租约。这支持多进程工作进程和崩溃恢复。

### 步骤

1. 将 `redis[hiredis]` 添加到 `pyproject.toml` 依赖。

2. 将 `redis_url: str | None = None` 添加到 `config.py`（从 `NANOCLAW_REDIS_URL` 读取）。

3. 创建共享 Redis 连接的 `storage/redis_client.py`：

   ```python
   """Shared async Redis connection."""

   from redis.asyncio import Redis
   from nanoclaw.config import settings

   _redis: Redis | None = None

   async def get_redis() -> Redis:
       global _redis
       if _redis is None:
           url = settings.redis_url or "redis://localhost:6379/0"
           _redis = Redis.from_url(url, decode_responses=True)
       return _redis

   async def close_redis() -> None:
       global _redis
       if _redis:
           await _redis.aclose()
           _redis = None
   ```

   为什么 `decode_responses=True`：
   - 所有负载都是 JSON 字符串。没有必要到处解码字节。
   - 仅在连接上设置；个别调用者如果需要仍然可以获取字节。

4. 创建 `storage/redis_queue.py`：

   ```python
   """Redis-backed TaskQueue with lease-based worker crash detection."""

   class RedisQueue(TaskQueue):
       KEY_PREFIX = "nanoclaw:queue:"

       def __init__(self, session_id: str) -> None:
           self.session_id = session_id
           self._redis: Redis | None = None
           # In-memory mirrors for DAG navigation (Redis keeps the actual queue)
           self._dag: dict[str, list[str]] = {}
           self._rdag: dict[str, list[str]] = {}
           self._tasks: dict[str, Subtask] = {}
           self._completed_count = 0
           self._total_count = 0
           self._pubsub_channels: list[str] = []

       @property
       def _q(self) -> str:
           return f"{self.KEY_PREFIX}{self.session_id}:ready"

       @property
       def _pubsub(self) -> str:
           return f"{self.KEY_PREFIX}{self.session_id}:done"

       @property
       def _leases(self) -> str:
           return f"{self.KEY_PREFIX}{self.session_id}:leases"

       @property
       def _task_key(self, task_id: str) -> str:
           return f"{self.KEY_PREFIX}{self.session_id}:tasks:{task_id}"

       async def init_plan(self, plan: TaskPlan) -> None:
           self._redis = await get_redis()
           self._tasks = {s.id: s for s in plan.subtasks}
           self._total_count = len(plan.subtasks)
           # Build DAG mirrors in-memory
           for s in plan.subtasks:
               self._dag[s.id] = s.depends_on
               for dep in s.depends_on:
                   self._rdag.setdefault(dep, []).append(s.id)
           # Persist subtask data to Redis hashes
           for s in plan.subtasks:
               await self._redis.hset(self._task_key(s.id), mapping={
                   "status": s.status.value,
                   "result": s.result or "",
                   "error": s.error or "",
                   "retry_count": str(s.retry_count),
               })
           # Enqueue leaf tasks
           for s in plan.subtasks:
               if not s.depends_on:
                   await self._redis.lpush(self._q, s.id)

       async def dequeue(self) -> Subtask | None:
           redis = await get_redis()
           # BRPOP with 5s timeout — non-blocking poll
           result = await redis.brpop(self._q, timeout=5)
           if result is None:
               return None
           _, task_id = result
           subtask = self._tasks.get(task_id)
           if subtask is None:
               return None
           # Claim lease: ZADD with expiration timestamp
           expire_ts = time.time() + 300  # 5-minute lease
           await redis.zadd(self._leases, {task_id: expire_ts})
           subtask.status = TaskStatus.RUNNING
           await redis.hset(self._task_key(task_id), "status", TaskStatus.RUNNING.value)
           return subtask

       async def complete(self, task_id: str, result: str) -> None:
           redis = await get_redis()
           self._tasks[task_id].status = TaskStatus.SUCCEEDED
           self._tasks[task_id].result = result
           self._completed_count += 1
           # Remove lease
           await redis.zrem(self._leases, task_id)
           # Persist
           await redis.hset(self._task_key(task_id), mapping={
               "status": TaskStatus.SUCCEEDED.value,
               "result": result,
           })
           # Enqueue downstream tasks whose deps are all satisfied
           for downstream in self._rdag.get(task_id, []):
               deps = self._dag[downstream]
               # Check all deps — read from Redis hash for crash-consistent state
               dep_statuses = []
               for dep in deps:
                   s = await redis.hget(self._task_key(dep), "status")
                   dep_statuses.append(s)
               if all(s == TaskStatus.SUCCEEDED.value for s in dep_statuses):
                   await redis.lpush(self._q, downstream)
           # If all done, publish to the done channel
           if self._completed_count >= self._total_count:
               await redis.publish(self._pubsub, "ALL_DONE")

       async def fail(self, task_id: str, error: str) -> None:
           redis = await get_redis()
           self._tasks[task_id].status = TaskStatus.FAILED
           self._tasks[task_id].error = error
           self._completed_count += 1
           await redis.zrem(self._leases, task_id)
           await redis.hset(self._task_key(task_id), mapping={
               "status": TaskStatus.FAILED.value,
               "error": error,
           })
           # Mark downstream as CANCELLED
           for downstream in self._rdag.get(task_id, []):
               self._tasks[downstream].status = TaskStatus.CANCELLED
               self._completed_count += 1
               await redis.hset(self._task_key(downstream), "status", TaskStatus.CANCELLED.value)
               # Also mark their downstream transitively
               await self._cascade_cancel(downstream)
           if self._completed_count >= self._total_count:
               await redis.publish(self._pubsub, "ALL_DONE")

       async def _cascade_cancel(self, task_id: str) -> None:
           """Recursively mark all transitive downstream tasks as CANCELLED."""
           redis = await get_redis()
           for downstream in self._rdag.get(task_id, []):
               if self._tasks[downstream].status == TaskStatus.PENDING:
                   self._tasks[downstream].status = TaskStatus.CANCELLED
                   self._completed_count += 1
                   await redis.hset(self._task_key(downstream), "status", TaskStatus.CANCELLED.value)
                   await self._cascade_cancel(downstream)

       async def wait_for_all(self) -> dict:
           redis = await get_redis()
           pubsub = redis.pubsub()
           await pubsub.subscribe(self._pubsub)
           # Already done?
           if self._completed_count >= self._total_count:
               await pubsub.unsubscribe(self._pubsub)
               return self._collect_results()
           # Wait for the ALL_DONE message
           async for message in pubsub.listen():
               if message["type"] == "message" and message["data"] == "ALL_DONE":
                   break
           await pubsub.unsubscribe(self._pubsub)
           return self._collect_results()

       async def snapshot(self) -> dict:
           redis = await get_redis()
           task_snapshots = {}
           for task_id in self._tasks:
               data = await redis.hgetall(self._task_key(task_id))
               task_snapshots[task_id] = data
           return {
               "session_id": self.session_id,
               "dag": self._dag,
               "rdag": self._rdag,
               "tasks": {k: asdict(v) for k, v in self._tasks.items()},
               "redis_tasks": task_snapshots,
               "completed_count": self._completed_count,
               "total_count": self._total_count,
           }

       async def restore(self, snapshot: dict) -> None:
           self._dag = snapshot["dag"]
           self._rdag = snapshot["rdag"]
           self._completed_count = snapshot["completed_count"]
           self._total_count = snapshot["total_count"]
           # Rehydrate task objects from snapshot
           self._tasks = {}
           for task_id, data in snapshot["tasks"].items():
               subtask = Subtask.from_dict(data)
               self._tasks[task_id] = subtask
               # Re-enqueue PENDING tasks that are ready
               if subtask.status == TaskStatus.PENDING and not subtask.depends_on:
                   await self._redis.lpush(self._q, task_id)
               # Re-enqueue RETRYING tasks
               if subtask.status == TaskStatus.RETRYING:
                   await self._redis.lpush(self._q, task_id)
               # Check for expired leases and reset to PENDING
               lease_ts = await self._redis.zscore(self._leases, task_id)
               if lease_ts and lease_ts < time.time():
                   subtask.status = TaskStatus.PENDING
                   await self._redis.lpush(self._q, task_id)
                   await self._redis.zrem(self._leases, task_id)
   ```

   关键设计点：
   - **内存 DAG 镜像**：DAG 结构（`_dag`, `_rdag`）在内存中镜像，因为数据量小且仅在 `init_plan` 时变化。实际的任务队列和状态保存在 Redis 中。
   - **租约机制**：每个出队的任务在 ZSET 中获得一个 5 分钟租约。在 `restore()` 时，检测到过期租约后任务会被重新入队。这处理了工作进程崩溃的情况。
   - **Pub/sub 等待机制**：`wait_for_all()` 订阅一个按会话隔离的 Redis pub/sub 频道。当计数达到总数时，`complete()`/`fail()` 会发布消息。
   - **BRPOP 超时**：5 秒。这个值足够低以保证关闭不会延迟太久，也足够高以避免忙轮询。
   - **失败时的级联取消**：当任务失败时，其所有传递性下游任务会被标记为 CANCELLED。这是对 `_rdag` 的递归深度优先遍历。

5. 在 `server/app.py` 中接入 Redis 生命周期：

   ```python
   from nanoclaw.storage.redis_client import get_redis, close_redis

   @app.on_event("startup")
   async def startup():
       if settings.redis_url:
           await get_redis()

   @app.on_event("shutdown")
   async def shutdown():
       await close_redis()
   ```

### 验证

```bash
# 1. Docker services up
# 2. Start backend with NANOCLAW_REDIS_URL set
# 3. Trigger a complex task (goes through planner → dispatch)
# 4. Verify queue operations:
docker compose exec redis redis-cli KEYS 'nanoclaw:queue:*'
docker compose exec redis redis-cli LLEN nanoclaw:queue:<session_id>:ready
docker compose exec redis redis-clI ZRANGE nanoclaw:queue:<session_id>:leases 0 -1 WITHSCORES
```

### 提交信息

```
feat: add RedisQueue with LPUSH/BRPOP, pub/sub wait, and ZSET lease mechanism
```

---

## 任务 5.5：PgCheckpointer — PostgreSQL 支持的 Checkpointer

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/storage/pg_checkpointer.py`

**原因：**
检查点在重启之间持久化图状态。使用现有的 `sessions.serialized_state` 列避免了单独的表，并将检查点生命周期与会话生命周期绑定。

### 步骤

1. 创建 `storage/pg_checkpointer.py`：

   ```python
   """PostgreSQL implementation of Checkpointer."""

   class PgCheckpointer(Checkpointer):
       async def save(self, session_id: str, state: CheckpointState) -> None:
           async with get_session() as s:
               await s.execute(
                   text("""
                       UPDATE sessions
                       SET serialized_state = :state::jsonb
                       WHERE id = :session_id
                   """),
                   {
                       "session_id": session_id,
                       "state": json.dumps(asdict(state)),
                   },
               )
               await s.commit()

       async def load(self, session_id: str) -> CheckpointState | None:
           async with get_session() as s:
               row = (await s.execute(
                   text("SELECT serialized_state FROM sessions WHERE id = :id"),
                   {"id": session_id},
               )).fetchone()
           if row is None or row.serialized_state is None:
               return None
           return CheckpointState.from_dict(json.loads(row.serialized_state))

       async def list_sessions(self) -> list[str]:
           async with get_session() as s:
               rows = (await s.execute(
                   text("SELECT id FROM sessions WHERE serialized_state IS NOT NULL")
               )).fetchall()
           return [row.id for row in rows]
   ```

   为什么在 sessions 表上使用 UPDATE：
   - 每个会话同时只有一个活跃的检查点（最新的图状态）。
   - 覆盖 `serialized_state` 是正确的——我们不需要检查点历史。
   - 这比带有会话外键的独立检查点表更简单、更快。

2. 确保 `storage/checkpointer.py` 中存在 `CheckpointState.from_dict()`。如果不存在则添加——它与 `asdict()` 相对应。

### 验证

```python
# After a complex task execution:
# docker compose exec postgres psql -U nanoclaw -c "SELECT id, serialized_state IS NOT NULL AS has_state FROM sessions;"
# Should show true for the session that ran.
```

### 提交信息

```
feat: add PgCheckpointer using sessions.serialized_state JSONB column
```

---

## 任务 5.6：PgScheduledTaskRepo — PostgreSQL 支持的 ScheduledTaskRepo

**创建的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/scheduler/pg_repo.py`

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/scheduler/repo.py` — 如果抽象基类需要 `close()` 方法

**原因：**
计划任务必须在重启后仍然存活。`scheduled_tasks` 表已经具有来自 `init.sql` 的正确模式。该仓库使用 SQLAlchemy 原始文本查询而非 ORM——该表足够简单。

### 步骤

1. 创建 `scheduler/pg_repo.py`：

   ```python
   """PostgreSQL implementation of ScheduledTaskRepo."""

   class PgScheduledTaskRepo(ScheduledTaskRepo):
       async def create(self, task: ScheduledTask) -> ScheduledTask:
           async with get_session() as s:
               await s.execute(
                   text("""
                       INSERT INTO scheduled_tasks
                           (id, user_id, description, prompt, schedule, enabled, created_at, agent_id, session_id)
                       VALUES
                           (:id, :user_id, :description, :prompt, :schedule, :enabled, :created_at, :agent_id, :session_id)
                   """),
                   asdict(task),
               )
               await s.commit()
           return task

       async def get_due_tasks(self) -> list[ScheduledTask]:
           async with get_session() as s:
               rows = (await s.execute(
                   text("""
                       SELECT * FROM scheduled_tasks
                       WHERE enabled = TRUE
                         AND (last_run IS NULL
                              OR last_run + (schedule::interval) <= NOW())
                   """),
               )).fetchall()
           return [self._row_to_task(r) for r in rows]

       async def update_last_run(self, task_id: str, ts: str) -> None:
           async with get_session() as s:
               await s.execute(
                   text("UPDATE scheduled_tasks SET last_run = :ts::timestamptz WHERE id = :id"),
                   {"id": task_id, "ts": ts},
               )
               await s.commit()

       async def list_all(self) -> list[ScheduledTask]:
           async with get_session() as s:
               rows = (await s.execute(
                   text("SELECT * FROM scheduled_tasks ORDER BY created_at DESC")
               )).fetchall()
           return [self._row_to_task(r) for r in rows]

       async def delete(self, task_id: str) -> None:
           async with get_session() as s:
               await s.execute(
                   text("DELETE FROM scheduled_tasks WHERE id = :id"),
                   {"id": task_id},
               )
               await s.commit()

       def _row_to_task(self, row: Any) -> ScheduledTask:
           return ScheduledTask(
               id=row.id,
               user_id=row.user_id,
               description=row.description,
               prompt=row.prompt,
               schedule=row.schedule,
               enabled=row.enabled,
               created_at=row.created_at,
               last_run=row.last_run.isoformat() if row.last_run else None,
               agent_id=row.agent_id,
               session_id=row.session_id,
           )
   ```

   关于 `get_due_tasks` 的重要说明：
   - `schedule` 列存储的是 cron 表达式字符串。
   - PG 原生不支持 cron 表达式。有两种方法：
     **方案 A（Phase 5 推荐）**：使用 Python `croniter` 库在 WHERE 子句过滤器中评估 cron 表达式。将 `croniter` 加入依赖。保持 `get_due_tasks` 简单：获取所有已启用的任务，在 Python 中过滤。
     **方案 B**：将 `next_run` 存储为计算列并在每次运行后更新。更复杂但扩展性更好。
   - Phase 5 应使用方案 A 以保持简单性。

2. 将 `croniter` 添加到 `pyproject.toml`：

   ```
   "croniter>=6.0",
   ```

3. 优化 `get_due_tasks` 实现：

   ```python
   async def get_due_tasks(self) -> list[ScheduledTask]:
       async with get_session() as s:
           rows = (await s.execute(
               text("SELECT * FROM scheduled_tasks WHERE enabled = TRUE")
           )).fetchall()
       now = datetime.now(timezone.utc)
       due = []
       for r in rows:
           task = self._row_to_task(r)
           try:
               cron = croniter(task.schedule, now)
               prev_run = cron.get_prev(datetime)
               if task.last_run is None or prev_run > datetime.fromisoformat(task.last_run):
                   due.append(task)
           except (ValueError, KeyError):
               continue  # Invalid cron expression — skip silently
       return due
   ```

   该方法将每个 cron 表达式与上次运行时间进行比对。如果上次 cron 触发时间在 `last_run` 之后，则该任务到期。每次检查的时间复杂度为 O(N)（N = 已启用任务数量），对于拥有 <100 个计划任务的个人助手来说是可以接受的。

### 验证

```python
# Insert a scheduled task manually:
# docker compose exec postgres psql -U nanoclaw -c "INSERT INTO scheduled_tasks (id, user_id, description, prompt, schedule, enabled, created_at) VALUES ('test_001', 'default', 'Test task', 'say hello', '* * * * *', TRUE, EXTRACT(EPOCH FROM NOW()));"
# Then call get_due_tasks() — should return the task within the next minute.
```

### 提交信息

```
feat: add PgScheduledTaskRepo with croniter-based get_due_tasks filtering
```

---

## 任务 5.7：配置切换与启动集成

**修改的文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/config.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/deps.py`（如不存在则创建）
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/main.py`

**原因：**
Phase 5 的全部意义在于应用在有可用存储时无缝使用真实存储。此任务集成了根据环境变量选择 Memory 还是 PG/Redis 实现的工厂逻辑。

### 步骤

1. 更新 `config.py` 以包含所有新设置：

   ```python
   class Settings(BaseSettings):
       model_config = {"env_prefix": "NANOCLAW_"}

       # LLM
       llm_provider: str = "openai"
       llm_model: str = "gpt-4o-mini"
       openai_api_key: str | None = None
       anthropic_api_key: str | None = None

       # Server
       host: str = "127.0.0.1"
       port: int = 8420

       # Persistence — existing
       db_path: str = "nanoclaw.db"

       # Infrastructure — Phase 5 additions
       db_url: str | None = None        # postgresql+asyncpg://...
       redis_url: str | None = None     # redis://...
       chroma_url: str | None = None    # http://localhost:8001
   ```

2. 创建 `server/deps.py` — 存储实现的 FastAPI 依赖注入：

   ```python
   """FastAPI dependency injection — returns Memory or PG/Redis implementations."""

   from nanoclaw.config import settings

   # Module-level singletons (set once at startup)
   _session_repo = None
   _task_repo = None
   _task_queue_cls = None
   _checkpointer = None
   _scheduled_task_repo = None

   def is_production() -> bool:
       return settings.db_url is not None

   def get_session_repo() -> SessionRepository:
       global _session_repo
       if _session_repo is None:
           if is_production():
               from nanoclaw.storage.pg_session_repo import PgSessionRepo
               _session_repo = PgSessionRepo()
           else:
               from nanoclaw.storage.session_repo import MemorySessionRepo
               _session_repo = MemorySessionRepo()
       return _session_repo

   def get_task_repo() -> TaskRepository:
       global _task_repo
       if _task_repo is None:
           if is_production():
               from nanoclaw.storage.pg_task_repo import PgTaskRepo
               _task_repo = PgTaskRepo()
           else:
               from nanoclaw.storage.task_repo import MemoryTaskRepo
               _task_repo = MemoryTaskRepo()
       return _task_repo

   def get_checkpointer() -> Checkpointer:
       global _checkpointer
       if _checkpointer is None:
           if is_production():
               from nanoclaw.storage.pg_checkpointer import PgCheckpointer
               _checkpointer = PgCheckpointer()
           else:
               from nanoclaw.storage.checkpointer import LocalFileCheckpointer
               _checkpointer = LocalFileCheckpointer()
       return _checkpointer

   def get_redis_queue(session_id: str) -> RedisQueue:
       from nanoclaw.storage.redis_queue import RedisQueue
       return RedisQueue(session_id)

   def get_memory_queue() -> MemoryQueue:
       from nanoclaw.storage.task_queue import MemoryQueue
       return MemoryQueue()

   def create_queue(session_id: str) -> TaskQueue:
       """Factory: returns RedisQueue if redis_url is set, else MemoryQueue."""
       if settings.redis_url:
           return get_redis_queue(session_id)
       return get_memory_queue()
   ```

   为什么使用模块级单例：
   - SessionRepo、TaskRepo、Checkpointer 是无状态服务（每次调用创建新的数据库会话）。
   - 无需每次请求都重新构造。
   - 队列实例按会话隔离，每次必须新建。

3. 更新 `server/app.py` 在启动时初始化基础设施：

   ```python
   from nanoclaw.storage.db import init_db, close_db
   from nanoclaw.storage.redis_client import get_redis, close_redis

   @app.on_event("startup")
   async def startup():
       if settings.db_url:
           await init_db()
       if settings.redis_url:
           await get_redis()

   @app.on_event("shutdown")
   async def shutdown():
       await close_db()
       await close_redis()
   ```

4. 更新 `agent/supervisor_graph.py`（或任何调用 `create_queue()` 的地方）以使用 `deps.py` 中的工厂：

   ```python
   from nanoclaw.server.deps import create_queue

   def build_supervisor(session_id: str) -> CompiledGraph:
       queue = create_queue(session_id)
       # ... build graph with queue
   ```

### 验证

完整集成测试：

```bash
# Test 1: Mock mode (no env vars)
make backend  # verify it starts and works without Docker

# Test 2: Production mode
make docker
export NANOCLAW_DB_URL=postgresql+asyncpg://nanoclaw:nanoclaw_dev@localhost:5432/nanoclaw
export NANOCLAW_REDIS_URL=redis://localhost:6379/0
make backend
# POST a chat message, verify data in PG and Redis queues
```

### 提交信息

```
feat: add config-driven storage switching (Memory vs PG/Redis via env vars)
```

---

## Phase 5 总结

| 任务 | 创建的文件 | 修改的文件 | 关键依赖 |
|------|-------------|----------------|----------------|
| 5.1 Docker Compose | `docker-compose.yml`, `.env.example`, `backend/migrations/init.sql` | `Makefile`, `.gitignore` | None |
| 5.2 PgSessionRepo | `storage/db.py`, `storage/pg_session_repo.py` | `pyproject.toml`, `config.py`, `server/app.py` | 5.1 |
| 5.3 PgTaskRepo | `storage/pg_task_repo.py` | `storage/task_repo.py` | 5.1, 5.2 |
| 5.4 RedisQueue | `storage/redis_queue.py`, `storage/redis_client.py` | `pyproject.toml`, `config.py`, `server/app.py` | 5.1 |
| 5.5 PgCheckpointer | `storage/pg_checkpointer.py` | `storage/checkpointer.py` | 5.1, 5.2 |
| 5.6 PgScheduledTaskRepo | `scheduler/pg_repo.py` | `pyproject.toml`, `scheduler/repo.py` | 5.1, 5.2 |
| 5.7 配置切换 | `server/deps.py` | `config.py`, `server/app.py`, `agent/supervisor_graph.py` | 5.2–5.6 |

**新增文件总数：** ~10
**修改文件总数：** ~10
**新增 Python 依赖：** `sqlalchemy[asyncio]`, `asyncpg`, `redis[hiredis]`, `croniter`
