# Phase 3: Memory + Evaluation Implementation Plan

**Date:** 2026-06-08
**Scope:** Chroma-backed MemoryStore, EventLogger (JSONL), ContextManager with memory retrieval, Reflection Node (auto-generated experience drafts), TUI feedback interaction (confirm/reject)

---

## Prerequisites

- Phases 1-2 complete (basic LangGraph ReAct, Supervisor graph, Worker pool, TaskQueue)
- `backend/pyproject.toml` exists with LangGraph, FastAPI, SSE dependencies
- `cli/` exists with Ink TUI, SSE streaming client
- Chroma must be available as a dependency: add `chromadb>=0.6` to `pyproject.toml`

---

## Task 1: Add Chroma dependency and extend Settings

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/pyproject.toml`
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/config.py`

**What & Why:**
- Add `chromadb>=0.6` to `pyproject.toml` dependencies. Chroma is the vector store for MemoryEntry embeddings.
- Extend `Settings` in `config.py` with `nanoclaw_home: str = ".nanoclaw"` — this is the root directory for all persistence (checkpoints, eval logs, memory data). Default to `.nanoclaw` relative to CWD (personal tool, single-user).
- Also add `chroma_persist_dir: str` under `nanoclaw_home/memory/chroma` so Chroma data lives in a known location.

**步骤：**
1. Open `pyproject.toml`, add `"chromadb>=0.6"` to the `dependencies` list. If `chromadb` has known platform issues on macOS ARM, add `"chromadb"` without version pin for now.
2. Open `config.py`, add two fields to `Settings`:
   - `nanoclaw_home: str = ".nanoclaw"` — Pydantic will read from `NANOCLAW_NANOCLAW_HOME` which is ugly. Override with `model_config` alias: `"nanoclaw_home": env_prefix = ""` with alias `NANOCLAW_HOME`. Actually, simpler: rename the prefix filter so `NANOCLAW_HOME` maps to a field. Set `model_config = {"env_prefix": "NANOCLAW_"}`, then field name must be `home: str = ".nanoclaw"` so env var is `NANOCLAW_HOME`.
   - Optionally `chroma_persist_dir: str = ""` as a computed property that returns `{nanoclaw_home}/memory/chroma`.

3. Create the factory function `get_memory_store()` in future task, not here.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv pip freeze | grep chroma
python -c "from nanoclaw.config import settings; print(settings.home)"
```

**提交信息：**
```
chore: add chromadb dependency and nanoclaw_home config
```

---

## Task 2: Define MemoryEntry data model and MemoryType enum

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/types.py` (new)

**What & Why:**
- `MemoryType` is a `str` enum with four values: `user_profile`, `skill`, `semantic`, `reflection`. This constrains the type field and makes the code self-documenting.
- `MemoryEntry` is a frozen dataclass that represents one stored memory. Fields match the design doc: `id`, `type`, `tags`, `content`, `embedding`, `source`, `confidence`, `created_at`, `confirmed`. Frozen ensures immutability per project coding style.

