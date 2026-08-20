-- Additive MAS control plane. Safe on a live n8n database: CREATE IF NOT EXISTS only.
-- Never DROP n8n tables. postgres-init runs only on a fresh volume. Lab also applies this via psql.

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cases_status_check CHECK (
        status IN ('new', 'running', 'waiting_user', 'done', 'failed')
    )
);

CREATE TABLE IF NOT EXISTS events (
    event_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    task_id TEXT,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    agent_id TEXT,
    status TEXT,
    status_message TEXT,
    handoff_message TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_case_id_event_id_idx ON events (case_id, event_id);

CREATE TABLE IF NOT EXISTS error_traces (
    error_id BIGSERIAL PRIMARY KEY,
    case_id TEXT,
    execution_id TEXT,
    workflow_name TEXT,
    node_name TEXT,
    error_message TEXT,
    error_type TEXT,
    stack TEXT,
    input_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS error_traces_case_id_idx ON error_traces (case_id);
CREATE INDEX IF NOT EXISTS error_traces_execution_id_idx ON error_traces (execution_id);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    case_id TEXT,
    workflow_name TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS executions_case_id_idx ON executions (case_id);

CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id TEXT PRIMARY KEY,
    title TEXT,
    when_to_use TEXT,
    input_required JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_provides JSONB NOT NULL DEFAULT '[]'::jsonb
);

INSERT INTO agent_registry (agent_id, title, when_to_use, input_required, output_provides)
VALUES
    (
        'excel_extractor',
        'Excel Extractor',
        'Если есть Excel-файл и нужно извлечь скважины, даты, дебиты, управления',
        '["excel"]'::jsonb,
        '["excel_table", "normalized_rows"]'::jsonb
    ),
    (
        'calculation_agent',
        'Calculation Agent',
        'Если есть структурная поверхность и траектория, нужно найти пересечение и начало интервала перфорации',
        '["surface", "trajectory"]'::jsonb,
        '["top_perforation_md"]'::jsonb
    )
ON CONFLICT (agent_id) DO UPDATE SET
    title = EXCLUDED.title,
    when_to_use = EXCLUDED.when_to_use,
    input_required = EXCLUDED.input_required,
    output_provides = EXCLUDED.output_provides;
