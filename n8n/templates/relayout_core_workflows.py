#!/usr/bin/env python3
"""Relayout n8n core and retired workflows: compact layered positions + yellow edit-after-import notes.

Usage (repo root or n8n/templates):
  python3 n8n/templates/relayout_core_workflows.py
  python3 n8n/templates/relayout_core_workflows.py --check   # audit only
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "n8n" / "workflows" / "core"
RETIRED = ROOT / "n8n" / "workflows" / "retired"

# Canvas spacing (n8n node ~192x80 visually)
COL = 280
ROW = 170
NODE_W, NODE_H = 200, 88
PAD = 24

# n8n stickyNote color 1 ≈ yellow
YELLOW = 1

EDIT_NOTE_NAME = "edit after import"
EDIT_TITLE = "## edit after import"


def _uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"omegalul-layout:{seed}"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_sticky(n: dict) -> bool:
    return n.get("type") == "n8n-nodes-base.stickyNote"


def _box(n: dict) -> tuple[int, int, int, int]:
    x, y = n.get("position") or [0, 0]
    p = n.get("parameters") or {}
    if _is_sticky(n):
        return int(x), int(y), int(p.get("width") or 420), int(p.get("height") or 260)
    return int(x), int(y), NODE_W, NODE_H


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int], pad: int = PAD) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + pad <= bx or bx + bw + pad <= ax or ay + ah + pad <= by or by + bh + pad <= ay)


def count_overlaps(nodes: list[dict]) -> list[tuple[str, str]]:
    boxes = [(n.get("name") or "?", _box(n)) for n in nodes]
    out: list[tuple[str, str]] = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _overlap(boxes[i][1], boxes[j][1]):
                out.append((boxes[i][0], boxes[j][0]))
    return out


def _iter_links(conns: dict):
    for src, ports in (conns or {}).items():
        if not isinstance(ports, dict):
            continue
        for ptype, groups in ports.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, list):
                    continue
                for link in group:
                    if isinstance(link, dict) and link.get("node"):
                        yield src, link["node"], str(ptype or "main")


def layered_positions(nodes: list[dict], connections: dict) -> dict[str, list[int]]:
    """Main-pipeline layers left→right; AI/tool satellites stacked above their hub."""
    work = [n for n in nodes if not _is_sticky(n)]
    names = {n["name"] for n in work}

    main_adj: dict[str, list[str]] = defaultdict(list)
    satellites: dict[str, list[str]] = defaultdict(list)  # hub <- side nodes
    sat_set: set[str] = set()

    for src, tgt, ptype in _iter_links(connections):
        if src not in names or tgt not in names:
            continue
        if ptype == "main":
            main_adj[src].append(tgt)
        else:
            # LangChain wires: Model/Tool --ai_*--> Agent
            satellites[tgt].append(src)
            sat_set.add(src)

    main_names = names - sat_set
    indeg = {n: 0 for n in main_names}
    for src, tgts in main_adj.items():
        if src not in main_names:
            continue
        for t in tgts:
            if t in main_names:
                indeg[t] = indeg.get(t, 0) + 1

    roots = sorted([n for n, d in indeg.items() if d == 0])
    if not roots and main_names:
        roots = sorted(main_names)[:1]

    # Kahn longest-path layers; remaining cyclic components get heuristic layers
    layer: dict[str, int] = {}
    indeg_work = dict(indeg)
    q = deque(roots)
    while q:
        u = q.popleft()
        layer.setdefault(u, 0)
        for v in main_adj.get(u, []):
            if v not in main_names:
                continue
            layer[v] = max(layer.get(v, 0), layer[u] + 1)
            indeg_work[v] = indeg_work.get(v, 0) - 1
            if indeg_work[v] == 0:
                q.append(v)

    stuck = sorted(main_names - set(layer))
    # Place cycle nodes from any already-layered predecessors; break pure cycles last
    guard = 0
    while stuck and guard < len(main_names) + 5:
        guard += 1
        progress = False
        for n in list(stuck):
            preds = [s for s, ts in main_adj.items() if n in ts and s in main_names]
            known = [layer[p] for p in preds if p in layer]
            if known:
                layer[n] = max(known) + 1
                stuck.remove(n)
                progress = True
            elif not preds:
                layer[n] = 0
                stuck.remove(n)
                progress = True
        if progress:
            continue
        # Pure cycle / mutual deps: assign one seed then retry
        seed = stuck[0]
        base = (max(layer.values()) + 1) if layer else 0
        layer[seed] = base
        stuck.remove(seed)

    for n in main_names:
        layer.setdefault(n, 0)

    by_layer: dict[int, list[str]] = defaultdict(list)
    for n, L in layer.items():
        by_layer[L].append(n)

    ordered_layers: dict[int, list[str]] = {}
    for L in sorted(by_layer):
        if L == 0:
            ordered_layers[L] = sorted(by_layer[L])
            continue
        prev = ordered_layers.get(L - 1, [])
        score = {name: idx for idx, name in enumerate(prev)}
        child_rank: dict[str, float] = {}
        for src in prev:
            for j, t in enumerate(main_adj.get(src, [])):
                if t in by_layer[L]:
                    child_rank[t] = min(child_rank.get(t, 1e9), score[src] + j * 0.01)
        ordered_layers[L] = sorted(by_layer[L], key=lambda n: (child_rank.get(n, 1e6), n))

    positions: dict[str, list[int]] = {}
    for L, names_l in ordered_layers.items():
        x = L * COL
        n = len(names_l)
        total_h = (n - 1) * ROW if n else 0
        y0 = -total_h // 2
        for i, name in enumerate(names_l):
            positions[name] = [x, y0 + i * ROW]

    # Place AI/tool satellites in a tight grid above-left of each hub
    side_row = 130
    side_col = 210
    for hub, sats in sorted(satellites.items()):
        if hub not in positions:
            continue
        hx, hy = positions[hub]
        uniq = list(dict.fromkeys(sats))
        cols = 2 if len(uniq) > 3 else 1
        for i, sat in enumerate(uniq):
            if sat in positions:
                continue
            c = i % cols
            r = i // cols
            positions[sat] = [hx - 50 - c * side_col, hy - side_row * (r + 1) - 10]

    # Any leftover nodes (disconnected)
    leftovers = sorted(names - set(positions))
    if leftovers:
        max_x = max((p[0] for p in positions.values()), default=0) + COL
        for i, name in enumerate(leftovers):
            positions[name] = [max_x, i * ROW]

    return positions


def compact_and_resolve(nodes: list[dict]) -> None:
    """Pack nodes by x-column, then eliminate residual overlaps."""
    exec_nodes = [n for n in nodes if not _is_sticky(n)]
    if not exec_nodes:
        return

    # Bucket by approximate column
    buckets: dict[int, list[dict]] = defaultdict(list)
    for n in exec_nodes:
        col = int(round(n["position"][0] / COL))
        buckets[col].append(n)

    for col in sorted(buckets):
        group = buckets[col]
        group.sort(key=lambda n: (n["position"][1], n.get("name") or ""))
        # Center pack around 0
        total_h = (len(group) - 1) * ROW
        y0 = -total_h // 2
        x = col * COL
        for i, n in enumerate(group):
            # keep slight left offset for satellites that were intentionally shifted
            ox = n["position"][0]
            if abs(ox - x) > 80:
                n["position"] = [ox, y0 + i * ROW]
            else:
                n["position"] = [x, y0 + i * ROW]

    # Resolve any remaining overlaps (including cross-column satellite vs main)
    for _ in range(60):
        moved = False
        for i in range(len(exec_nodes)):
            for j in range(i + 1, len(exec_nodes)):
                a, b = exec_nodes[i], exec_nodes[j]
                if not _overlap(_box(a), _box(b), pad=PAD):
                    continue
                # Prefer vertical separation; if same y prefer push b down
                if a["position"][1] < b["position"][1] or (
                    a["position"][1] == b["position"][1] and (a.get("name") or "") < (b.get("name") or "")
                ):
                    b["position"][1] = a["position"][1] + ROW
                else:
                    a["position"][1] = b["position"][1] + ROW
                moved = True
        if not moved:
            break

    # Second compact pass after nudges (global y renormalize per column)
    buckets = defaultdict(list)
    for n in exec_nodes:
        col = int(round(n["position"][0] / float(COL)))
        buckets[col].append(n)
    for col in sorted(buckets):
        group = buckets[col]
        group.sort(key=lambda n: (n["position"][1], n.get("name") or ""))
        total_h = (len(group) - 1) * ROW
        y0 = -total_h // 2
        for i, n in enumerate(group):
            n["position"][1] = y0 + i * ROW

    # Final cross-column overlap resolve (no further compact)
    for _ in range(80):
        moved = False
        for i in range(len(exec_nodes)):
            for j in range(i + 1, len(exec_nodes)):
                a, b = exec_nodes[i], exec_nodes[j]
                if not _overlap(_box(a), _box(b), pad=PAD):
                    continue
                if a["position"][1] < b["position"][1] or (
                    a["position"][1] == b["position"][1] and (a.get("name") or "") < (b.get("name") or "")
                ):
                    b["position"][1] = a["position"][1] + ROW
                else:
                    a["position"][1] = b["position"][1] + ROW
                moved = True
        if not moved:
            break

def manual_edit_reasons(data: dict) -> list[str]:
    reasons: list[str] = []
    for n in data.get("nodes") or []:
        t = n.get("type") or ""
        p = n.get("parameters") or {}
        name = n.get("name") or "?"
        short = t.split(".")[-1]

        if t == "n8n-nodes-base.dataTable":
            tid = p.get("dataTableId")
            val = str((tid or {}).get("value") if isinstance(tid, dict) else (tid or ""))
            if not val or val.startswith("REPLACE") or val == "REPLACE_IN_UI":
                reasons.append(f"Bind Data Table on **{name}**")
            else:
                reasons.append(f"Confirm Data Table binding on **{name}**")

        if t == "n8n-nodes-base.executeWorkflow":
            wid = p.get("workflowId")
            val = str((wid or {}).get("value") if isinstance(wid, dict) else (wid or ""))
            if not val or "REPLACE" in val:
                reasons.append(f"Bind sub-workflow on **{name}**")
            else:
                reasons.append(f"Confirm sub-workflow binding on **{name}**")

        if t == "n8n-nodes-base.postgres" or short == "postgres":
            reasons.append(f"Set Postgres credentials on **{name}**")
        if "embeddingsOpenAi" in t or "lmChatOpenAi" in t or "openAi" in short.lower():
            reasons.append(f"Set OpenAI credentials on **{name}**")
        if "vectorStorePGVector" in t or "memoryPostgresChat" in t:
            reasons.append(f"Set vector/memory credentials on **{name}**")
        if t.startswith("@n8n/n8n-nodes-langchain.agent"):
            reasons.append(f"Confirm model wiring for agent **{name}**")
        if t == "n8n-nodes-base.webhook" and n.get("credentials"):
            reasons.append(f"Set webhook auth credentials on **{name}**")
        if t == "n8n-nodes-base.set":
            assigns = (((p.get("assignments") or {}).get("assignments")) or [])
            if any(isinstance(a, dict) and a.get("name") == "wipe_data" for a in assigns):
                reasons.append(
                    "Keep **Operator flags** `wipe_data` = false except a manual wipe"
                )
        if short == "toolHttpRequest":
            # tools often inherit; skip noise unless many
            pass

    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def build_edit_note_content(wf_name: str, reasons: list[str]) -> str:
    lines = [
        EDIT_TITLE,
        "",
        f"**{wf_name}** — after UI import, complete bindings before activate:",
        "",
    ]
    for r in reasons[:14]:
        lines.append(f"- {r}")
    if len(reasons) > 14:
        lines.append(f"- …and {len(reasons) - 14} more credential/bind steps in this graph")
    lines.append("")
    lines.append("Do not rely on env()/Globals for corporate UI import.")
    return "\n".join(lines)


def _place_edit_note(nodes: list[dict], width: int, height: int) -> list[int]:
    non_sticky = [n for n in nodes if not _is_sticky(n)]
    if non_sticky:
        xs = [n["position"][0] for n in non_sticky]
        ys = [n["position"][1] for n in non_sticky]
        min_x, min_y = min(xs), min(ys)
    else:
        min_x, min_y = 0, 0
    pos = [min_x - 40, min_y - height - 60]
    for _ in range(8):
        trial = (pos[0], pos[1], width, height)
        hit = False
        for n in nodes:
            if _is_sticky(n) and _overlap(trial, _box(n), pad=16):
                pos[1] -= 40
                hit = True
                break
        if not hit:
            break
    return pos


def _merge_wipe_hint(content: str, reasons: list[str]) -> str:
    if "wipe_data" in content:
        return content
    hint = next((r for r in reasons if "wipe_data" in r), None)
    if not hint:
        return content
    return content.rstrip() + f"\n- {hint}\n"


def upsert_edit_note(data: dict, reasons: list[str]) -> None:
    nodes = data.setdefault("nodes", [])
    existing = None
    keep = []
    for n in nodes:
        if not _is_sticky(n):
            keep.append(n)
            continue
        content = ((n.get("parameters") or {}).get("content") or "").lower()
        name = (n.get("name") or "").lower()
        if name == EDIT_NOTE_NAME or EDIT_TITLE.lower() in content:
            if existing is None:
                existing = n
            continue
        keep.append(n)
    nodes[:] = keep

    if existing is None and not reasons:
        return

    if existing is not None:
        params = existing.setdefault("parameters", {})
        content = _merge_wipe_hint(str(params.get("content") or ""), reasons)
        params["content"] = content
        width = int(params.get("width") or 440)
        height = int(params.get("height") or min(520, 160 + 28 * min(max(len(reasons), 1), 14)))
        existing["position"] = _place_edit_note(nodes, width, height)
        nodes.insert(0, existing)
        return

    width, height = 440, min(520, 160 + 28 * min(len(reasons), 14))
    pos = _place_edit_note(nodes, width, height)
    wf_name = data.get("name") or "workflow"
    note = {
        "parameters": {
            "content": build_edit_note_content(wf_name, reasons),
            "height": height,
            "width": width,
            "color": YELLOW,
        },
        "id": _uid(f"edit-note:{data.get('id') or wf_name}"),
        "name": EDIT_NOTE_NAME,
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": pos,
    }
    nodes.insert(0, note)


def separate_other_stickies(data: dict) -> None:
    """Push non-edit stickies so they don't cover executable nodes."""
    nodes = data.get("nodes") or []
    exec_nodes = [n for n in nodes if not _is_sticky(n)]
    stickies = [n for n in nodes if _is_sticky(n) and (n.get("name") or "") != EDIT_NOTE_NAME]
    if not exec_nodes or not stickies:
        return
    min_x = min(n["position"][0] for n in exec_nodes)
    min_y = min(n["position"][1] for n in exec_nodes)
    # Stack README stickies to the left of the pipeline
    cursor_y = min_y - 40
    for i, s in enumerate(stickies):
        p = s.setdefault("parameters", {})
        w = int(p.get("width") or 420)
        h = int(p.get("height") or 260)
        # Keep existing color unless missing
        p.setdefault("color", 5)
        target = [min_x - w - 80, cursor_y - h]
        # If edit note exists, stay clear of it
        for n in nodes:
            if (n.get("name") or "") == EDIT_NOTE_NAME:
                ex, ey, ew, eh = _box(n)
                if _overlap((target[0], target[1], w, h), (ex, ey, ew, eh), pad=20):
                    target[1] = ey - h - 40
        s["position"] = target
        cursor_y = target[1] - 40