**步骤：**
1. Create `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/types.py`.
2. Define `MemoryType(str, Enum)` — values exactly as above, using `auto()` or explicit strings.
3. Define `MemoryEntry` as `@dataclass(frozen=True)` — all fields.
   - `id: str` — use `uuid4` string or similar.
   - `type: MemoryType`
   - `tags: list[str]` — default `field(default_factory=list)`
   - `content: str`
   - `embedding: list[float] | None = None` — None for unembedded entries
   - `source: str = ""` — session_id or task_id
   - `confidence: float = 0.0` — [0, 1]
   - `created_at: float` — use `field(default_factory=time.time)`
   - `confirmed: bool = False`

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "from nanoclaw.memory.types import MemoryType, MemoryEntry; e = MemoryEntry(id='x', type=MemoryType.REFLECTION, content='test'); print(e)"
```

**提交信息：**
```
feat: define MemoryEntry dataclass and MemoryType enum
```

---

## Task 3: Implement MemoryStore ABC and ChromaMemoryStore

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/store.py` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/__init__.py` (update — export `MemoryStore`, `ChromaMemoryStore`)

**What & Why:**
- `MemoryStore` is the abstract base class with `save(entry)` and `search(query, tags, top_k)`. This is the interface used by ContextManager and Reflection. It matches the design doc exactly.
- `ChromaMemoryStore` implements the ABC using ChromaDB with a local persistent client. The design specifies a hybrid retrieval strategy: first filter by keyword/tags, if < N results then fall back to pure vector search. Chroma supports both `where` filtering (metadata/tags) and `query` (embedding similarity), which maps directly to this approach.

**Data mapping to Chroma:**
- Chroma document → MemoryEntry (serialized via `dataclasses.asdict`)
- Chroma metadata → `type`, `tags` (stored as string with separator), `source`, `confidence`, `confirmed`, `created_at`
- Chroma ID → MemoryEntry.id
- Chroma embedding → auto-computed by Chroma using default all-MiniLM-L6-v2 (sentence-transformers)

**步骤：**
1. Create `store.py`.
2. Define `MemoryStore(ABC)` with:
   - `@abstractmethod async def save(self, entry: MemoryEntry) -> None`
   - `@abstractmethod async def search(self, query: str, tags: list[str] | None = None, top_k: int = 5) -> list[MemoryEntry]`
3. Define `ChromaMemoryStore(MemoryStore)`:
   - `__init__(self, persist_directory: str)`: Create `chromadb.PersistentClient(path=persist_directory)`. Get or create collection named `"memories"`.
   - Constructor should handle `sentence-transformers` not being installed — catch `ImportError` and log a warning about fallback to all-MiniLM via Chroma's built-in ONNX.
   - `save(entry)`: Convert entry to dict via `asdict()`. Call `self._collection.add(ids=[entry.id], documents=[entry.content], metadatas=[{...}])`.
     - Tags stored in metadata as a comma-separated string (Chroma metadata values must be strings/numbers/bools).
     - Embedding field: if `entry.embedding` is provided, pass it. Otherwise let Chroma auto-embed.
   - `search(query, tags, top_k)`:
     - **Phase A (keyword):** If tags provided, build `where` filter: `{"tags": {"$contains": tag}}` (or use `$and` for multi-tag). Chroma's `$contains` works on string metadata values. Since tags are stored as comma-separated, search for each tag individually and union results.
     - **Phase B (vector):** Call `self._collection.query(query_texts=[query], n_results=top_k, where=where_filter)`. If no tags, `where=None`.
     - **Fallback logic:** If Phase A returns fewer than `top_k` results, call again without the `where` filter and merge: take the keyword-filtered results first, then fill remaining slots with pure-vector results (deduplicate by id).
     - Parse returned documents + metadatas + ids into `MemoryEntry` objects.
4. In `memory/__init__.py`, add exports: `from .store import MemoryStore, ChromaMemoryStore`.
5. In `memory/__init__.py`, add a helper function `create_memory_store(persist_dir: str) -> ChromaMemoryStore` that constructs the store. This keeps construction logic in one place.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
from nanoclaw.memory import ChromaMemoryStore
store = ChromaMemoryStore('/tmp/test_chroma')
store.save(MemoryEntry(id='1', type=MemoryType.SKILL, content='test skill', tags=['python']))
results = await store.search(query='test', tags=['python'], top_k=5)
print(results)
"
```

**提交信息：**
```
feat: implement ChromaMemoryStore with hybrid keyword+vector search
```

---

## Task 4: Define EventLogger data types

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/events.py` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/__init__.py` (new)

**What & Why:**
- Define event type constants and structured event dataclasses so the EventLogger has typed records to write. Six event types from the design doc: `task_start`, `task_end`, `tool_call`, `user_feedback`, `context_stats`, `llm_call`.
- Separating event definitions from the logger keeps the code modular — logger only needs `asdict(event)` to serialize.

**步骤：**
1. Create `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/__init__.py` (empty, or with module docstring).
2. Create `events.py`.
3. Define string constants/`Literal` type for event types:
   ```python
   EVENT_TASK_START = "task_start"
   EVENT_TASK_END = "task_end"
   EVENT_TOOL_CALL = "tool_call"
   EVENT_USER_FEEDBACK = "user_feedback"
   EVENT_CONTEXT_STATS = "context_stats"
   EVENT_LLM_CALL = "llm_call"
   ```
4. Define dataclasses for events that carry structured data:
   - `TaskStartEvent`: `session_id, task_id, description, subtask_count: int, created_at`
   - `TaskEndEvent`: `session_id, task_id, success: bool, result_summary: str, duration_ms: float, error: str | None = None`
   - `ToolCallEvent`: `session_id, task_id, tool_name: str, args_summary: str, result_summary: str, duration_ms: float`
   - `UserFeedbackEvent`: `session_id, feedback_type: str, content: str, memory_entry_id: str | None = None`
   - `ContextStatsEvent`: `session_id, total_tokens: int, compression_count: int, tokens_before: int, tokens_after: int`
   - `LlmCallEvent`: `session_id, task_id, model: str, input_tokens: int, output_tokens: int, duration_ms: float`

