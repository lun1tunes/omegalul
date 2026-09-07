(() => {
  "use strict";

  const START_LABEL = "Постановка задачи";
  const END_LABEL = "Итог";
  const FINISHED_RESULT_TEXT = "Задача завершена. Загрузите результаты работы.";
  const FIXED_NODES = ["input", "orchestrator", "user", "output"];
  const KIND_LABELS = {
    "case.created": START_LABEL,
    "case.finished": END_LABEL,
    "case.failed": "Задача завершилась с ошибкой",
    "orchestrator.status": "Оркестратор думает",
    "orchestrator.decision": "Решение оркестратора",
    "agent.handoff": "Поручение агенту",
    "agent.accepted": "Агент принял задачу",
    "agent.progress": "Агент работает",
    "agent.result": "Агент вернул результат",
    "agent.failed": "Сбой агента",
    "hitl.request": "Вопрос вам",
    "hitl.answered": "Ваш ответ",
    "system.node_error": "Сбой узла",
  };
  const TONE_STATUS = {
    idle: "Ожидает",
    pending: "Получил задачу",
    active: "В работе",
    waiting: "Ждёт вас",
    done: "Готово",
    error: "Ошибка",
  };
  const ORCH_RE = /orchestrator/i;
  const USER_ROLES = new Set(["", "user", "engineer", "human_operator", "specialist", "mas activity user"]);

  const root = document.getElementById("schemaView");
  const stage = document.getElementById("schemaStage");
  const nodesEl = document.getElementById("schemaNodes");
  const edgesEl = document.getElementById("schemaEdges");
  const rangeEl = document.getElementById("schemaRange");
  const stepEl = document.getElementById("schemaStepLabel");
  const startLabelEl = document.getElementById("schemaStartLabel");
  const endLabelEl = document.getElementById("schemaEndLabel");
  const prevBtn = document.getElementById("schemaPrev");
  const nextBtn = document.getElementById("schemaNext");
  const playBtn = document.getElementById("schemaPlay");
  const countEl = document.getElementById("schemaStepCount");

  if (startLabelEl) startLabelEl.textContent = START_LABEL;
  if (endLabelEl) endLabelEl.textContent = END_LABEL;

  /** @type {Map<string, {title: string, when_to_use: string}>} */
  const registry = new Map();
  let frames = [];
  let index = 0;
  let followLive = true;
  let complete = false;
  let currentFrame = null;
  let agentIds = [];
  let deliverableCards = [];
  let inputCards = [];
  let resizeTimer = 0;
  let playTimer = 0;
  let lastKey = "";
  let peekEl = null;
  let peekFor = null;
  let lastFeed = null;

  const text = (v) => String(v || "").trim();
  const copy = (v) => JSON.parse(JSON.stringify(v));

  function isUser(role) { return USER_ROLES.has(text(role).toLowerCase()); }
  function isOrch(role) { return ORCH_RE.test(text(role)); }
  function agentKey(id) { return `agent:${text(id)}`; }
  function agentIdOf(key) { return String(key || "").startsWith("agent:") ? key.slice(6) : ""; }

  function humanTitle(id) {
    const reg = registry.get(id);
    if (reg && reg.title) return reg.title;
    return text(id).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Агент";
  }
  function initials(label) {
    const words = text(label).split(/\s+/).filter(Boolean);
    if (!words.length) return "·";
    if (words.length === 1) return words[0].slice(0, 2);
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  function nodeMeta(key) {
    if (key === "input") return { kicker: "вход", title: START_LABEL, icon: "i-file" };
    if (key === "orchestrator") return { kicker: "оркестратор", title: "Оркестратор", icon: "i-orch" };
    if (key === "user") return { kicker: "инженер", title: "Вы", icon: "i-user" };
    if (key === "output") return { kicker: "выход", title: END_LABEL, icon: "i-download" };
    const id = agentIdOf(key);
    return { kicker: "агент", title: humanTitle(id), initials: initials(humanTitle(id)), hint: registry.get(id)?.when_to_use || "" };
  }
  function statusLabel(key, tone) {
    if (tone === "idle") {
      if (key === "user") return "Вопросов не было";
      if (key === "orchestrator") return "Ожидает задачу";
      if (key.startsWith("agent:")) return "Не вызывался";
      return "";
    }
    if (key === "output" && tone === "active") return complete ? "Готово" : "Формируется";
    if (key === "input" && tone === "active") return "Принята";
    return TONE_STATUS[tone] || tone;
  }

  // ------------------------------------------------------------------ frames
  function filesFromState(state) {
    const arts = state && typeof state.artifacts === "object" ? state.artifacts : {};
    const names = [];
    const seen = new Set();
    (function walk(value, key) {
      if (key === "schedule_out" || key === "diff" || key === "out") return;
      if (Array.isArray(value)) { value.forEach((v) => walk(v, key)); return; }
      if (value && typeof value === "object") {
        if (value.kind === "deliverable" || value.role === "schedule_out" || value.role === "diff") return;
        if (value.filename || value.artifact_id) {
          const name = text(value.filename);
          if (name && !seen.has(name)) { seen.add(name); names.push(name); }
          return;
        }
        Object.entries(value).forEach(([k, v]) => walk(v, k));
      }
    })(arts, "");
    return names;
  }

  function blankGraph(state, agents) {
    const nodes = {};
    const edges = {};
    for (const key of FIXED_NODES) nodes[key] = { tone: "idle", bubble: null, caption: "" };
    for (const id of agents) nodes[agentKey(id)] = { tone: "idle", bubble: null, caption: "" };
    edges["input>orchestrator"] = { tone: "idle", bubble: null };
    edges["orchestrator>output"] = { tone: "idle", bubble: null };
    edges["orchestrator>user"] = { tone: "idle", bubble: null };
    edges["user>orchestrator"] = { tone: "idle", bubble: null };
    for (const id of agents) {
      edges[`orchestrator>${agentKey(id)}`] = { tone: "idle", bubble: null };
      edges[`${agentKey(id)}>orchestrator`] = { tone: "idle", bubble: null };
    }
    return {
      nodes, edges,
      input: { goal: text(state && state.goal), files: filesFromState(state || {}) },
      output: { result: "", prompt: "" },
      active_node: null, active_edge: null, in_flight: null, last_handoff: {}, last_orch_prompt: "",
    };
  }

  function setCaption(graph, key, value) { const c = text(value); if (c && graph.nodes[key]) graph.nodes[key].caption = c; }
  function clearBubbles(graph, keep) { for (const [k, n] of Object.entries(graph.nodes)) if (k !== keep) n.bubble = null; }
  function activateNode(graph, key, bubble) {
    if (!graph.nodes[key]) return;
    for (const [k, n] of Object.entries(graph.nodes)) {
      if (k === key) continue;
      if (n.tone === "active") n.tone = "done";
      n.bubble = null;
    }
    graph.nodes[key].tone = "active";
    graph.nodes[key].bubble = bubble || null;
    setCaption(graph, key, bubble);
    graph.active_node = key;
  }
  function markDone(graph, key) {
    const n = graph.nodes[key];
    if (!n) return;
    if (n.tone !== "error") n.tone = "done";
    n.bubble = null;
    if (graph.active_node === key) graph.active_node = null;
  }
  function setEdge(graph, id, tone, bubble) {
    if (!graph.edges[id]) return;
    for (const [eid, e] of Object.entries(graph.edges)) {
      if (eid !== id && e.tone === "active" && tone === "active") { e.tone = "done"; e.bubble = null; }
    }
    graph.edges[id].tone = tone;
    graph.edges[id].bubble = tone === "active" ? (bubble || null) : null;
    graph.active_edge = tone === "active" ? id : (graph.active_edge === id ? null : graph.active_edge);
  }
  function eventAgent(event) {
    const a = text(event.agent_id);
    if (a && !isOrch(a) && !isUser(a)) return a;
    const actor = text(event.actor);
    if (actor && !isOrch(actor) && !isUser(actor)) return actor;
    return "";
  }
  function frameLabel(event) {
    const kind = text(event.kind);
    if (kind === "case.created") return START_LABEL;
    if (kind === "case.finished") return END_LABEL;
    if (kind === "agent.handoff" && text(event.handoff_message)) return text(event.handoff_message);
    if (text(event.status_message)) return text(event.status_message);
    return KIND_LABELS[kind] || kind || "Шаг";
  }
  function snapshot(graph, event, idx) {
    return {
      index: idx, label: frameLabel(event), kind: text(event.kind), event_id: event.event_id,
      agent_id: eventAgent(event),
      nodes: copy(graph.nodes), edges: copy(graph.edges), input: copy(graph.input), output: copy(graph.output),
      active_node: graph.active_node, active_edge: graph.active_edge,
    };
  }

  function applyEvent(graph, event) {
    const kind = text(event.kind);
    const statusMessage = text(event.status_message);
    const handoff = text(event.handoff_message);
    const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
    const agent = eventAgent(event);
    const node = agent ? agentKey(agent) : null;
    const out = node ? `orchestrator>${node}` : null;
    const back = node ? `${node}>orchestrator` : null;

    if (kind === "case.created") {
      if (Array.isArray(payload.files) && payload.files.length) graph.input.files = payload.files.map(String).filter(Boolean);
      if (!graph.input.goal && statusMessage) graph.input.goal = statusMessage;
      activateNode(graph, "input", null);
      setEdge(graph, "input>orchestrator", "active");
      return;
    }
    if (kind === "orchestrator.status" || kind === "orchestrator.decision") {
      if (graph.nodes.input.tone === "active") markDone(graph, "input");
      if (graph.edges["input>orchestrator"].tone === "active") setEdge(graph, "input>orchestrator", "done");
      const flying = graph.in_flight;
      if (flying && graph.edges[`${flying}>orchestrator`]?.tone === "active") setEdge(graph, `${flying}>orchestrator`, "done");
      graph.last_orch_prompt = statusMessage || graph.last_orch_prompt || "";
      activateNode(graph, "orchestrator", statusMessage || null);
      return;
    }
    if (kind === "agent.handoff" && node) {
      graph.in_flight = node;
      graph.last_handoff[node] = handoff;
      graph.nodes[node].tone = "pending";
      setEdge(graph, out, "active", handoff || null);
      if (graph.nodes.orchestrator.tone !== "error") {
        graph.nodes.orchestrator.tone = "active";
        if (statusMessage) {
          graph.nodes.orchestrator.bubble = statusMessage;
          setCaption(graph, "orchestrator", statusMessage);
          graph.active_node = "orchestrator";
          clearBubbles(graph, "orchestrator");
        }
      }
      return;
    }
    if ((kind === "agent.accepted" || kind === "agent.progress") && node) {
      graph.in_flight = node;
      const kept = handoff || graph.last_handoff[node] || graph.edges[out].bubble;
      activateNode(graph, node, statusMessage || null);
      setEdge(graph, out, "active", kept);
      return;
    }
    if (kind === "agent.result" && node) {
      setCaption(graph, node, statusMessage);
      markDone(graph, node);
      setEdge(graph, out, "done");
      setEdge(graph, back, "active", statusMessage || null);
      graph.in_flight = null;
      graph.nodes.orchestrator.tone = "pending";
      graph.nodes.orchestrator.bubble = null;
      graph.active_node = null;
      return;
    }
    if (kind === "agent.failed" && node) {
      graph.nodes[node].tone = "error";
      graph.nodes[node].bubble = statusMessage || null;
      setCaption(graph, node, statusMessage);
      graph.active_node = node;
      clearBubbles(graph, node);
      setEdge(graph, out, "done");
      setEdge(graph, back, "error");
      graph.in_flight = null;
      return;
    }
    if (kind === "hitl.request") {
      const question = statusMessage || text(payload.question);
      activateNode(graph, "user", question || null);
      graph.nodes.orchestrator.tone = "waiting";
      graph.nodes.orchestrator.bubble = null;
      setEdge(graph, "orchestrator>user", "active", question || null);
      return;
    }
    if (kind === "hitl.answered") {
      markDone(graph, "user");
      setEdge(graph, "orchestrator>user", "done");
      setEdge(graph, "user>orchestrator", "active", statusMessage || null);
      activateNode(graph, "orchestrator", statusMessage || null);
      return;
    }
    if (kind === "case.finished") {
      for (const [k, n] of Object.entries(graph.nodes)) {
        if (k === "output") continue;
        if (["active", "pending", "waiting"].includes(n.tone)) n.tone = "done";
        n.bubble = null;
      }
      setCaption(graph, "orchestrator", (statusMessage && !/^case\./.test(statusMessage) ? statusMessage : "") || graph.last_orch_prompt);
      for (const e of Object.values(graph.edges)) { if (e.tone === "active") e.tone = "done"; e.bubble = null; }
      graph.output.prompt = "";
      graph.output.result = statusMessage && !/^case\./.test(statusMessage) ? statusMessage : FINISHED_RESULT_TEXT;
      activateNode(graph, "output", null);
      setEdge(graph, "orchestrator>output", "active");
      graph.in_flight = null;
      graph.active_node = "output";
      return;
    }
    if (kind === "case.failed") {
      graph.output.prompt = statusMessage || graph.last_orch_prompt || "";
      graph.output.result = statusMessage || "Задача завершилась с ошибкой.";
      graph.nodes.orchestrator.tone = "error";
      graph.nodes.orchestrator.bubble = statusMessage || null;
      setCaption(graph, "orchestrator", statusMessage);
      graph.nodes.output.tone = "error";
      clearBubbles(graph, "orchestrator");
      setEdge(graph, "orchestrator>output", "error");
      graph.active_node = "orchestrator";
      graph.in_flight = null;
      return;
    }
    if (kind === "system.node_error") {
      graph.nodes.orchestrator.tone = "error";
      graph.nodes.orchestrator.bubble = statusMessage || null;
      setCaption(graph, "orchestrator", statusMessage);
      graph.active_node = "orchestrator";
      clearBubbles(graph, "orchestrator");
    }
  }

  function collectAgents(events) {
    const ids = [];
    const seen = new Set();
    for (const id of registry.keys()) { if (!seen.has(id)) { seen.add(id); ids.push(id); } }
    for (const e of events) {
      const id = eventAgent(e);
      if (id && !seen.has(id)) { seen.add(id); ids.push(id); }
    }
    return ids;
  }

  function buildSchemaFrames(events, state) {
    const rows = Array.isArray(events) ? events.filter((r) => r && typeof r === "object") : [];
    agentIds = collectAgents(rows);
    const graph = blankGraph(state || {}, agentIds);
    if (!rows.length) {
      activateNode(graph, "input", null);
      return [{ ...snapshot(graph, { kind: "case.created" }, 0), label: START_LABEL, active_node: "input", active_edge: null }];
    }
    return rows.map((event, idx) => { applyEvent(graph, event); return snapshot(graph, event, idx); });
  }

  function eventsFromFeed(data) {
    if (Array.isArray(data?.events) && data.events.length) return data.events;
    return (Array.isArray(data?.activity) ? data.activity : []).map((turn) => {
      const details = turn?.details && typeof turn.details === "object" ? turn.details : {};
      return {
        kind: turn.event_type || turn.stage || turn.status,
        actor: turn.from?.role || turn.from_role,
        agent_id: details.agent_id || turn.to?.role || turn.to_role,
        status_message: turn.brief || turn.text || turn.summary,
        handoff_message: turn.handoff_message || details.handoff_message,
        payload: details.payload || {},
        event_id: details.event_id,
      };
    });
  }

  // ------------------------------------------------------------------ layout
  const NODE_W = 196;
  function layoutCenters() {
    const centers = { user: { x: 50, y: 13 }, input: { x: 13, y: 50 }, orchestrator: { x: 50, y: 50 }, output: { x: 87, y: 50 } };
    const n = agentIds.length;
    const stageW = stage.clientWidth || 900;
    // Agents sit on the bottom row; shrink the cards when the stage cannot fit them side by side.
    let nodeW = NODE_W;
    if (n > 1) nodeW = Math.max(132, Math.min(NODE_W, (0.94 * stageW) / n - 12));
    nodesEl.style.setProperty("--node-w", `${Math.round(nodeW)}px`);
    const minGap = ((nodeW + 12) / stageW) * 100;
    const spacing = n <= 1 ? 0 : Math.max(minGap, Math.min(26, 76 / (n - 1)));
    const edge = (nodeW / 2 / stageW) * 100 + 1;
    agentIds.forEach((id, i) => {
      const x = 50 + (i - (n - 1) / 2) * spacing;
      centers[agentKey(id)] = { x: Math.max(edge, Math.min(100 - edge, x)), y: 87 };
    });
    // Side nodes keep clear of the stage border as well.
    centers.input.x = Math.max(edge, 13);
    centers.output.x = Math.min(100 - edge, 87);
    return centers;
  }

  function measuredBoxes() {
    const sr = stage.getBoundingClientRect();
    const map = {};
    for (const el of nodesEl.querySelectorAll(".schema-node")) {
      const br = el.getBoundingClientRect();
      map[el.dataset.node] = { x: br.left - sr.left, y: br.top - sr.top, w: br.width, h: br.height };
    }
    return { boxes: map, w: sr.width, h: sr.height };
  }

  function anchor(box, side) {
    if (side === "left") return { x: box.x, y: box.y + box.h / 2 };
    if (side === "right") return { x: box.x + box.w, y: box.y + box.h / 2 };
    if (side === "top") return { x: box.x + box.w / 2, y: box.y };
    return { x: box.x + box.w / 2, y: box.y + box.h };
  }

  function edgeEnds(id) {
    const [a, b] = id.split(">");
    if (a === "input") return [a, "right", b, "left"];
    if (b === "output") return [a, "right", b, "left"];
    if (b === "user") return [a, "top", b, "bottom"];
    if (a === "user") return [a, "bottom", b, "top"];
    if (a === "orchestrator") return [a, "bottom", b, "top"];
    return [a, "top", b, "bottom"];
  }

  function geometry(id, boxes) {
    const [na, sa, nb, sb] = edgeEnds(id);
    const A = boxes[na]; const B = boxes[nb];
    if (!A || !B) return null;
    const p1 = anchor(A, sa); const p2 = anchor(B, sb);
    const dx = p2.x - p1.x; const dy = p2.y - p1.y;
    let c1; let c2;
    if (sa === "left" || sa === "right") {
      const k = Math.max(24, Math.abs(dx) * 0.42);
      c1 = { x: p1.x + (sa === "right" ? k : -k), y: p1.y };
      c2 = { x: p2.x + (sb === "left" ? -k : k), y: p2.y };
    } else {
      const k = Math.max(20, Math.abs(dy) * 0.45);
      c1 = { x: p1.x, y: p1.y + (sa === "bottom" ? k : -k) };
      c2 = { x: p2.x, y: p2.y + (sb === "top" ? -k : k) };
    }
    const mid = cubic(p1, c1, c2, p2, 0.5);
    return { d: `M ${p1.x} ${p1.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${p2.x} ${p2.y}`, mid };
  }
  function cubic(p0, c1, c2, p1, t) {
    const u = 1 - t;
    return {
      x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p1.x,
      y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p1.y,
    };
  }

  /** Physical wires: one per pair; the logical return edge decides direction/tone when it is live. */
  function drawnEdges() {
    const ids = ["input>orchestrator", "orchestrator>output", "orchestrator>user"];
    for (const id of agentIds) ids.push(`orchestrator>${agentKey(id)}`);
    return ids;
  }
  function backOf(id) {
    const [a, b] = id.split(">");
    return b === "output" || a === "input" ? null : `${b}>${a}`;
  }
  const live = (t) => t === "active" || t === "error";
  function pairVisual(id, edges, activeEdge) {
    const out = edges[id] || { tone: "idle" };
    const backId = backOf(id);
    const back = backId ? edges[backId] || { tone: "idle" } : null;
    if (back && live(back.tone) && (!live(out.tone) || activeEdge === backId)) return { tone: back.tone, dir: "back", bubble: back.bubble || null };
    if (live(out.tone)) return { tone: out.tone, dir: "out", bubble: out.bubble || null };
    const rank = (t) => (live(t) ? 2 : ["done", "pending", "waiting"].includes(t) ? 1 : 0);
    if (back && rank(back.tone) > rank(out.tone)) return { tone: back.tone, dir: "none", bubble: null };
    return { tone: out.tone || "idle", dir: "none", bubble: null };
  }

  // ------------------------------------------------------------------ DOM
  function svgEl(name, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }
  function iconUse(id) {
    const svg = svgEl("svg", { class: "icon", "aria-hidden": "true" });
    svg.append(svgEl("use", { href: `#${id}` }));
    return svg;
  }

  function ensureNodes() {
    const wanted = [...FIXED_NODES, ...agentIds.map(agentKey)];
    const existing = new Set(Array.from(nodesEl.querySelectorAll(".schema-node"), (el) => el.dataset.node));
    for (const el of nodesEl.querySelectorAll(".schema-node")) if (!wanted.includes(el.dataset.node)) el.remove();
    for (const key of wanted) {
      if (existing.has(key)) continue;
      const meta = nodeMeta(key);
      const node = document.createElement("article");
      node.className = "schema-node is-idle";
      node.dataset.node = key;
      node.tabIndex = 0;
      const head = document.createElement("div");
      head.className = "schema-node-head";
      const av = document.createElement("span");
      av.className = "avatar";
      if (meta.icon) av.append(iconUse(meta.icon)); else av.textContent = meta.initials || "·";
      const copyEl = document.createElement("div");
      const kicker = document.createElement("div");
      kicker.className = "schema-node-kicker";
      kicker.textContent = meta.kicker;
      const title = document.createElement("div");
      title.className = "schema-node-title";
      title.textContent = meta.title;
      if (meta.hint) title.title = meta.hint;
      copyEl.append(kicker, title);
      const status = document.createElement("span");
      status.className = "status-pill schema-node-status";
      head.append(av, copyEl, status);
      const caption = document.createElement("p");
      caption.className = "schema-node-caption";
      const files = document.createElement("div");
      files.className = "schema-node-files";
      files.hidden = true;
      node.append(head, caption, files);
      node.addEventListener("click", (ev) => { if (!ev.target.closest("a")) togglePeek(node); });
      node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); togglePeek(node); } });
      nodesEl.append(node);
    }
    // markers once
    if (!edgesEl.querySelector("defs")) {
      const defs = svgEl("defs");
      for (const [id, color] of [["schemaArrowActive", "#00B8F0"], ["schemaArrowDone", "#0033A0"], ["schemaArrowError", "#F90D4B"]]) {
        const m = svgEl("marker", { id, viewBox: "0 0 10 10", refX: "8", refY: "5", markerWidth: "7", markerHeight: "7", markerUnits: "userSpaceOnUse", orient: "auto" });
        m.append(svgEl("path", { d: "M 0 1 L 10 5 L 0 9 Z", fill: color }));
        defs.append(m);
      }
      edgesEl.append(defs);
    }
    const wantedEdges = drawnEdges();
    for (const p of edgesEl.querySelectorAll("path[data-edge]")) if (!wantedEdges.includes(p.dataset.edge)) p.remove();
    for (const id of wantedEdges) {
      if (edgesEl.querySelector(`[data-edge="${cssEscape(id)}"]`)) continue;
      edgesEl.append(svgEl("path", { class: "schema-edge-glow", "data-glow": id }));
      edgesEl.append(svgEl("path", { class: "schema-edge", "data-edge": id }));
    }
    applyCenters();
  }

  function applyCenters() {
    const centers = layoutCenters();
    for (const el of nodesEl.querySelectorAll(".schema-node")) {
      const c = centers[el.dataset.node];
      if (!c) continue;
      el.style.left = `${c.x}%`;
      el.style.top = `${c.y}%`;
    }
  }
  function cssEscape(s) { return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/([:>])/g, "\\$1"); }

  function fileChip(card) {
    const name = text(card.filename || card.artifact_id);
    const el = document.createElement(card.download_path ? "a" : "span");
    el.className = "file-chip";
    if (card.download_path) { el.href = card.download_path; el.setAttribute("download", name); }
    const ext = document.createElement("span");
    ext.className = "ext";
    const e = (name.match(/\.([a-z0-9]{1,6})$/i) || [, "file"])[1].toLowerCase();
    ext.dataset.ext = e;
    ext.textContent = e.slice(0, 4);
    const n = document.createElement("span");
    n.className = "name";
    n.textContent = name;
    el.title = card.summary || name;
    el.append(ext, n);
    if (card.download_path) el.append(iconUse("i-download"));
    return el;
  }

  function paintNodes(frame) {
    for (const el of nodesEl.querySelectorAll(".schema-node")) {
      const key = el.dataset.node;
      const spec = frame.nodes[key] || { tone: "idle" };
      const tone = spec.tone || "idle";
      el.className = `schema-node is-${tone === "error" ? "failed" : tone}`;
      const status = el.querySelector(".schema-node-status");
      const label = statusLabel(key, tone);
      status.textContent = label;
      status.hidden = !label;
      status.dataset.tone = tone === "error" ? "failed" : tone === "active" || tone === "pending" ? "running" : tone === "waiting" ? "waiting" : tone === "done" ? "done" : "";
      const caption = el.querySelector(".schema-node-caption");
      const files = el.querySelector(".schema-node-files");
      files.innerHTML = "";
      files.hidden = true;
      if (key === "input") {
        caption.textContent = frame.input?.goal || "Нет описания задачи";
        const cards = inputCards.length ? inputCards : (frame.input?.files || []).map((f) => ({ filename: f }));
        if (cards.length) { files.hidden = false; for (const c of cards.slice(0, 4)) files.append(fileChip(c)); }
      } else if (key === "output") {
        caption.textContent = frame.output?.result || (complete ? "Нет текста итога" : "Итог появится, когда оркестратор завершит задачу");
        if (complete && deliverableCards.length && ["active", "done", "error"].includes(tone)) {
          files.hidden = false;
          for (const c of deliverableCards.slice(0, 6)) files.append(fileChip(c));
        }
      } else {
        const c = text(spec.caption || spec.bubble);
        caption.textContent = c;
        caption.hidden = !c;
      }
      caption.hidden = !text(caption.textContent);
      el.classList.toggle("is-peekable", Boolean(text(caption.textContent)) || !files.hidden);
      el.dataset.full = text(caption.textContent);
    }
  }

  function paintEdges(frame) {
    applyCenters();
    const { boxes, w, h } = measuredBoxes();
    if (!w || !h) return;
    edgesEl.setAttribute("viewBox", `0 0 ${w} ${h}`);
    stage.querySelectorAll(".schema-slip").forEach((el) => el.remove());
    for (const id of drawnEdges()) {
      const path = edgesEl.querySelector(`[data-edge="${cssEscape(id)}"]`);
      const glow = edgesEl.querySelector(`[data-glow="${cssEscape(id)}"]`);
      if (!path) continue;
      const geo = geometry(id, boxes);
      if (!geo) continue;
      const vis = pairVisual(id, frame.edges, frame.active_edge);
      const tone = vis.tone || "idle";
      path.setAttribute("d", geo.d);
      if (glow) { glow.setAttribute("d", geo.d); glow.setAttribute("class", `schema-edge-glow${tone === "active" ? " is-active" : ""}`); }
      path.setAttribute("class", `schema-edge is-${tone === "error" ? "failed" : tone}`);
      const marker = tone === "active" ? "schemaArrowActive" : tone === "error" ? "schemaArrowError" : tone === "done" || tone === "pending" ? "schemaArrowDone" : "";
      if (marker && vis.dir !== "none") {
        // arrow at the receiving end; for "back" the receiver is the start of the drawn path
        path.setAttribute(vis.dir === "back" ? "marker-start" : "marker-end", `url(#${marker})`);
        path.removeAttribute(vis.dir === "back" ? "marker-end" : "marker-start");
      } else {
        path.removeAttribute("marker-end");
        path.removeAttribute("marker-start");
      }
      if (vis.bubble && tone === "active") {
        const slip = document.createElement("button");
        slip.type = "button";
        slip.className = "schema-slip";
        slip.style.left = `${(geo.mid.x / w) * 100}%`;
        slip.style.top = `${(geo.mid.y / h) * 100}%`;
        const full = text(vis.bubble);
        slip.dataset.full = full;
        slip.textContent = full.length > 64 ? `${full.slice(0, 63).trimEnd()}…` : full;
        slip.title = full;
        slip.addEventListener("click", (ev) => { ev.stopPropagation(); togglePeek(slip); });
        stage.append(slip);
      }
    }
  }
  /** Back edges put the arrow on `marker-start`; auto-start-reverse flips it to point at the receiver. */
  function fixMarkerOrientation() {
    for (const m of edgesEl.querySelectorAll("marker")) m.setAttribute("orient", "auto-start-reverse");
  }

  function paintChrome(frame) {
    stepEl.textContent = frame?.label || "";
    stepEl.title = frame?.label || "";
    countEl.textContent = frames.length ? `шаг ${index + 1} / ${frames.length}` : "шаг 0 / 0";
    rangeEl.max = String(Math.max(frames.length - 1, 0));
    rangeEl.value = String(index);
    rangeEl.disabled = frames.length < 2;
    rangeEl.style.setProperty("--progress", `${frames.length < 2 ? 0 : (index / Math.max(frames.length - 1, 1)) * 100}%`);
    prevBtn.disabled = index <= 0;
    nextBtn.disabled = index >= frames.length - 1;
    playBtn.disabled = frames.length < 2;
    root.dataset.complete = complete ? "true" : "false";
    if (frame?.agent_id || frame?.active_node) {
      const key = frame.active_node || agentKey(frame.agent_id);
      const el = nodesEl.querySelector(`[data-node="${cssEscape(key)}"]`);
      nodesEl.querySelectorAll(".schema-node.is-current").forEach((n) => n.classList.remove("is-current"));
      if (el) el.classList.add("is-current");
    }
  }

  function renderFrame(frame) {
    if (!root || !frame) return;
    currentFrame = frame;
    ensureNodes();
    fixMarkerOrientation();
    const key = `${index}|${JSON.stringify(frame.nodes)}|${JSON.stringify(frame.edges)}|${deliverableCards.length}|${inputCards.length}|${complete}`;
    if (key !== lastKey) {
      lastKey = key;
      hidePeek();
      paintNodes(frame);
    }
    paintChrome(frame);
    if (!root.hidden) requestAnimationFrame(() => paintEdges(frame));
  }

  // ------------------------------------------------------------------ peek
  function ensurePeek() {
    if (peekEl) return peekEl;
    peekEl = document.createElement("div");
    peekEl.className = "schema-peek";
    peekEl.hidden = true;
    peekEl.setAttribute("role", "tooltip");
    stage.append(peekEl);
    return peekEl;
  }
  function hidePeek() {
    if (peekEl) { peekEl.hidden = true; peekEl.innerHTML = ""; }
    peekFor = null;
  }
  function togglePeek(anchorEl) {
    if (peekFor === anchorEl) { hidePeek(); return; }
    const full = text(anchorEl.dataset.full);
    const node = anchorEl.classList.contains("schema-node") ? anchorEl : null;
    const files = node ? node.querySelector(".schema-node-files") : null;
    if (!full && (!files || files.hidden)) { hidePeek(); return; }
    const peek = ensurePeek();
    peek.innerHTML = "";
    peek.classList.add("is-pinned");
    const title = document.createElement("div");
    title.className = "schema-peek-title";
    if (node) {
      const meta = nodeMeta(node.dataset.node);
      title.textContent = meta.title;
      const small = document.createElement("small");
      small.textContent = node.querySelector(".schema-node-status")?.textContent || "";
      title.append(small);
    } else {
      title.textContent = "Сообщение";
    }
    const body = document.createElement("div");
    body.className = "schema-peek-body";
    body.textContent = full;
    peek.append(title, body);
    if (files && !files.hidden) {
      const list = document.createElement("div");
      list.className = "schema-peek-files";
      for (const chip of files.querySelectorAll(".file-chip .name")) {
        const s = document.createElement("span");
        s.textContent = chip.textContent;
        list.append(s);
      }
      peek.append(list);
    }
    peek.hidden = false;
    const sr = stage.getBoundingClientRect();
    const ar = anchorEl.getBoundingClientRect();
    const cx = ar.left - sr.left + ar.width / 2;
    const above = ar.top - sr.top > peek.offsetHeight + 20;
    peek.classList.toggle("is-below", !above);
    peek.style.left = `${Math.max(160, Math.min(sr.width - 160, cx))}px`;
    peek.style.top = `${above ? ar.top - sr.top : ar.bottom - sr.top}px`;
    peekFor = anchorEl;
  }
  document.addEventListener("pointerdown", (ev) => {
    if (!peekEl || peekEl.hidden) return;
    if (peekEl.contains(ev.target) || (peekFor && peekFor.contains(ev.target))) return;
    hidePeek();
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { hidePeek(); stopPlay(); } });

  // ------------------------------------------------------------------ navigation / playback
  function showIndex(next, { user = false } = {}) {
    if (!frames.length) return;
    index = Math.max(0, Math.min(frames.length - 1, next));
    if (user) followLive = index === frames.length - 1;
    renderFrame(frames[index]);
  }
  function stopPlay() {
    if (playTimer) { clearInterval(playTimer); playTimer = 0; }
    playBtn.setAttribute("aria-pressed", "false");
    playBtn.innerHTML = "";
    playBtn.append(iconUse("i-play"));
    playBtn.setAttribute("aria-label", "Проиграть шаги");
  }
  function startPlay() {
    if (frames.length < 2) return;
    if (index >= frames.length - 1) showIndex(0, { user: true });
    playBtn.setAttribute("aria-pressed", "true");
    playBtn.innerHTML = "";
    playBtn.append(iconUse("i-pause"));
    playBtn.setAttribute("aria-label", "Остановить");
    playTimer = setInterval(() => {
      if (index >= frames.length - 1) { stopPlay(); followLive = true; return; }
      showIndex(index + 1, { user: true });
    }, 1100);
  }

  function setFeed(data) {
    const feed = data && typeof data === "object" ? data : {};
    lastFeed = feed;
    const state = { ...(feed.state && typeof feed.state === "object" ? feed.state : {}) };
    if (feed.objective && !state.goal) state.goal = feed.objective;
    const attached = Array.isArray(feed.attached_files) ? feed.attached_files : [];
    if (attached.length && (!state.artifacts || !Object.keys(state.artifacts).length)) {
      state.artifacts = Object.fromEntries(attached.map((name, i) => [`file_${i}`, { filename: name }]));
    }
    const cards = Array.isArray(feed.artifacts) ? feed.artifacts.filter((c) => c && typeof c === "object") : [];
    deliverableCards = Array.isArray(feed.deliverables) && feed.deliverables.length ? feed.deliverables : cards.filter((c) => c.kind === "deliverable");
    inputCards = cards.filter((c) => c.kind === "input");
    const events = eventsFromFeed(feed);
    const prevLen = frames.length;
    frames = buildSchemaFrames(events, state);
    complete = Boolean(feed.schema?.complete) || ["done", "failed"].includes(String(feed.status || "")) || ["case.finished", "case.failed"].includes(frames[frames.length - 1]?.kind);
    if (followLive || index >= frames.length) index = Math.max(frames.length - 1, 0);
    index = Math.max(0, Math.min(frames.length - 1, index));
    if (frames.length !== prevLen) stopPlay();
    renderFrame(frames[index] || frames[0]);
  }

  function reset() {
    stopPlay();
    lastFeed = null;
    deliverableCards = [];
    inputCards = [];
    frames = buildSchemaFrames([], {});
    index = 0;
    followLive = true;
    complete = false;
    lastKey = "";
    hidePeek();
    renderFrame(frames[0]);
  }

  function setAgents(list) {
    for (const row of Array.isArray(list) ? list : []) {
      if (row && row.agent_id) registry.set(String(row.agent_id), { title: String(row.title || row.agent_id), when_to_use: String(row.when_to_use || "") });
    }
    lastKey = "";
    // Rebuild so registry agents appear on the canvas (idle) even before they are called.
    if (lastFeed) setFeed(lastFeed); else reset();
  }

  rangeEl.addEventListener("input", () => { stopPlay(); showIndex(Number(rangeEl.value), { user: true }); });
  prevBtn.addEventListener("click", () => { stopPlay(); showIndex(index - 1, { user: true }); });
  nextBtn.addEventListener("click", () => { stopPlay(); showIndex(index + 1, { user: true }); });
  playBtn.addEventListener("click", () => (playTimer ? stopPlay() : startPlay()));
  root.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowLeft") { ev.preventDefault(); stopPlay(); showIndex(index - 1, { user: true }); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); stopPlay(); showIndex(index + 1, { user: true }); }
  });
  const relayout = () => { if (currentFrame && !root.hidden) requestAnimationFrame(() => paintEdges(currentFrame)); };
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 60); });
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 40); }).observe(stage);
  new MutationObserver(() => { if (root.hidden) { hidePeek(); stopPlay(); } else relayout(); }).observe(root, { attributes: true, attributeFilter: ["hidden"] });

  window.MasSchema = { setFeed, reset, setAgents, buildFrames: buildSchemaFrames, showIndex, relayout, START_LABEL, END_LABEL };

  reset();
})();
