---
name: mas-gate
description: Runs the NOVATEK RE MASter verification gate (workflow regeneration, n8n smokes, pytest suites, offline combat cases, optional lab redeploy + six live cases) and interprets the result. Use before reporting any change as done, when the user asks to verify/test/check the MAS, or when a live case failed.
---

# MAS gate

## Run

```bash
python3 scripts/mas_gate.py                 # offline: regen drift, n8n/tests/*-smoke.js, 4 pytest suites, combat engine (~1–2 min)
python3 scripts/mas_gate.py --live          # + lab redeploy + run_live_five (6 cases, ~10 min)
python3 scripts/mas_gate.py --live --cases combat_case3
python3 scripts/mas_gate.py --only smokes,pytest --keep-going
```

When to add `--live`: any change to `generate_mas_orchestrator.py`, agent generators/prompts, `mas_state_utils.py`, Schedule Builder apply/emit, Excel extraction, Activity artifacts/HITL. Lab only (Docker Compose + `.env`); in the field the equivalent is the n8n Health Check form + one manual case.

## Read the verdict

- `regen` lists JSON files that changed on regeneration → templates were edited without regenerating (or JSON was hand-edited). The files are now refreshed; include them in the change.
- `smokes` failure prints the assertion — fix the generator, not the smoke, unless the behaviour change was intended and documented.
- `pytest` prints the last line per suite; failures print the tail. Excel suite runs from the Activity venv with `PYTHONPATH=excel-agent-tools API_KEY=test-key`.
- `combat` is the offline engine (no LLM). A failure here is a deterministic bug in Schedule Builder — fix before any live run.
- `live` prints one JSON line per case (`ok`, `status`, `mismatch_count`) and `FAIL <case>: <reason>`. Reasons the harness raises: loop (repeat handoff), repeated HITL question, `completion_review`, step budget, `done` without `.INC`, machine text, `.INC` semantic mismatch.

## After a live failure

1. `python3 scripts/mas_trace_case.py CASE-… --n8n` — read the timeline, ledger, HITL answers, hints.
2. Find the deciding event/node (`--node "Parse decision"`, `--node "Summarize AI steps"`, `--node "Excel Extractor AI Agent"`).
3. Only then change code: guard/contract/prompt + regression test with the case id in its docstring.
4. Re-run `--live --cases <that case>` first, then the full `--live`.

Report the final table verbatim plus case ids of the live run. GREEN means done; RED means not done — say which stage and why.