5. Each dataclass should have a `to_dict()` method or just use `dataclasses.asdict()`.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "from nanoclaw.eval.events import TaskStartEvent; e = TaskStartEvent(session_id='s1', task_id='t1', description='test', subtask_count=3, created_at=0.0); print(e)"
```

**提交信息：**
```
feat: define evaluation event types and dataclasses
```

---

## Task 5: Implement EventLogger with asyncio.Queue batch writing

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/logger.py` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/__init__.py` (update — export `EventLogger`)

**What & Why:**
- `EventLogger` manages writing JSONL files. The design doc specifies: one JSONL file per session at `$NANOCLAW_HOME/eval/{session_id}/events.jsonl`, with batched writes via `asyncio.Queue` instead of per-event `open()` call. Batched writes reduce I/O overhead significantly when events fire rapidly (tool calls, LLM calls).
- The logger maintains an internal `asyncio.Queue`, and a background `_writer` coroutine drains the queue every 1 second (or every N events) writing a batch of JSON lines to the file.

**步骤：**
1. Create `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/eval/logger.py`.
2. Define `EventLogger`:
   - `__init__(self, base_dir: str | Path)`: Store `base_dir` as Path. Initialize `self._queues: dict[str, asyncio.Queue]` — one queue per session. Initialize `self._writers: dict[str, asyncio.Task]` — one writer task per active session.
   - `async def log_event(self, session_id: str, event_type: str, data: dict) -> None`: Get or create the queue for `session_id`. Put `(event_type, data, timestamp)` tuple into queue. If no writer task exists for this session, start one via `asyncio.create_task(self._writer_loop(session_id))`.
   - `async def _writer_loop(self, session_id: str)`: Infinite loop:
     - Wait up to 1 second for first event via `asyncio.wait_for(queue.get(), timeout=1.0)`.
     - Then drain: while queue is not empty, `queue.get_nowait()` to collect up to 50 events in a batch.
     - Ensure the directory `{base_dir}/{session_id}/` exists (`path.mkdir(parents=True, exist_ok=True)`).
     - Open `events.jsonl` in append mode, write each event as one JSON line: `json.dumps({"type": event_type, "data": data, "timestamp": ts})`.
     - File is kept open during a single drain batch to avoid open/close per event.
     - Handle `asyncio.CancelledError` gracefully — flush remaining events before exiting.
   - `async def close(self)`: Cancel all writer tasks, drain remaining events. This should be called on server shutdown.
   - `async def flush_session(self, session_id: str)`: Force-flush one session's queue (used before closing a session).

3. In `eval/__init__.py`, add `from .logger import EventLogger`.

**验证：**
Create a temporary test script:
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
import asyncio, tempfile
from nanoclaw.eval import EventLogger
async def test():
    with tempfile.TemporaryDirectory() as tmp:
        logger = EventLogger(tmp)
        await logger.log_event('sess_1', 'task_start', {'task_id': 't1'})
        await logger.log_event('sess_1', 'llm_call', {'model': 'gpt-4', 'tokens': 100})
        await asyncio.sleep(2)  # wait for writer to flush
        await logger.close()
        content = open(f'{tmp}/sess_1/events.jsonl').read()
        print(content)
asyncio.run(test())
"
```

**提交信息：**
```
feat: implement EventLogger with asyncio.Queue batch writing to JSONL
```

---

## Task 6: Implement ContextManager with Memory retrieval

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/context/__init__.py` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/context/manager.py` (new)

**What & Why:**
- `ContextManager` is responsible for assembling the LLM prompt from five sources: system prompt, user profile (from memory), skill injections (from memory), thread context (session messages), and active task state (subtask trace). This matches the design doc's context composition diagram.
- It also handles the "compression" aspect — though full micro-compression logic is Phase 3.5 or later. For Phase 3, we implement the memory retrieval integration: the manager takes a `MemoryStore` instance and at `build_prompt()` time, queries relevant memories to inject into the prompt.
- This is the bridge between the memory system and the agent graph — every ReAct loop and Worker invocation should go through `ContextManager.build_prompt()`.

