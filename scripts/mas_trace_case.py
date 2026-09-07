#!/usr/bin/env python3
"""Lab-only: everything an engineer/agent needs to see about one MAS case, in one screen.

  python3 scripts/mas_trace_case.py CASE-6a9e6bac-11da25            # timeline + state summary + text audit
  python3 scripts/mas_trace_case.py CASE-... --n8n                   # + last n8n executions of the core workflows
  python3 scripts/mas_trace_case.py CASE-... --n8n --node "Parse decision" --exec 74922
  python3 scripts/mas_trace_case.py --last                           # newest case

What it prints
  1. Event timeline from Activity (`kind | actor | status | status_message`), HITL questions/answers, handoffs.
  2. State summary: status, step_count, ledger history, HITL pending/answers, artifacts (flat ids + filenames),
     data buckets (excel facts / new_wells / schedule).
  3. Human-text audit: every engineer-facing string (status_message, hitl questions/options, case.finished)
     checked for machine tokens (snake_case ids, key=value, JSON braces, a|b enums).
  4. Verdict hints: loops (repeated handoff to the same agent without new input), review escalation,
     step budget, done-without-schedule.
  5. --n8n: latest executions of Orchestrator / Excel Extractor / Schedule Builder via the n8n REST
     session (lab .env: N8N_USERNAME / N8N_PASSWORD / N8N_HOST_PORT); `--node` dumps one node's output.

Field note: only the Activity part works on the workstation (Activity :8200). n8n REST is lab-only —
in the field open n8n → Executions in the UI.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ACTIVITY = os.environ.get("MAS_ACTIVITY_URL", "http://127.0.0.1:8200").rstrip("/")
MACHINE_RE = re.compile(r"[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]<>]|\w\|\w")
CORE_WORKFLOWS = ("Orchestrator — MAS", "Agent — Excel Extractor", "Agent — Schedule Builder")


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def cases() -> list[dict[str, Any]]:
    data = get_json(f"{ACTIVITY}/cases")
    return data.get("cases", data) if isinstance(data, dict) else data


def events(case_id: str) -> list[dict[str, Any]]:
    data = get_json(f"{ACTIVITY}/cases/{case_id}/events")
    return data.get("events", data) if isinstance(data, dict) else data


def state(case_id: str) -> dict[str, Any]:
    data = get_json(f"{ACTIVITY}/cases/{case_id}/state")
    return data.get("state", data) if isinstance(data, dict) else {}


def short(text: Any, n: int = 200) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def machine_tokens(text: str) -> list[str]:
    return sorted({m.group(0) for m in MACHINE_RE.finditer(str(text or ""))})


def flatten_artifacts(arts: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "artifact_id" in node or isinstance(node.get("filename"), str):
            aid = str(node.get("artifact_id") or key)
            out[aid] = node
            return
        for k, v in node.items():
            walk(v, str(k))

    walk(arts)
    return out


def print_timeline(evs: list[dict[str, Any]]) -> None:
    print("── timeline ──")
    for e in evs:
        kind = str(e.get("kind") or "")
        actor = str(e.get("actor") or "")
        status = str(e.get("status") or "")
        msg = short(e.get("status_message"), 220)
        extra = ""
        if kind == "agent.handoff":
            extra = f"  → {e.get('agent_id')}: {short(e.get('handoff_message'), 160)}"
        if kind == "hitl.request":
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            q = payload.get("question")
            opts = payload.get("options") or (q.get("options") if isinstance(q, dict) else None) or []
            if isinstance(opts, list) and opts:
                labels = [o.get("label") if isinstance(o, dict) else o for o in opts]
                extra = f"  options: {labels}"
        if kind == "orchestrator.decision":
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            extra = f"  action={payload.get('action_type')} agent={payload.get('agent_id')} step={payload.get('step_count')}"
            if payload.get("guard"):
                extra += f" guard={payload.get('guard')}"
            if isinstance(payload.get("verification"), dict):
                extra += f" verified={payload['verification'].get('all_covered')}"
        print(f"{kind:22s} | {actor:16s} | {status:12s} | {msg}{extra}")


def print_state(st: dict[str, Any]) -> None:
    print("\n── state ──")
    print(f"status={st.get('status')} step_count={st.get('step_count')} version={st.get('version')} error_count={st.get('error_count')}")
    print(f"goal: {short(st.get('goal'), 300)}")
    ledger = st.get("ledger") if isinstance(st.get("ledger"), dict) else {}
    for row in ledger.get("history") or []:
        if isinstance(row, dict):
            print(f"  ledger[{row.get('step')}] {row.get('kind')} {row.get('agent_id') or ''} {row.get('status') or ''}: {short(row.get('summary'), 200)}")
    hitl = st.get("hitl") if isinstance(st.get("hitl"), dict) else {}
    if hitl:
        print(f"hitl.pending={hitl.get('pending')} answers={list((hitl.get('answers') or {}).keys())}")
        for q in hitl.get("questions") or []:
            if isinstance(q, dict):
                print(f"  question {q.get('question_id')}: {short(q.get('question'), 200)}")
    flat = flatten_artifacts(st.get("artifacts"))
    print("artifacts: " + ", ".join(f"{aid}={short(card.get('filename'), 40)}" for aid, card in flat.items()))
    dels = [(aid, card) for aid, card in flat.items() if str(card.get("kind") or "") == "deliverable" or aid in {"schedule_out", "diff"}]
    if dels:
        print("deliverables: " + ", ".join(f"{aid}←{card.get('producer') or '?'}" for aid, card in dels))
    data = st.get("data") if isinstance(st.get("data"), dict) else {}
    for bucket, value in data.items():
        if isinstance(value, dict):
            desc = []
            for key in ("facts", "new_wells", "normalized_rows", "shifted", "added", "removed", "operations"):
                if isinstance(value.get(key), list):
                    desc.append(f"{key}={len(value[key])}")
            other = [k for k in value.keys() if k not in {"facts", "new_wells", "normalized_rows"}]
            print(f"data.{bucket}: {' '.join(desc)} keys={other[:12]}")
    if st.get("last_error"):
        print("last_error:", short(st.get("last_error"), 300))


def audit_text(evs: list[dict[str, Any]], st: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for e in evs:
        kind = str(e.get("kind") or "")
        if kind in {"hitl.request", "case.finished", "agent.result"} or kind.startswith("agent."):
            for label, text in (("status_message", e.get("status_message")),):
                toks = machine_tokens(str(text or ""))
                if toks:
                    problems.append(f"{kind}.{label}: {toks[:5]} ← {short(text, 120)}")
        if kind == "hitl.request":
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            question = payload.get("question") if isinstance(payload.get("question"), str) else (payload.get("question") or {}).get("question")
            toks = machine_tokens(str(question or ""))
            if toks:
                problems.append(f"hitl.question: {toks[:5]} ← {short(question, 120)}")
    hitl = st.get("hitl") if isinstance(st.get("hitl"), dict) else {}
    for q in hitl.get("questions") or []:
        if isinstance(q, dict):
            for opt in q.get("options") or []:
                label = opt.get("label") if isinstance(opt, dict) else opt
                toks = machine_tokens(str(label or ""))
                if toks:
                    problems.append(f"hitl.option: {toks[:5]} ← {label}")
    return problems


def verdict_hints(evs: list[dict[str, Any]], st: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    handoffs: list[tuple[str, int]] = []
    answered_at: list[int] = []
    for i, e in enumerate(evs):
        if e.get("kind") == "agent.handoff":
            handoffs.append((str(e.get("agent_id")), i))
        if e.get("kind") == "hitl.answered":
            answered_at.append(i)
    for (agent_a, ia), (agent_b, ib) in zip(handoffs, handoffs[1:]):
        if agent_a == agent_b and not any(ia < k < ib for k in answered_at):
            hints.append(f"repeat handoff to {agent_b} without a new HITL answer between events {ia}→{ib} (loop?)")
    questions = [short((e.get("payload") or {}).get("question") if isinstance(e.get("payload"), dict) and isinstance((e.get("payload") or {}).get("question"), str) else e.get("status_message"), 80) for e in evs if e.get("kind") == "hitl.request"]
    dupes = {q for q in questions if questions.count(q) > 1}
    if dupes:
        hints.append(f"same HITL question asked more than once: {list(dupes)[:2]}")
    for e in evs:
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if payload.get("guard") in {"completion_review", "completion_unverified"}:
            hints.append(f"orchestrator guard {payload.get('guard')} at step {payload.get('step_count')}")
    steps = int(st.get("step_count") or 0)
    if steps > 8:
        hints.append(f"step_count={steps} > 8 budget")
    flat = flatten_artifacts(st.get("artifacts"))
    dels = [aid for aid, card in flat.items() if str(card.get("kind") or "") == "deliverable" or aid in {"schedule_out", "diff"}]
    if st.get("status") == "done" and not dels:
        hints.append("status done but no agent deliverables in artifacts")
    return hints


# ---- n8n (lab only) -------------------------------------------------------------------------------


def n8n_session() -> tuple[str, Any] | None:
    try:
        from lab_soft_redeploy import load_env  # type: ignore
    except Exception:
        return None
    env = load_env()
    user = env.get("N8N_USERNAME") or env.get("N8N_USER")
    password = env.get("N8N_PASSWORD")
    if not user or not password:
        return None
    base = f"http://127.0.0.1:{env.get('N8N_HOST_PORT', '15678')}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(
        urllib.request.Request(
            base + "/rest/login",
            data=json.dumps({"emailOrLdapLoginId": user, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=30,
    )
    return base, opener


def n8n_get(sess: tuple[str, Any], path: str) -> Any:
    base, opener = sess
    return json.load(opener.open(base + path, timeout=60))


def unflatten_execution(data: Any) -> Any:
    if not isinstance(data, str):
        return data
    arr = json.loads(data)

    def unflat(i: Any) -> Any:
        v = arr[int(i)] if isinstance(i, str) and i.isdigit() else i
        if isinstance(v, dict):
            return {k: unflat(x) for k, x in v.items()}
        if isinstance(v, list):
            return [unflat(x) for x in v]
        return v

    return unflat("0")


def print_n8n(node: str | None, exec_id: str | None, since: str | None) -> None:
    sess = n8n_session()
    if not sess:
        print("\n── n8n ── no lab credentials (.env N8N_USERNAME/N8N_PASSWORD) — open n8n → Executions in the UI")
        return
    print("\n── n8n executions ──")
    workflows = n8n_get(sess, "/rest/workflows")["data"]
    for wf in workflows:
        if wf["name"] not in CORE_WORKFLOWS:
            continue
        flt = urllib.parse.quote(json.dumps({"workflowId": wf["id"]}))
        resp = n8n_get(sess, f"/rest/executions?filter={flt}&limit=6")["data"]
        rows = resp["results"] if isinstance(resp, dict) and "results" in resp else resp
        for ex in rows:
            if since and str(ex.get("startedAt") or "") < since:
                continue
            if exec_id and str(ex["id"]) != str(exec_id):
                continue
            print(f"{wf['name']:28s} #{ex['id']} {ex.get('status'):9s} {ex.get('startedAt')} → {ex.get('stoppedAt')}")
            if node or exec_id or ex.get("status") in {"error", "crashed"}:
                full = n8n_get(sess, f"/rest/executions/{ex['id']}")["data"]
                data = unflatten_execution(full.get("data"))
                result = data.get("resultData", {}) if isinstance(data, dict) else {}
                run = result.get("runData", {})
                print("   nodes:", ", ".join(list(run.keys())[:40]))
                err = result.get("error")
                if err:
                    print("   ERROR:", short(json.dumps(err, ensure_ascii=False), 600))
                if node and node in run:
                    for i, r in enumerate(run[node]):
                        print(f"   == {node} [{i}]:", short(json.dumps(r.get("data", {}), ensure_ascii=False), 2500))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_id", nargs="?", help="CASE-… id (or --last)")
    parser.add_argument("--last", action="store_true", help="use the newest case")
    parser.add_argument("--n8n", action="store_true", help="also list n8n executions (lab .env credentials)")
    parser.add_argument("--node", default=None, help="dump this node's output from the matching executions")
    parser.add_argument("--exec", dest="exec_id", default=None, help="only this n8n execution id")
    parser.add_argument("--json", action="store_true", help="print raw events + state JSON instead of the report")
    args = parser.parse_args()

    case_id = args.case_id
    if args.last or not case_id:
        rows = cases()
        if not rows:
            print("no cases in Activity")
            return 1
        case_id = str(rows[0].get("case_id"))
    evs = events(case_id)
    st = state(case_id)
    if args.json:
        print(json.dumps({"case_id": case_id, "events": evs, "state": st}, ensure_ascii=False, indent=2))
        return 0
    print(f"case {case_id}  ui={ACTIVITY}/t/{case_id}")
    print_timeline(evs)
    print_state(st)
    problems = audit_text(evs, st)
    print("\n── human-text audit ──")
    print("\n".join(problems) if problems else "ok — no machine tokens in engineer-facing text")
    hints = verdict_hints(evs, st)
    print("\n── hints ──")
    print("\n".join(hints) if hints else "none")
    if args.n8n or args.node or args.exec_id:
        since = str(evs[0].get("created_at") or "")[:19] if evs else None
        print_n8n(args.node, args.exec_id, since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
