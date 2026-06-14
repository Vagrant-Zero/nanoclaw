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