**步骤：**
1. Create `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/context/__init__.py` (empty or with docstring).
2. Create `manager.py`.
3. Define `ContextManager`:
   - `__init__(self, memory_store: MemoryStore)`: Store reference.
   - `async def build_prompt(self, session, active_subtask=None) -> list[BaseMessage]`:
     1. **System prompt** — a constant string defining the AI's role and behavior. For now, a simple string `"You are Nanoclaw, a helpful AI assistant."`. Later will come from config.
     2. **User profile** — Call `memory_store.search(query="user profile", tags=["user_profile"], top_k=3)`. For each result, add a `SystemMessage` with prefixed content: `"[User Profile: {entry.content}]"`.
     3. **Skill injections** — If `active_subtask` is provided, use its `tools_needed` or description to query `memory_store.search(query=subtask.description, tags=["skill"], top_k=2)`. Add as `SystemMessage`: `"[Relevant Skill: {entry.content}]"`.
     4. **Thread context** — Convert `session.messages` to `HumanMessage`/`AIMessage` list. This is the conversation history.
     5. **Active task state** — If `active_subtask` is provided, add a `SystemMessage` with the current subtask status, trace summary, and result so far.
     6. Return the combined list.
   - The order matters: system prompt first, then memory/profile injections, then thread context, then task state. This gives memory context before the conversation, letting the LLM use it as implicit grounding.

4. For the `session` parameter, define a minimal protocol (or use `TypedDict`) so it doesn't depend on the full Session model:
   ```python
   @dataclass
   class SessionContext:
       id: str
       messages: list[ChatMessage]
   ```

5. At the top, define `DEFAULT_SYSTEM_PROMPT` as a module constant.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
from nanoclaw.context.manager import DEFAULT_SYSTEM_PROMPT, ContextManager
print(DEFAULT_SYSTEM_PROMPT)
print('OK')
"
```

**提交信息：**
```
feat: implement ContextManager with memory retrieval and prompt assembly
```

---

## Task 7: Define MemoryEntry schema and implement save/load confirmation

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/reflection.py` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/__init__.py` (update — export `ReflectionEngine`)

**What & Why:**
- `ReflectionEngine` generates experience drafts after a task is completed. The design doc says: Collector node finishes → Reflection node starts → collects subtask DAG, results, failures, tool traces → LLM summarizes "what was learned" → writes `MemoryEntry(confirmed=False)`.
- The reflection is async fire-and-forget — it should not block the main response flow. The user should get their answer immediately while reflection happens in the background.
- Later, the user confirms the experience in TUI, which calls back to the API to set `confirmed=True`.

**步骤：**
1. Create `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/reflection.py`.
2. Define `ReflectionEngine`:
   - `__init__(self, memory_store: MemoryStore)`: Store reference.
   - `async def reflect(self, session_id: str, subtasks: list[Subtask], task_results: dict, llm) -> None`:
     1. Build a reflection prompt: summarize what was accomplished, which tools were used, what errors occurred, what patterns emerged.
     2. Call `llm.ainvoke(...)` with the reflection prompt.
     3. Parse the LLM response to extract:
        - `skill_entries`: Tool patterns worth saving as skills.
        - `profile_entries`: User preferences inferred.
        - `reflection_entries`: General insights.
     4. For each extracted entry, create `MemoryEntry(type=..., content=..., source=session_id, confidence=0.6, confirmed=False)` and call `self.memory_store.save(entry)`.
     5. Log the reflection events via EventLogger (if available).

   Keep the LLM call simple — a single prompt asking "Given this task execution trace, what skills, user preferences, and insights should be saved?" with structured output expectations.

3. Because the real LLM is not wired yet (Phases 1-2 may use a mock), the `ReflectionEngine` should accept an LLM interface — any callable with `ainvoke(prompt) -> str`. This lets it work with both mock and real LLMs.
4. In `memory/__init__.py`, add export for `ReflectionEngine`.
5. Also add `async def confirm_memory(self, entry_id: str) -> bool` to `MemoryStore` — sets `confirmed=True` on an existing entry by reading it, modifying, and re-saving. Or simpler: a dedicated `confirm(entry_id)` method on the store.

**Backend API for confirmation (needed by Task 9):**
6. In `store.py`, add to `MemoryStore(ABC)`:
   - `@abstractmethod async def confirm(self, entry_id: str) -> MemoryEntry | None`: Retrieve entry, set `confirmed=True`, re-save, return updated entry.
   - `@abstractmethod async def delete(self, entry_id: str) -> bool`: Delete an entry (for rejection).

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
from nanoclaw.memory import ReflectionEngine
from nanoclaw.memory.store import ChromaMemoryStore
store = ChromaMemoryStore('/tmp/test_reflection')
engine = ReflectionEngine(store)
print('ReflectionEngine created')
print('await engine.reflect(...) — needs LLM mock')
"
```

