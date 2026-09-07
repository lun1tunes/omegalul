---
name: mas-brief
description: Writes a self-contained delegation brief for handing one MAS work item to another (cheaper) model or a fresh chat — scope, files, contracts, done criteria, verification commands, forbidden moves. Use when the user wants to delegate, split work, start a new agent session on a plan item, or asks "what should the next model do".
disable-model-invocation: true
---

# Delegation brief for a MAS work item

A brief is what lets a cheaper model do one plan item well: it removes discovery cost and pins the done criteria. One item per brief. Fill every section; delete nothing.

## Template

```markdown
# Task: <one line, verb first>

Read first: `AGENTS.md` (all), `MAS_REFACTORING_PLAN.md` §<n>, <specific rule .mdc>.

## Goal
<what changes for the engineer/system, in one paragraph. Quote the plan item verbatim.>

## Out of scope
<adjacent debt not to touch; e.g. "do not change the orchestrator prompt", "do not touch retired/">

## Where
- <file>: <what to change and why>
- <test file>: <regression to add, with case id>
- regenerate: `cd n8n/templates && python3 generate_<x>.py`

## Contracts to respect
<agent_result shape / ToolError codes / HITL prose / artifacts ids / node versions — only the ones this task touches>

## Evidence to start from
<case ids, trace excerpts, failing test names. If a live defect: paste `mas_trace_case.py` hints and the deciding node output.>

## Done when
- [ ] <behavioural criterion, observable in a trace or test>
- [ ] `python3 scripts/mas_gate.py` GREEN
- [ ] `python3 scripts/mas_gate.py --live [--cases …]` GREEN (if orchestrator/agents/emit/artifacts touched)
- [ ] `MAS_REFACTORING_PLAN.md` row updated + «Ревизия N» entry; `docs.md` if a contract/command changed

## Forbidden
- regex/keyword heuristics instead of an LLM decision; domain facts in the orchestrator prompt
- hand-editing workflow JSON; `toolHttpRequest`; `$env`, `require`, community nodes
- machine text to the engineer; engineer writing `.INC`/JSON
- rewriting goldens; wiping cases; relaxing harness assertions

## Report format
Files changed, counts, case ids of the live run, gate table, what is not done and why.
```

## Sizing guidance

- Good for a cheaper model: one tool/one guard/one summary text/one fixture/one smoke, or a documented recipe from `AGENTS.md` §6 (new keyword card, new tool, new live case).
- Needs a strong model (or a strong model first, to produce the evidence + hypothesis): Qwen behaviour changes from prompt wording, n8n platform quirks (node versions, execution model), cross-language state helpers, anything where the trace does not yet name the deciding node.
- If the item has two deciding nodes or two services, split into two briefs; run the gate between them.
