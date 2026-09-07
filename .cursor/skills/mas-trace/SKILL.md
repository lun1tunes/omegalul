---
name: mas-trace
description: Traces one MAS case end to end (Activity event timeline, orchestrator state and ledger, HITL questions/answers, human-text audit, loop hints, n8n executions and node outputs) to find the real cause of a stuck, looping, wrongly finished or badly worded case. Use when the user names a CASE-… id, says a task hung/looped/finished early/asked a strange question, or before changing any prompt or guard.
---

# Trace a MAS case

## Commands

```bash
python3 scripts/mas_trace_case.py CASE-6a9e6bac-11da25            # timeline + state + text audit + hints
python3 scripts/mas_trace_case.py --last                           # newest case
python3 scripts/mas_trace_case.py CASE-… --n8n                     # + latest executions of Orchestrator / Excel / Schedule workflows
python3 scripts/mas_trace_case.py CASE-… --n8n --node "Parse decision"     # dump one node's output
python3 scripts/mas_trace_case.py CASE-… --json > /tmp/case.json           # raw events + state
```

n8n part needs lab `.env` (`N8N_USERNAME`, `N8N_PASSWORD`, `N8N_HOST_PORT`). In the field: n8n UI → Executions, filter by workflow, open the execution and read the same nodes.

## How to read it

1. **Timeline** — who did what. Key kinds: `agent.handoff` (orchestrator decision + handoff text), `agent.progress`/`agent.result` (what the agent actually did), `hitl.request`/`hitl.answered`, `orchestrator.decision` (payload: `action_type`, `guard`, `verification.all_covered`), `case.finished`.
2. **State** — `ledger.history` is the orchestrator's memory (one row per agent step / human answer); `hitl.answers` keys are question ids (`unlisted_wells_policy`, `Q-…`); `data.excel` (`facts`, `new_wells`), `data.schedule`; artifacts flat ids with filenames.
3. **Human-text audit** — any snake_case / `key=value` / JSON in engineer-facing strings is a defect in the agent summary or HITL composer, not in the harness.
4. **Hints** — repeat handoff without new input (loop), same HITL question twice, `completion_unverified`/`completion_review` guards, `step_count > 8`, `done` without `schedule_out`.

## Typical causes → where to look

| Symptom | Node / code |
|---|---|
| Agent re-called with nothing new | orchestrator `Parse decision` (`repeatDelegation`, `goal_flag_ignored`), agent `Summarize AI steps` |
| Finished but goal not covered | `Verify completion` output, `completion_verified` in `case.finished.payload` |
| Same question again after an answer | agent didn't read `context.hitl.answers` → Schedule `_new_well_defs` / Excel `engineer_answers` |
| Excel agent extracted the wrong table/column | `Excel Extractor AI Agent` intermediateSteps, tool `error.code` (`column_not_dates`, `column_not_found`), inventory in `Open excel session` |
| Tool call failed in n8n | execution `ERROR`; `supplyData … no execute` = wrong tool node type |
| Only one of two workbooks seen | `artifacts` in state (`excel` + `attachments[role=excel]`), `nestArtifacts` twins |

Write down: case id, deciding event/node, quoted evidence, hypothesis. Only then edit code (see `mas-working-contract`).