**提交信息：**
```
feat: implement ReflectionEngine for post-task experience extraction
```

---

## Task 8: Integrate EventLogger and Reflection into Supervisor graph

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/state.py` (update — extend `AgentState`)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py` (update — add reflection trigger)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py` (update — wire EventLogger into lifecycle)

**What & Why:**
- The Supervisor graph needs to know about `EventLogger` and `ReflectionEngine` so the Collector node can fire off reflection after task completion.
- `AgentState` needs an optional `event_logger` and `reflection_engine` field so nodes can access them.
- The Collector node, after aggregating all subtask results, should:
  1. Create a `TaskEndEvent` and log it via EventLogger.
  2. Fire-and-forget the `ReflectionEngine.reflect()` — use `asyncio.create_task()` so it doesn't block the response.
- The FastAPI app lifecycle (`startup`/`shutdown` events) should create and close the EventLogger.

**步骤：**

1. **Extend `AgentState`** at `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/state.py`:
   - Add `event_logger: EventLogger | None` to AgentState.
   - Add `reflection_engine: ReflectionEngine | None` to AgentState.
   - Add `session_id: str` so nodes know which session they belong to.

2. **Update Collector node** at `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/collector.py`:
   - After collecting all results:
     a. Log `task_end` event: `await state["event_logger"].log_event(state["session_id"], "task_end", {...})`.
     b. Fire reflection: `reflect_task = asyncio.create_task(state["reflection_engine"].reflect(...))`.
     c. Store `reflect_task` reference (optional, for awaiting on shutdown if needed).

3. **Update FastAPI app** at `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py`:
   - In `create_app()`, instantiate `EventLogger` with `settings.nanoclaw_home / "eval"`.
   - Add a `lifespan` context manager (FastAPI lifespan) that creates the logger on startup and closes it on shutdown.
   - Pass the EventLogger instance to the agent graph via state or through dependency injection.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
from nanoclaw.agent.state import AgentState
# AgentState should accept the new optional fields
state: AgentState = {'messages': [], 'session_id': 'test', 'event_logger': None, 'reflection_engine': None}
print('State OK:', state)
"
```

**提交信息：**
```
feat: integrate EventLogger and Reflection into Agent graph and server lifecycle
```

---

## Task 9: Add SSE events for memory reflection (experience_ready)

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py` (update — add `experience_ready` SSE event)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/memory/reflection.py` (update — emit SSE event after reflection)

**What & Why:**
- The Reflection engine generates `MemoryEntry` objects with `confirmed=False`. The frontend needs to know about these so it can show a ThumbsUp/ThumbsDown prompt.
- A new SSE event type `experience_ready` carries the experience summary and the `entry_id`. The frontend listens for this event and renders a confirmation widget.
- The reflection engine needs a callback or an event bus to emit this. Simplest approach: pass a `emit_event` callable that the reflection engine calls after each entry is saved.

**步骤：**
1. In `server/app.py`, define the SSE event type: `"experience_ready"` with data `{"entry_id": str, "summary": str, "type": str}`.
2. Modify `ReflectionEngine.reflect()` to accept a callback parameter: `emit_event: Callable[[str, dict], None] | None = None`.
3. After saving each `MemoryEntry`, call `emit_event("experience_ready", {"entry_id": entry.id, "summary": entry.content[:200], "type": entry.type.value})` if the callback is provided.
4. In the SSE stream handler, when a session is active, create a closure that yields SSE events back to the client. This requires connecting the reflection callback to the SSE generator. Approach:
   - Use an `asyncio.Queue` per active SSE connection as an event bus.
   - The reflection engine callback puts events into this queue.
   - The SSE generator reads from the queue and yields them.
   - This avoids coupling the reflection engine to the HTTP layer.

**验证：**
Manually inspect `app.py` for proper SSE event types and callback wiring. No automated test needed — covered in end-to-end verification.

**提交信息：**
```
feat: add experience_ready SSE event for reflection confirmation flow
```

---

