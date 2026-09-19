---
name: mas-gate
description: Runs the NOVATEK RE MASter verification gate (workflow regeneration, n8n smokes, pytest suites, offline combat cases, optional lab redeploy + thirteen live cases) and interprets the result. Use before reporting any change as done, when the user asks to verify/test/check the MAS, or when a live case failed.
---

# MAS gate

## Run

```bash
python3 scripts/mas_gate.py                 # offline: regen drift, n8n/tests/*-smoke.js, pytest suites, combat engine (~1–2 min)
python3 scripts/mas_gate.py --bundle        # dist/mas-<VERSION>.zip (field import pack; not a default offline stage)
python3 scripts/mas_gate.py --live          # + lab redeploy + knowledge probe + 13 live cases (≤25 min): 6 commissioning (LIVE_FIVE_WORKERS=6), then demo_agent_long_job ∥ three_agent_chain ∥ excel_datasets, then tnav_cluster (serial), then recovery (agent_down, then rework ∥ step_limit)
python3 scripts/mas_gate.py --live --repeat 3   # six commissioning ×3 in one pool (workers=9), demo/chain/datasets/cluster/recovery once; --hot if lab healthy / no JSON drift; ~25 min. Not 13×3. Do not enable demo_agent or tnav_cluster during the six.
python3 scripts/mas_gate.py --only live     # skip offline stages after a GREEN `mas_gate.py`
python3 scripts/mas_gate.py --live --cases combat_case3
python3 scripts/mas_gate.py --live --cases tnav_cluster
python3 scripts/mas_gate.py --only smokes,pytest --keep-going
```

When to add `--live`: any change to `generate_mas_orchestrator.py`, agent generators/prompts, `mas_state_utils.py`, Schedule Builder apply/emit, Excel extraction, Activity artifacts/HITL. Lab only (Docker Compose + `.env`); in the field the equivalent is the n8n Health Check form + one manual case.

## Read the verdict

- `regen` lists JSON files that changed on regeneration → templates were edited without regenerating (or JSON was hand-edited). The files are now refreshed; include them in the change.
- `smokes` failure prints the assertion — fix the generator, not the smoke, unless the behaviour change was intended and documented.
- `pytest` prints the last line per suite; failures print the tail. Excel suite runs from the Activity venv with `PYTHONPATH=excel-agent-tools API_KEY=test-key`.
- `combat` is the offline engine (no LLM). A failure here is a deterministic bug in Schedule Builder — fix before any live run.
- `live` prints one JSON line per case (`ok`, `status`, `mismatch_count`, `steps`, `warnings`, `tool_calls`, `llm_truncated`, `rag_empty`, `kb_calls`, `plan`, `orch_rag_ids`, `duration_ms`, `pass` on commissioning repeats) and `FAIL <case>: <reason>`. Before the 13 slots, `--live` probes Activity `/knowledge` (no Google Fonts/jsDelivr, local `marked`, RU `document_count`, `?q=DATES`). Reasons the harness raises: loop (repeat handoff), repeated HITL question, `completion_review`, step budget, `done` without `.INC`, machine text, `.INC` semantic mismatch, log thresholds (`warnings`/`steps`/`tool_calls`/`llm_truncated`/`rag_empty`/`kb_calls` over the limit — the message names `seq`/`title` of the log record), LLM provider 503/busy, commissioning first orchestrator RAG outside the four policy cards, commissioning vs rebind RAG sets equal, file-less demo still carrying both excel and schedule routing cards, plan missing the agent a retrieved routing card routes. `--repeat N` runs each of the six commissioning specs N times in one pool (same INC/thresholds) and prints a per-case table (passes, step distribution, plan shapes, time, INC); demo/chain/datasets/recovery stay once (recovery mutates excel-tools / `max_steps` and cannot overlap with commissioning or with each other except rework∥step_limit). Do not enable `demo_agent` while the six run. Wall ≤ 20 min. `--hot` when regen did not change JSON. After 8.4, `excel_apply_dataset` fails if `kb_calls < 1`. After 8.5, commissioning fails if the first orchestrator `trace.rag` has extra cards. After 8.6, golden_1 vs golden_2 RAG sets must differ.

## After a live failure

1. `python3 scripts/mas_trace_case.py CASE-… --n8n` — read the timeline, ledger, HITL answers, hints.
2. Find the deciding event/node (`--node "Parse decision"`, `--node "Summarize AI steps"`, `--node "Excel Extractor AI Agent"`).
3. Only then change code: guard/contract/prompt + regression test with the case id in its docstring.
4. Re-run `--live --cases <that case>` first, then the full `--live`.

Report the final table verbatim plus case ids of the live run. GREEN means done; RED means not done — say which stage and why.
