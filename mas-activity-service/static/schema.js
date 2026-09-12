(() => {
  "use strict";

  const START_LABEL = "Постановка задачи";
  const END_LABEL = "Итог";
  const FINISHED_RESULT_TEXT = "Задача завершена. Загрузите результаты работы.";
  const FIXED_NODES = ["engineer", "orchestrator", "output"];
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
    if (key === "engineer") return { kicker: "вы", title: "Инженер", icon: "i-user" };
    if (key === "orchestrator") return { kicker: "оркестратор", title: "Оркестратор", icon: "i-orch" };
    if (key === "output") return { kicker: "выход", title: END_LABEL, icon: "i-download" };
    const id = agentIdOf(key);
    return { kicker: "агент", title: humanTitle(id), initials: initials(humanTitle(id)), hint: registry.get(id)?.when_to_use || "" };
  }
  function statusLabel(key, tone, spec) {
    if (key === "engineer") {
      if (tone === "waiting") return "Вопрос вам";
      if (tone === "active" && spec && spec.hitl_answer) return "Ваш ответ";
      if (tone === "active") return "Постановка";
      if (tone === "done" && spec && spec.hitl_answer) return "Ответил";
      if (tone === "done") return "Задача поставлена";
      return "";
    }
    if (tone === "idle") {
      if (key === "orchestrator") return "Ожидает задачу";
      if (key.startsWith("agent:")) return "Не вызывался";
      return "";
    }
    if (key === "output" && tone === "active") return complete ? "Готово" : "Формируется";
    return TONE_STATUS[tone] || tone;
  }

  function payloadOf(event) {
    return event && event.payload && typeof event.payload === "object" ? event.payload : {};
  }
  function hitlQuestionText(event) {
    const payload = payloadOf(event);
    return text(event.status_message) || text(payload.question);
  }
  function hitlAnswerText(event) {
    const payload = payloadOf(event);
    const raw = payload.answer;
    if (raw && typeof raw === "object") return text(raw.text || raw.label || raw.choice);
    if (text(raw)) return text(raw);
    return text(event.status_message).replace(/^(Пользователь|Инженер)\s+ответил[а]?:\s*/i, "");
  }
  function formatHitl(question, answer) {
    const q = text(question);
    const a = text(answer);
    if (q && a) return `Вопрос: ${q}\nОтвет: ${a}`;
    return a || q;
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
    names.sort((a, b) => {
      const ra = /\.(xlsx|xls|xlsm|xltx|xltm)$/i.test(a) ? 0 : 1;
      const rb = /\.(xlsx|xls|xlsm|xltx|xltm)$/i.test(b) ? 0 : 1;
      return ra - rb;
    });
    return names;
  }

  // Plan (O13): state.plan rows with an agent → the node's caption is the planned result; idle nodes with a
  // plan row read «В плане» instead of «Не вызывался».
  function planByAgent(state) {
    const out = new Map();
    for (const item of Array.isArray(state?.plan) ? state.plan : []) {
      const id = text(item?.agent_id);
      if (!id || !item?.id || item.status === "dropped" || out.has(id)) continue;
      out.set(id, { title: text(item.title || item.id), status: text(item.status || "pending") });
    }
    return out;
  }

  function blankGraph(state, agents) {
    const nodes = {};
    const edges = {};
    const planned = planByAgent(state);
    for (const key of FIXED_NODES) nodes[key] = { tone: "idle", bubble: null, caption: "", hitl_question: "", hitl_answer: "" };
    for (const id of agents) nodes[agentKey(id)] = { tone: "idle", bubble: null, caption: planned.get(id)?.title || "", planned: planned.has(id) };
    edges["engineer>orchestrator"] = { tone: "idle", bubble: null };
    edges["orchestrator>engineer"] = { tone: "idle", bubble: null };
    edges["orchestrator>output"] = { tone: "idle", bubble: null };
    for (const id of agents) {
      edges[`orchestrator>${agentKey(id)}`] = { tone: "idle", bubble: null };
      edges[`${agentKey(id)}>orchestrator`] = { tone: "idle", bubble: null };
    }
    return {
      nodes, edges,
      input: { goal: text(state && state.goal), files: filesFromState(state || {}) },
      output: { result: "", prompt: "" },
      active_node: null, active_edge: null, in_flight: null, last_handoff: {}, last_orch_prompt: "",
      last_hitl: { question: "", answer: "" },
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
    const kind = text(event.kind);
    let label = frameLabel(event);
    if (kind === "hitl.request" && graph.last_hitl.question) label = graph.last_hitl.question;
    if (kind === "hitl.answered") label = formatHitl(graph.last_hitl.question, graph.last_hitl.answer) || label;
    return {
      index: idx, label, kind, event_id: event.event_id,
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
      activateNode(graph, "engineer", null);
      setEdge(graph, "engineer>orchestrator", "active");
      return;
    }
    if (kind === "orchestrator.status" || kind === "orchestrator.decision") {
      if (graph.nodes.engineer.tone === "active") markDone(graph, "engineer");
      if (graph.edges["engineer>orchestrator"].tone === "active") setEdge(graph, "engineer>orchestrator", "done");
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
      const question = hitlQuestionText(event);
      graph.last_hitl = { question, answer: "" };
      const node = graph.nodes.engineer;
      node.hitl_question = question;
      node.hitl_answer = "";
      activateNode(graph, "engineer", question || null);
      node.tone = "waiting";
      graph.nodes.orchestrator.tone = "waiting";
      graph.nodes.orchestrator.bubble = null;
      setEdge(graph, "orchestrator>engineer", "active", question || null);
      graph.active_node = "engineer";
      return;
    }
    if (kind === "hitl.answered") {
      const question = graph.last_hitl.question || text(payload.question);
      const answer = hitlAnswerText(event);
      graph.last_hitl = { question, answer };
      const node = graph.nodes.engineer;
      node.hitl_question = question;
      node.hitl_answer = answer;
      const both = formatHitl(question, answer);
      activateNode(graph, "engineer", both || answer || null);
      setEdge(graph, "orchestrator>engineer", "done");
      setEdge(graph, "engineer>orchestrator", "active", answer || null);
      graph.nodes.orchestrator.tone = "pending";
      graph.nodes.orchestrator.bubble = null;
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
      activateNode(graph, "engineer", null);
      return [{ ...snapshot(graph, { kind: "case.created" }, 0), label: START_LABEL, active_node: "engineer", active_edge: null }];
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
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  function isCompact() {
    const w = stage.clientWidth || 900;
    const h = stage.clientHeight || 400;
    return w < 700 || h < 360;
  }
  function layoutCenters() {
    const compact = isCompact();
    const stageW = Math.max(stage.clientWidth || 900, 1);
    const stageH = Math.max(stage.clientHeight || 400, 1);
    const n = agentIds.length;
    const short = stageH < 500;
    const roomy = !compact && stageW >= 920;
    root.classList.toggle("is-compact", compact);
    root.classList.toggle("is-roomy", roomy && !short);

    const padX = Math.max(14, Math.round(stageW * 0.022));
    const gap = Math.max(12, Math.round(stageW * 0.014));
    const agentMin = compact ? 148 : 176;
    const agentMax = Math.round(clamp(stageW * 0.24, 216, 300));
    let agentW = 216;
    if (n > 0) {
      const agentFit = Math.floor((stageW - 2 * padX - Math.max(n - 1, 0) * gap) / n);
      agentW = clamp(agentFit, agentMin, agentMax);
    }
    const hubMin = compact ? 176 : 196;
    const hubMax = Math.round(clamp(stageW * 0.26, 220, 320));
    const hubFit = Math.floor((stageW - 2 * padX - 2 * Math.max(gap, 20)) / 3.15);
    let hubW = clamp(hubFit, hubMin, hubMax);
    let orchW = clamp(Math.round(hubW * 1.08), hubW, Math.min(hubMax + 28, 340));
    while (padX + hubW + gap > stageW / 2 - orchW / 2 - 4 && hubW > hubMin) {
      hubW -= 4;
      orchW = clamp(Math.round(hubW * 1.08), hubW, Math.min(hubMax + 28, 340));
    }

    const capLines = compact ? 1 : (roomy && !short ? 4 : 3);
    const talkLines = compact ? 3 : (roomy && !short ? 6 : 5);
    nodesEl.style.setProperty("--node-w", `${Math.round(agentW)}px`);
    nodesEl.style.setProperty("--hub-w", `${Math.round(hubW)}px`);
    nodesEl.style.setProperty("--orch-w", `${Math.round(orchW)}px`);
    nodesEl.style.setProperty("--node-typo", roomy ? "1.05" : compact ? "0.96" : "1");
    nodesEl.style.setProperty("--cap-lines", String(capLines));
    nodesEl.style.setProperty("--talk-lines", String(talkLines));

    const pctX = (px) => (px / stageW) * 100;
    const pctY = (px) => (px / stageH) * 100;
    // Hub row uses the top band (HITL no longer sits there). Agents stay on the bottom
    // so the bezier handoffs have a clear lane between the two rows.
    const estAgentH = (compact ? 88 : 128) + capLines * 16;
    const estHubH = (compact ? 120 : 168) + (roomy ? 24 : 0);
    const padY = Math.max(16, Math.round(stageH * 0.04));
    const hubY = clamp(pctY(padY + estHubH / 2), 18, compact ? 34 : 32);
    const agentY = clamp(pctY(stageH - padY - estAgentH / 2), compact ? 70 : 74, 90);
    const centers = {
      engineer: { x: pctX(padX + hubW / 2), y: hubY },
      orchestrator: { x: 50, y: hubY },
      output: { x: 100 - pctX(padX + hubW / 2), y: hubY },
    };
    const spacing = n <= 1 ? 0 : pctX(agentW + gap);
    const agentEdge = pctX(agentW / 2) + 0.5;
    agentIds.forEach((id, i) => {
      const x = n <= 1 ? 50 : 50 + (i - (n - 1) / 2) * spacing;
      centers[agentKey(id)] = { x: clamp(x, agentEdge, 100 - agentEdge), y: agentY };
    });
    return centers;
  }

  function nodeBox(el, sr) {
    const b = el.getBoundingClientRect();
    return { x: b.left - sr.left, y: b.top - sr.top, w: b.width, h: b.height };
  }
  function boxesOverlap(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }
  function placeNode(el, box, sr) {
    el.style.left = `${((box.x + box.w / 2) / sr.width) * 100}%`;
    el.style.top = `${((box.y + box.h / 2) / sr.height) * 100}%`;
  }
  /** Two-row layout: lift the hub band so arrows between orchestrator and agents stay visible. */
  function uncollide() {
    const sr = stage.getBoundingClientRect();
    if (sr.width < 8 || sr.height < 8) return;
    const nodes = Array.from(nodesEl.querySelectorAll(".schema-node"));
    const agents = nodes.filter((el) => String(el.dataset.node).startsWith("agent:"));
    const blockers = nodes.filter((el) => ["engineer", "output", "orchestrator"].includes(el.dataset.node));
    const pad = 8;
    const lane = isCompact() ? 36 : 56;

    const clampBox = (el) => {
      const box = nodeBox(el, sr);
      box.x = Math.max(0, Math.min(sr.width - box.w, box.x));
      box.y = Math.max(0, Math.min(sr.height - box.h, box.y));
      placeNode(el, box, sr);
    };

    if (agents.length && blockers.length) {
      let hubTop = sr.height;
      let hubBottom = 0;
      for (const el of blockers) {
        const b = nodeBox(el, sr);
        hubTop = Math.min(hubTop, b.y);
        hubBottom = Math.max(hubBottom, b.y + b.h);
      }
      let agentTop = sr.height;
      for (const el of agents) {
        const a = nodeBox(el, sr);
        agentTop = Math.min(agentTop, a.y);
      }
      const lift = hubBottom + lane - agentTop;
      if (lift > 0) {
        const shift = Math.min(lift, Math.max(0, hubTop - pad));
        if (shift > 0) {
          for (const el of blockers) {
            const b = nodeBox(el, sr);
            b.y -= shift;
            placeNode(el, b, sr);
          }
        }
      }
    }

    for (const agentEl of agents) {
      for (const blockEl of blockers) {
        const A = nodeBox(agentEl, sr);
        const B = nodeBox(blockEl, sr);
        if (!boxesOverlap(A, B)) continue;
        const up = A.y - lane - B.h;
        if (up >= pad) {
          B.y = up;
          placeNode(blockEl, B, sr);
          continue;
        }
        const down = B.y + B.h + lane;
        if (down + A.h <= sr.height - pad) {
          A.y = down;
          placeNode(agentEl, A, sr);
        }
      }
    }
    for (const el of nodes) clampBox(el);
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
    if (a === "engineer") return [a, "right", b, "left"];
    if (b === "output") return [a, "right", b, "left"];
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
    const ids = ["engineer>orchestrator", "orchestrator>output"];
    for (const id of agentIds) ids.push(`orchestrator>${agentKey(id)}`);
    return ids;
  }
  function backOf(id) {
    const [a, b] = id.split(">");
    return b === "output" ? null : `${b}>${a}`;
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

  function syncNodeChrome(el) {
    const meta = nodeMeta(el.dataset.node);
    const av = el.querySelector(".avatar");
    const kicker = el.querySelector(".schema-node-kicker");
    const title = el.querySelector(".schema-node-title");
    if (kicker) kicker.textContent = meta.kicker;
    if (title) title.textContent = meta.title;
    const hint = text(meta.hint);
    el.dataset.hint = hint;
    if (av && !av.querySelector("svg")) {
      if (meta.icon) { av.textContent = ""; av.append(iconUse(meta.icon)); }
      else av.textContent = meta.initials || "·";
    }
  }

  function ensureNodes() {
    const wanted = [...FIXED_NODES, ...agentIds.map(agentKey)];
    const existing = new Set(Array.from(nodesEl.querySelectorAll(".schema-node"), (el) => el.dataset.node));
    for (const el of nodesEl.querySelectorAll(".schema-node")) if (!wanted.includes(el.dataset.node)) el.remove();
    for (const key of wanted) {
      if (existing.has(key)) continue;
      const node = document.createElement("article");
      node.className = "schema-node is-idle";
      node.dataset.node = key;
      node.tabIndex = 0;
      const head = document.createElement("div");
      head.className = "schema-node-head";
      const av = document.createElement("span");
      av.className = "avatar";
      const copyEl = document.createElement("div");
      const kicker = document.createElement("div");
      kicker.className = "schema-node-kicker";
      const title = document.createElement("div");
      title.className = "schema-node-title";
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
      node.addEventListener("click", (ev) => {
        if (ev.target.closest("a") || !node.classList.contains("is-peekable")) return;
        togglePeek(node);
      });
      node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); togglePeek(node); } });
      nodesEl.append(node);
    }
    for (const el of nodesEl.querySelectorAll(".schema-node")) syncNodeChrome(el);
    // markers once
    if (!edgesEl.querySelector("defs")) {
      const defs = svgEl("defs");
      for (const [id, color] of [["schemaArrowActive", "#00B8F0"], ["schemaArrowDone", "#0033A0"], ["schemaArrowError", "#F90D4B"]]) {
        const m = svgEl("marker", { id, viewBox: "0 0 10 10", refX: "10", refY: "5", markerWidth: "14", markerHeight: "14", markerUnits: "userSpaceOnUse", orient: "auto" });
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

  function prioritizeInputCards(cards) {
    const rank = (card) => {
      const role = String(card.role || "");
      const name = String(card.filename || "").toLowerCase();
      if (role === "excel" || /\.(xlsx|xls|xlsm|xltx|xltm)$/i.test(name)) return 0;
      if (role === "schedule_source") return 1;
      return 2;
    };
    return (cards || []).slice().sort((a, b) => rank(a) - rank(b));
  }

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
    layoutCenters();
    applyCenters();
    for (const el of nodesEl.querySelectorAll(".schema-node")) {
      const key = el.dataset.node;
      const spec = frame.nodes[key] || { tone: "idle" };
      const tone = spec.tone || "idle";
      el.className = `schema-node is-${tone === "error" ? "failed" : tone}`;
      const q0 = text(spec.hitl_question);
      const a0 = text(spec.hitl_answer);
      el.classList.toggle("is-dialogue", Boolean((q0 && a0) || ((tone === "waiting" || tone === "active") && (q0 || a0))));
      const status = el.querySelector(".schema-node-status");
      const label = tone === "idle" && spec.planned ? "В плане" : statusLabel(key, tone, spec);
      if (status) {
        status.textContent = label;
        status.hidden = !label;
        status.dataset.tone = tone === "error" ? "failed" : tone === "active" || tone === "pending" ? "running" : tone === "waiting" ? "waiting" : tone === "done" ? "done" : "";
      }
      const caption = el.querySelector(".schema-node-caption");
      const files = el.querySelector(".schema-node-files");
      files.innerHTML = "";
      files.hidden = true;
      const compact = isCompact();
      root.classList.toggle("is-compact", compact);
      syncNodeChrome(el);
      if (key === "engineer") {
        const q = q0;
        const a = a0;
        if (q && a) {
          caption.textContent = formatHitl(q, a);
        } else if ((tone === "waiting" || tone === "active") && (q || a)) {
          caption.textContent = formatHitl(q, a);
        } else {
          caption.textContent = frame.input?.goal || "Нет описания задачи";
          const cards = prioritizeInputCards(inputCards.length ? inputCards : (frame.input?.files || []).map((f) => ({ filename: f })));
          if (cards.length && !compact) { files.hidden = false; for (const c of cards.slice(0, 2)) files.append(fileChip(c)); }
        }
      } else if (key === "output") {
        caption.textContent = frame.output?.result || (complete ? "Нет текста итога" : "Итог появится, когда оркестратор завершит задачу");
        if (!compact && complete && deliverableCards.length && ["active", "done", "error"].includes(tone)) {
          files.hidden = false;
          for (const c of deliverableCards.slice(0, 3)) files.append(fileChip(c));
        }
      } else {
        const c = text(spec.caption || spec.bubble);
        const hint = text(el.dataset.hint);
        caption.textContent = c || hint;
        caption.classList.toggle("is-hint", Boolean(hint && !c));
      }
      caption.hidden = !text(caption.textContent);
      el.dataset.full = text(caption.textContent);
      el.classList.toggle("is-peekable", files && !files.hidden);
    }
  }

  function overflows(el) {
    if (!el || el.hidden) return false;
    return el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
  }

  function setTruncHint(el, full) {
    if (!el) return false;
    const clip = overflows(el);
    el.classList.toggle("is-truncated", clip);
    const shown = text(full);
    if (clip && shown) el.setAttribute("title", shown);
    else el.removeAttribute("title");
    return clip;
  }

  function markOverflow() {
    for (const el of nodesEl.querySelectorAll(".schema-node")) {
      const caption = el.querySelector(".schema-node-caption");
      const files = el.querySelector(".schema-node-files");
      const title = el.querySelector(".schema-node-title");
      const status = el.querySelector(".schema-node-status");
      const hint = text(el.dataset.hint);
      const clip = setTruncHint(caption, el.dataset.full);
      const hasFiles = Boolean(files && !files.hidden);
      el.classList.toggle("is-peekable", clip || hasFiles || (Boolean(hint) && !text(el.dataset.full)));
      if (status && !status.hidden) setTruncHint(status, status.textContent);
      if (title) {
        const titleClip = setTruncHint(title, title.textContent);
        if (!titleClip && hint) title.setAttribute("title", hint);
      }
    }
    for (const slip of stage.querySelectorAll(".schema-slip")) {
      const clip = setTruncHint(slip, slip.dataset.full);
      slip.classList.toggle("is-peekable", clip);
    }
    if (stepEl) setTruncHint(stepEl, stepEl.textContent);
  }

  function paintEdges(frame) {
    applyCenters();
    uncollide();
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
        slip.textContent = full;
        slip.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (slip.classList.contains("is-truncated")) togglePeek(slip);
        });
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
    const key = `${index}|${JSON.stringify(frame.nodes)}|${JSON.stringify(frame.edges)}|${deliverableCards.length}|${inputCards.length}|${complete}|${stage.clientWidth}|${stage.clientHeight}`;
    if (key !== lastKey) {
      lastKey = key;
      hidePeek();
      paintNodes(frame);
    }
    paintChrome(frame);
    if (!root.hidden) requestAnimationFrame(() => { paintEdges(frame); markOverflow(); });
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
    if (peekEl) {
      peekEl.hidden = true;
      peekEl.innerHTML = "";
      peekEl.classList.remove("is-below");
      peekEl.style.visibility = "";
    }
    peekFor = null;
  }
  function placePeek(peek, anchorEl) {
    const sr = stage.getBoundingClientRect();
    const ar = anchorEl.getBoundingClientRect();
    const need = peek.offsetHeight + 16;
    const spaceAbove = ar.top - sr.top;
    const spaceBelow = sr.bottom - ar.bottom;
    const above = spaceAbove >= need || spaceAbove >= spaceBelow;
    peek.classList.toggle("is-below", !above);
    peek.style.left = `${Math.max(160, Math.min(sr.width - 160, ar.left - sr.left + ar.width / 2))}px`;
    peek.style.top = `${above ? ar.top - sr.top : ar.bottom - sr.top}px`;
  }
  function togglePeek(anchorEl) {
    if (peekFor === anchorEl) { hidePeek(); return; }
    const full = text(anchorEl.dataset.full);
    const node = anchorEl.classList.contains("schema-node") ? anchorEl : null;
    const files = node ? node.querySelector(".schema-node-files") : null;
    const caption = node ? node.querySelector(".schema-node-caption") : null;
    const hint = text(node && node.dataset.hint);
    const clipped = anchorEl.classList.contains("is-truncated") || (caption && caption.classList.contains("is-truncated"));
    if (!clipped && (!files || files.hidden) && !hint) { hidePeek(); return; }
    if (!full && (!files || files.hidden) && !hint) { hidePeek(); return; }
    const peek = ensurePeek();
    peek.innerHTML = "";
    peek.classList.add("is-pinned");
    peek.classList.remove("is-below");
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
    body.textContent = full || hint;
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
    peek.style.visibility = "hidden";
    peek.hidden = false;
    placePeek(peek, anchorEl);
    peek.style.animation = "none";
    peek.getBoundingClientRect();
    peek.style.animation = "";
    peek.style.visibility = "";
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
    }, 4000);
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
  const relayout = () => {
    if (currentFrame && !root.hidden) {
      lastKey = "";
      requestAnimationFrame(() => {
        paintNodes(currentFrame);
        paintEdges(currentFrame);
        markOverflow();
      });
    }
  };
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 60); });
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(relayout, 40); }).observe(stage);
  new MutationObserver(() => { if (root.hidden) { hidePeek(); stopPlay(); } else relayout(); }).observe(root, { attributes: true, attributeFilter: ["hidden"] });

  window.MasSchema = { setFeed, reset, setAgents, buildFrames: buildSchemaFrames, showIndex, relayout, START_LABEL, END_LABEL };

  reset();
})();