## Task 10: TUI — ThumbsUp/ThumbsDown feedback component

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/components/ExperienceFeedback.tsx` (new)
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/app.tsx` (update — render feedback)
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/client.ts` (update — add confirm/reject API calls)
- `/Users/vagrant/dev/code/python/nanoclaw/cli/src/types.ts` (update — add `ExperienceEntry` type)

**What & Why:**
- The user needs to confirm or reject generated experiences in the TUI. A ThumbsUp/ThumbsDown component sits below the assistant's last message and waits for keyboard input.
- Pressing `t` (thumbs up) calls the API to confirm the memory entry (`confirmed=True`). Pressing `f` (thumbs down) rejects it (deletes or marks as rejected).
- Once the user acts, the component disappears (or shows a confirmation dimmed message).

**步骤：**

1. **Update `types.ts`** — add:
   ```typescript
   export interface ExperienceEntry {
     entry_id: string
     summary: string
     type: "user_profile" | "skill" | "semantic" | "reflection"
   }
   ```

2. **Update `client.ts`** — add two methods:
   - `async function confirmMemory(baseUrl: string, entryId: string): Promise<void>`
   - `async function rejectMemory(baseUrl: string, entryId: string): Promise<void>`
   Both POST to new API endpoints (defined in Task 11): `/memories/{entry_id}/confirm` and `/memories/{entry_id}/reject`.

3. **Create `ExperienceFeedback.tsx`**:
   - Props: `experience: ExperienceEntry`, `onConfirm: () => void`, `onReject: () => void`, `dismissed: boolean`
   - When `dismissed` is false, render a `Box` with:
     - Dimmed text showing the experience preview: `"New insight: {summary}"` (truncate to fit terminal width, ~60 chars).
     - Instruction text: `"[t] confirm  [f] reject  [any] dismiss"`
   - Use `useInput` hook to listen for `t` / `f` keys.
   - On `t`: call `onConfirm()`, component shows `Text(dimColor) "Confirmed"`.
   - On `f`: call `onReject()`, component shows `Text(dimColor) "Rejected"`.
   - On any other key: call `onDismiss()`, component hides.
   - Export as default.

4. **Update `app.tsx`**:
   - Import `ExperienceFeedback`.
   - Add state: `const [pendingExperiences, setPendingExperiences] = useState<ExperienceEntry[]>([])`
   - In the SSE parsing (StreamingChat or app-level), listen for `"experience_ready"` events. Add the entry to `pendingExperiences`.
   - Below the StreamingChat component (or below messages), render `ExperienceFeedback` for the first pending experience.
   - Handlers:
     - `handleConfirm`: call `confirmMemory(config.baseUrl, entryId)`, remove from list.
     - `handleReject`: call `rejectMemory(config.baseUrl, entryId)`, remove from list.
     - `handleDismiss`: just remove from list (don't call API).

5. **Update `StreamingChat.tsx`** or the `app.tsx` SSE logic to also emit `experience_ready` events. Currently, `app.tsx` passes the message to `StreamingChat` which handles its own SSE. To avoid duplicating SSE handling, modify `StreamingChat` to accept an `onExperience` callback prop, or lift SSE parsing up to `app.tsx`.

   Recommended approach: Lift SSE event handling up to `app.tsx`:
   - `app.tsx` manages SSE connection.
   - Passes `streamingContent` to a simplified `StreamingChat` that just displays text.
   - `app.tsx` listens for `experience_ready` events and updates `pendingExperiences`.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/cli
npx tsc --noEmit  # TypeScript check
echo "Manual verification: start app, trigger reflection, see feedback prompt"
```

**提交信息：**
```
feat: implement ExperienceFeedback TUI component with thumbs-up/down
```

---