def relayout_workflow(data: dict) -> dict:
    nodes = data.get("nodes") or []
    connections = data.get("connections") or {}
    positions = layered_positions(nodes, connections)
    for n in nodes:
        if _is_sticky(n):
            continue
        name = n.get("name")
        if name in positions:
            n["position"] = positions[name]

    reasons = manual_edit_reasons(data)
    # calculation adapter may have zero reasons — skip note
    upsert_edit_note(data, reasons)
    separate_other_stickies(data)

    compact_and_resolve([n for n in nodes if not _is_sticky(n)])
    # Reposition notes after compact (exec y may have changed)
    upsert_edit_note(data, reasons)
    separate_other_stickies(data)

    # Ensure edit sticky clear of exec nodes
    exec_nodes = [n for n in nodes if not _is_sticky(n)]
    for n in nodes:
        if (n.get("name") or "") != EDIT_NOTE_NAME:
            continue
        for _ in range(20):
            hit = False
            for e in exec_nodes:
                if _overlap(_box(n), _box(e), pad=16):
                    n["position"][1] -= 50
                    hit = True
                    break
            if not hit:
                break
    return data


def audit(path: Path) -> dict:
    data = _load(path)
    ov = count_overlaps(data.get("nodes") or [])
    reasons = manual_edit_reasons(data)
    has_edit = any(
        (n.get("name") or "") == EDIT_NOTE_NAME
        or EDIT_TITLE.lower() in ((n.get("parameters") or {}).get("content") or "").lower()
        for n in data.get("nodes") or []
        if _is_sticky(n)
    )
    return {
        "file": path.name,
        "nodes": len(data.get("nodes") or []),
        "overlaps": len(ov),
        "overlap_pairs": ov[:8],
        "needs_edit": bool(reasons),
        "has_edit_note": has_edit,
        "edit_reasons": len(reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Audit only")
    ap.add_argument("--only", action="append", default=[], help="Substring filter on filename")
    args = ap.parse_args()

    files = sorted(CORE.glob("*.json")) + sorted(RETIRED.glob("*.json") if RETIRED.is_dir() else [])
    if args.only:
        files = [p for p in files if any(s in p.name for s in args.only)]

    if args.check:
        bad = 0
        for p in files:
            a = audit(p)
            flag = ""
            if a["overlaps"]:
                flag += " OVERLAP"
                bad += 1
            if a["needs_edit"] and not a["has_edit_note"]:
                flag += " MISSING_EDIT_NOTE"
                bad += 1
            print(
                f"{a['file']:55} nodes={a['nodes']:3} overlaps={a['overlaps']:3} "
                f"edit={a['needs_edit']} note={a['has_edit_note']}{flag}"
            )
            for pair in a["overlap_pairs"]:
                print(f"   collide: {pair[0]} × {pair[1]}")
        return 1 if bad else 0

    for p in files:
        data = _load(p)
        before = len(count_overlaps(data.get("nodes") or []))
        relayout_workflow(data)
        after = len(count_overlaps(data.get("nodes") or []))
        _save(p, data)
        a = audit(p)
        print(f"relayout {p.name}: overlaps {before} → {after}; edit_note={a['has_edit_note']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
