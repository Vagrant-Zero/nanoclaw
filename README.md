# Nanoclaw

**A personal AI assistant.** Nanoclaw understands high-level requests in natural language, decomposes them into structured tasks, executes with available tools, and delivers results back to you — including generated output files when relevant.

### What sets it apart

- **ReAct execution with streaming visibility** — the agent thinks, decides on a tool, executes, and observes the result. Every step streams to your terminal in real time. No black box.
- **Plan then execute (Phase 2)** — complex multi-step requests get decomposed into a DAG of subtasks, dispatched to workers, checked for correctness, and collected into a coherent result.
- **DAG-aware task scheduling** — subtask dependencies are resolved automatically. Workers never need to check `depends_on` — the queue only hands out work whose prerequisites are satisfied.
- **Files as first-class output** — when the agent produces a file, that's the deliverable, not just a chat response.
- **KV cache conscious** — tools are registered once at startup and never modified per-request, keeping the LLM prompt prefix stable for maximum cache hit rate.
- **Checkpoint and recover** — graph state and task queue snapshots enable pause/resume and crash recovery.

### Use cases

- "Analyze this project's architecture and write a design document"
- "Read all CSV files in this directory, clean duplicates, and output a merged report"
- "Search for the latest LangGraph release notes and summarize the breaking changes"
- "Create a new Python package scaffolded with tests, config, and CI"

## Quick Start

```bash
# Prerequisites: Python 3.12+, Node.js 20+, DeepSeek API key

# Install dependencies
make install

# Configure LLM key
cat > backend/.env << 'EOF'
OPENAI_API_KEY=sk-your-deepseek-key
LLM_MODEL=deepseek-v4-pro
LLM_BASE_URL=https://api.deepseek.com
EOF

# Start backend
make backend

# Start TUI (in another terminal)
make app
```

Type a request. You'll see the agent think, call tools, and stream results back to you as it works.