## Task 11: API endpoints for memory confirmation/rejection

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py` (update — add `/memories/{entry_id}/confirm` and `/memories/{entry_id}/reject`)

**What & Why:**
- The TUI needs HTTP endpoints to confirm or reject memory entries. These are simple mutations on the `MemoryStore`.
- `confirm`: calls `memory_store.confirm(entry_id)` → returns 200 with updated entry.
- `reject`: calls `memory_store.delete(entry_id)` → returns 204 No Content.
- Also need a `GET /memories` endpoint that lists unconfirmed entries (for the TUI to show persisted experiences on reconnect).

**步骤：**
1. In `app.py`, add the `MemoryStore` instance to the app state so route handlers can access it. (Store as `app.state.memory_store`.)
2. Add POST endpoint `/memories/{entry_id}/confirm`:
   - Get `MemoryStore` from `app.state`.
   - Call `await memory_store.confirm(entry_id)`.
   - If entry not found, return 404.
   - Return 200 with the updated entry serialized.
   - Also log a `user_feedback` event via the EventLogger.
3. Add POST endpoint `/memories/{entry_id}/reject`:
   - Get `MemoryStore` from `app.state`.
   - Call `await memory_store.delete(entry_id)`.
   - If entry not found, return 404.
   - Return 204 No Content.
   - Log rejection event.
4. Add GET endpoint `/memories` with optional query params `type`, `confirmed` (bool), `limit`:
   - Query the memory store with appropriate filters.
   - Return list of entries.

**验证：**
```bash
cd /Users/vagrant/dev/code/python/nanoclaw/backend
python -c "
from nanoclaw.config import settings
print('Settings loaded:', settings.home)
# Start server and test with curl
# curl -X POST http://127.0.0.1:8420/memories/test_id/confirm
"
```

**提交信息：**
```
feat: add /memories/{id}/confirm and /memories/{id}/reject API endpoints
```

---

## Task 12: Wire up ContextManager and EventLogger in the ReAct loop

**文件：**
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/agent/nodes/react_agent.py` (update — use ContextManager to build prompt)
- `/Users/vagrant/dev/code/python/nanoclaw/backend/src/nanoclaw/server/app.py` (update — pass EventLogger to graph)

**What & Why:**
- Currently the ReAct loop probably calls the LLM with just `state["messages"]`. It should use `ContextManager.build_prompt()` to include memory context.
- Every LLM call should also log an `llm_call` event via the EventLogger, and every tool call should log a `tool_call` event.
- This is the final integration step that connects all Phase 3 components into the agent execution loop.

**步骤：**

1. **Update the ReAct node** (`react_agent.py` if exists, or wherever the LLM is invoked):
   - Before calling the LLM, create (or get existing) `ContextManager` from state and call `await context_manager.build_prompt(session, active_subtask)`.
   - Pass the resulting messages list to the LLM.
   - After LLM returns, log `llm_call` event with input/output token counts (if available from the response).
   - After each tool call, log `tool_call` event with tool name, args summary, result summary, duration.

   If the ReAct loop is currently simple (single LLM call per cycle), add the logging as decorators or explicit calls.

2. **Update the FastAPI app** (`app.py`):
   - When creating the agent graph, pass the `EventLogger` instance into the graph's initial state.
   - When creating the `ContextManager`, pass the `MemoryStore` instance.
   - The graph build should accept these as dependencies.

3. **Log `task_start` event**: When a new task begins (in the router or at the start of the ReAct loop), log a `task_start` event.

**验证：**
Run the backend and send a test chat. Check that `eval/{session_id}/events.jsonl` contains events.
```bash
# Start server
cd /Users/vagrant/dev/code/python/nanoclaw/backend
uv run nanoclaw &
sleep 2
curl -X POST http://127.0.0.1:8420/chat -H "Content-Type: application/json" -d '{"message": "hello"}'
ls -la .nanoclaw/eval/*/events.jsonl
kill %1
```

**提交信息：**
```
feat: wire ContextManager and EventLogger into ReAct execution loop
```

---

## Summary of file changes

### 新增文件（后端）：
| File | Purpose |
|------|---------|
| `backend/src/nanoclaw/memory/types.py` | `MemoryType` enum, `MemoryEntry` dataclass |
| `backend/src/nanoclaw/memory/store.py` | `MemoryStore(ABC)`, `ChromaMemoryStore` |
| `backend/src/nanoclaw/memory/reflection.py` | `ReflectionEngine` for post-task experience extraction |
| `backend/src/nanoclaw/eval/__init__.py` | Package init |
| `backend/src/nanoclaw/eval/events.py` | Event type constants and dataclasses |
| `backend/src/nanoclaw/eval/logger.py` | `EventLogger` with asyncio.Queue batch writing |
| `backend/src/nanoclaw/context/__init__.py` | Package init |
| `backend/src/nanoclaw/context/manager.py` | `ContextManager` for prompt assembly with memory retrieval |

### 新增文件（前端）：
| File | Purpose |
|------|---------|
| `cli/src/components/ExperienceFeedback.tsx` | ThumbsUp/ThumbsDown TUI component |

### 修改文件：
| File | Changes |
|------|---------|
| `backend/pyproject.toml` | Add `chromadb>=0.6` dependency |
| `backend/src/nanoclaw/config.py` | Add `home` field (from `NANOCLAW_HOME`) |
| `backend/src/nanoclaw/memory/__init__.py` | Export `MemoryStore`, `ChromaMemoryStore`, `ReflectionEngine` |
| `backend/src/nanoclaw/agent/state.py` | Add `session_id`, `event_logger`, `reflection_engine` to AgentState |
| `backend/src/nanoclaw/agent/nodes/collector.py` | Add reflection trigger and event logging |
| `backend/src/nanoclaw/agent/nodes/react_agent.py` | Use ContextManager for prompt; log tool_call and llm_call events |
| `backend/src/nanoclaw/server/app.py` | Add EventLogger lifespan, memory endpoints, SSE event bus |
| `cli/src/types.ts` | Add `ExperienceEntry` interface |
| `cli/src/client.ts` | Add `confirmMemory`, `rejectMemory` API calls |
| `cli/src/app.tsx` | Listen for `experience_ready`, render `ExperienceFeedback` |

### 依赖关系图（执行顺序）：
```
Task 1 (config+deps)
  └── Task 2 (MemoryEntry types) ──┐
                                   ├── Task 3 (MemoryStore) ──┐
                                   │                          ├── Task 6 (ContextManager)
                                   │                          ├── Task 7 (ReflectionEngine) ──┐
                                   │                          │                              ├── Task 8 (Graph integration)
                                   │                          │                              │     └── Task 12 (ReAct wiring)
                                   │                          │                              │
Task 4 (Event types) ──┐           │                          │                              │
                       ├── Task 5 (EventLogger) ──────────────┼──────────────────────────────┘
                       │                                      │
                       └──────────────────────────────────────┼── Task 9 (SSE events)
                                                              │       └── Task 10+11 (TUI feedback + API)
```

---

## Design decisions and rationale

1. **Tags as comma-separated string in Chroma metadata**: Chroma's metadata only accepts string, number, bool values — not lists. Storing tags as `"python,async,reflection"` and using `$contains` for filtering is the simplest approach that works with Chroma's built-in filter syntax.

2. **Hybrid search fallback logic**: The design says "keyword filter first, if < N results then fallback to pure vector search." Implementation: always apply tag filter if provided. If fewer than `top_k` results, do a second query without the tag filter and merge (deduplicating by ID). This balances precision (keyword-filtered) with recall (vector fallback).

3. **Batch writing vs per-event open**: Using `asyncio.Queue` with a 1-second drain timer means events are grouped into batches. A single session might fire 50+ events during a complex task (tool calls, LLM calls per cycle). Writing in batches of 20-50 reduces I/O from 50 fsyncs to 2-3 per session.

4. **Reflection as fire-and-forget**: The design explicitly says Reflection should not block the response. Using `asyncio.create_task()` achieves this. The reflection task runs in the same event loop but yields to the main response flow. If the server shuts down before reflection finishes, it's acceptable to lose that draft — it will be regenerated on the next relevant task or during Dreaming.

5. **One SSE event bus per connection**: Using an `asyncio.Queue` per SSE connection as an ad-hoc event bus lets the Reflection engine emit `experience_ready` events without knowing about HTTP. This is more decoupled than passing the SSE generator around.

6. **MemoryStore.confirm() as update-in-place**: Chroma's API doesn't support partial document updates easily. The simplest approach is: read the entry by ID, recreate it with `confirmed=True`, delete the old document, add the new one. For low-frequency operations (user confirms maybe 1-2 times per session), the overhead is negligible.

---

## Risk notes

- **Chroma on macOS ARM**: `chromadb` has known issues with `grpcio` on Apple Silicon. If `pip install chromadb` fails, install `grpcio` manually first via `pip install grpcio --no-binary grpcio`, or use an older version of chromadb that ships with pre-built binaries. Alternatively, fallback to a pure JSON file store for development (`JsonMemoryStore`).

- **Sentence-transformers download**: Chroma's default embedding function downloads `all-MiniLM-L6-v2` on first use (~80MB). The first `save()` or `search()` call will be slow. This is a one-time cost. Document this in a comment.

- **EventLogger directory creation**: Multiple concurrent events for the same session may race on `mkdir -p`. Using `path.mkdir(parents=True, exist_ok=True)` inside the writer loop (which is single-threaded per session) avoids this.

- **TUI key conflicts**: `t` and `f` are common input keys. The `ExperienceFeedback` component should only capture these keys when it has focus/is visible. Ink's `useInput` captures globally, so check `!dismissed` before processing.
