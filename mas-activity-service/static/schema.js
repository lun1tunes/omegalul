(() => {
  const START_LABEL = "Постановка задачи";
  const END_LABEL = "Результат";
  const FINISHED_RESULT_TEXT = "Задача завершена. Загрузите результаты работы.";
  const NODE_KEYS = ["input", "orchestrator", "excel", "calc", "schedule", "user", "output"];
  const EDGE_KEYS = [
    "in_orch", "orch_out",
    "orch_excel", "orch_calc", "orch_schedule",
    "excel_orch", "calc_orch", "schedule_orch",
    "orch_user", "user_orch",
  ];
  /** Physical SVG paths: one line per orch↔agent / orch↔HITL pair; logical return edges stay in EDGE_KEYS. */
  const DRAW_EDGE_KEYS = [
    "in_orch", "orch_out",
    "orch_excel", "orch_calc", "orch_schedule",
    "orch_user",
  ];
  const PAIR_BACK = {
    orch_excel: "excel_orch",
    orch_calc: "calc_orch",
    orch_schedule: "schedule_orch",
    orch_user: "user_orch",
  };
  const AGENT_NODES = {
    excel_extractor: "excel",
    calculation_agent: "calc",
    schedule_builder: "schedule",
  };
  const OUTBOUND = { excel: "orch_excel", calc: "orch_calc", schedule: "orch_schedule" };
  const RETURN_EDGE = { excel: "excel_orch", calc: "calc_orch", schedule: "schedule_orch" };
  const NODE_META = {
    input: { kicker: "вход", title: START_LABEL },
    orchestrator: { kicker: "оркестратор", title: "Оркестратор" },
    excel: { kicker: "агент", title: "Excel" },
    calc: { kicker: "агент", title: "Расчёт" },
    schedule: { kicker: "агент", title: "Schedule" },
    user: { kicker: "HITL", title: "Вы" },
    output: { kicker: "выход", title: END_LABEL },
  };
  const LAYOUT = {
    input: { x: 3.2, y: 28, w: 21, h: 28 },
    orchestrator: { x: 38, y: 24, w: 24, h: 36 },
    output: { x: 75.8, y: 28, w: 21, h: 28 },
    excel: { x: 15, y: 68, w: 19, h: 24 },
    calc: { x: 40.5, y: 68, w: 19, h: 24 },
    schedule: { x: 66, y: 68, w: 19, h: 24 },
    user: { x: 38, y: 3.5, w: 24, h: 18 },
  };
  const EDGE_ENDS = {
    in_orch: ["input", "right", "orchestrator", "left"],
    orch_out: ["orchestrator", "right", "output", "left"],
    orch_excel: ["orchestrator", "bottom", "excel", "top"],
    orch_calc: ["orchestrator", "bottom", "calc", "top"],
    orch_schedule: ["orchestrator", "bottom", "schedule", "top"],
    excel_orch: ["excel", "top", "orchestrator", "bottom"],
    calc_orch: ["calc", "top", "orchestrator", "bottom"],
    schedule_orch: ["schedule", "top", "orchestrator", "bottom"],
    orch_user: ["orchestrator", "top", "user", "bottom"],
    user_orch: ["user", "bottom", "orchestrator", "top"],
  };
  const KIND_LABELS = {
    "case.created": START_LABEL,
    "case.finished": END_LABEL,
    "case.failed": END_LABEL,
    "orchestrator.status": "Оркестратор",
    "orchestrator.decision": "Оркестратор",
    "agent.handoff": "Передача",
    "agent.accepted": "Агент принял задачу",
    "agent.progress": "Агент работает",
    "agent.result": "Агент вернул результат",
    "agent.failed": "Сбой агента",
    "hitl.request": "Запрос к вам",
    "hitl.answered": "Ваш ответ",
    "system.node_error": "Сбой узла",
  };
  const TONE_STATUS = {
    idle: "Ожидает",
    pending: "Передан",
    active: "В работе",
    waiting: "Ждёт вас",
    done: "Готово",
    error: "Ошибка",
  };

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
  const countEl = document.getElementById("schemaStepCount");

  if (startLabelEl) startLabelEl.textContent = START_LABEL;
  if (endLabelEl) endLabelEl.textContent = END_LABEL;

  let frames = [];
  let index = 0;
  let followLive = true;
  let complete = false;
  let built = false;
  let resizeTimer = 0;
  let lastSlipsKey = "";
  let lastPaintIndex = -1;
  let scheduleDownload = null;
  let peekEl = null;
  let peekTimer = 0;
  let peekSource = null;
  let currentFrame = null;

  function text(value) {
    return String(value || "").trim();
  }

  function statusLabel(id, spec) {
    const tone = spec?.tone || "idle";
    if (tone === "idle" && id === "user") return "Не запрашивали";
    if (tone === "idle" && (id === "excel" || id === "calc" || id === "schedule")) return "Не вызывался";
    if (tone === "idle" && id === "orchestrator") return "Ожидает задачу";
    return TONE_STATUS[tone] || tone;
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function filesFromState(state) {
    const artifacts = state && typeof state.artifacts === "object" ? state.artifacts : {};
    const names = [];
    const seen = new Set();
    const skip = new Set(["schedule_out", "diff"]);
    for (const [key, item] of Object.entries(artifacts)) {
      if (skip.has(key)) continue;
      let name = "";
      if (item && typeof item === "object") name = text(item.filename || item.artifact_id || key);
      else if (item) name = text(key);
      if (name && !seen.has(name)) {
        seen.add(name);
        names.push(name);
      }
    }
    return names;
  }

  function humanStatus(message) {
    const msg = text(message);
    if (!msg || /^(case\.finished|case\.failed)$/i.test(msg)) return "";
    return msg;
  }

  function resultText(payload, statusMessage) {
    const data = payload && typeof payload === "object" ? payload : {};
    const result = data.result;
    if (typeof result === "string" && result.trim()) return result.trim();
    if (result && typeof result === "object") {
      for (const key of ["summary", "message", "text", "status_message"]) {
        if (text(result[key])) return text(result[key]);
      }
      try {
        return JSON.stringify(result).slice(0, 400);
      } catch (_) {
        return statusMessage;
      }
    }
    for (const key of ["message", "summary", "text"]) {
      if (text(data[key])) return text(data[key]);
    }
    return statusMessage;
  }

  function blankGraph(state) {
    const nodes = {};
    const edges = {};
    for (const key of NODE_KEYS) nodes[key] = { tone: "idle", bubble: null, caption: "" };
    for (const key of EDGE_KEYS) edges[key] = { tone: "idle", bubble: null };
    return {
      nodes,
      edges,
      input: { goal: text(state && state.goal), files: filesFromState(state || {}) },
      output: { result: "", prompt: "" },
      active_node: null,
      active_edge: null,
      in_flight: null,
      last_handoff: {},
      last_orch_prompt: "",
    };
  }

  function setCaption(graph, nodeId, value) {
    const caption = text(value);
    if (caption) graph.nodes[nodeId].caption = caption;
  }

  function clearNodeBubbles(graph, keep) {
    for (const [nodeId, node] of Object.entries(graph.nodes)) {
      if (nodeId !== keep) node.bubble = null;
    }
  }

  function activateNode(graph, nodeId, bubble) {
    for (const [nid, node] of Object.entries(graph.nodes)) {
      if (nid === nodeId) continue;
      if (node.tone === "active") node.tone = "done";
      node.bubble = null;
    }
    graph.nodes[nodeId].tone = "active";
    graph.nodes[nodeId].bubble = bubble || null;
    setCaption(graph, nodeId, bubble);
    graph.active_node = nodeId;
  }

  function markDone(graph, nodeId) {
    const node = graph.nodes[nodeId];
    if (node.tone !== "error") node.tone = "done";
    node.bubble = null;
    if (graph.active_node === nodeId) graph.active_node = null;
  }

  function setEdge(graph, edgeId, tone, bubble) {
    for (const [eid, edge] of Object.entries(graph.edges)) {
      if (eid === edgeId) continue;
      if (edge.tone === "active" && tone === "active") {
        edge.tone = "done";
        edge.bubble = null;
      }
    }
    graph.edges[edgeId].tone = tone;
    graph.edges[edgeId].bubble = tone === "active" ? (bubble || null) : null;
    graph.active_edge = tone === "active" ? edgeId : (graph.active_edge === edgeId ? null : graph.active_edge);
  }

  function agentNode(event) {
    return AGENT_NODES[text(event.agent_id)] || AGENT_NODES[text(event.actor)] || null;
  }

  function frameLabel(event) {
    const kind = text(event.kind);
    if (kind === "case.created") return START_LABEL;
    if (kind === "case.finished" || kind === "case.failed") return END_LABEL;
    if (kind === "agent.handoff" && text(event.handoff_message)) return text(event.handoff_message);
    if (text(event.status_message)) return text(event.status_message);
    return KIND_LABELS[kind] || kind || "Шаг";
  }

  function snapshot(graph, event, idx) {
    return {
      index: idx,
      label: frameLabel(event),
      kind: text(event.kind),
      event_id: event.event_id,
      nodes: copy(graph.nodes),
      edges: copy(graph.edges),
      input: copy(graph.input),
      output: copy(graph.output),
      active_node: graph.active_node,
      active_edge: graph.active_edge,
    };
  }

  function applyEvent(graph, event) {
    const kind = text(event.kind);
    const statusMessage = text(event.status_message);
    const handoff = text(event.handoff_message);
    const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
    const node = agentNode(event);

    if (kind === "case.created") {
      if (Array.isArray(payload.files) && payload.files.length) {
        graph.input.files = payload.files.map((name) => String(name)).filter(Boolean);
      }
      if (!graph.input.goal && statusMessage) graph.input.goal = statusMessage;
      activateNode(graph, "input", null);
      setEdge(graph, "in_orch", "active");
      return;
    }
    if (kind === "orchestrator.status" || kind === "orchestrator.decision") {
      if (graph.nodes.input.tone === "active") markDone(graph, "input");
      if (graph.edges.in_orch.tone === "active") setEdge(graph, "in_orch", "done");
      const flying = graph.in_flight;
      if (flying && graph.edges[RETURN_EDGE[flying]]?.tone === "active") {
        setEdge(graph, RETURN_EDGE[flying], "done");
      }
      graph.last_orch_prompt = statusMessage || graph.last_orch_prompt || "";
      activateNode(graph, "orchestrator", statusMessage || null);
      return;
    }
    if (kind === "agent.handoff" && node) {
      graph.in_flight = node;
      graph.last_handoff[node] = handoff;
      graph.nodes[node].tone = "pending";
      setEdge(graph, OUTBOUND[node], "active", handoff || null);
      if (graph.nodes.orchestrator.tone !== "error") {
        graph.nodes.orchestrator.tone = "active";
        if (statusMessage) {
          graph.nodes.orchestrator.bubble = statusMessage;
          setCaption(graph, "orchestrator", statusMessage);
          graph.active_node = "orchestrator";
          clearNodeBubbles(graph, "orchestrator");
        }
      }
      return;
    }
    if ((kind === "agent.accepted" || kind === "agent.progress") && node) {
      graph.in_flight = node;
      const outbound = OUTBOUND[node];
      const kept = handoff || graph.last_handoff[node] || graph.edges[outbound].bubble;
      activateNode(graph, node, statusMessage || null);
      setEdge(graph, outbound, "active", kept);
      return;
    }
    if (kind === "agent.result" && node) {
      setCaption(graph, node, statusMessage);
      markDone(graph, node);
      setEdge(graph, OUTBOUND[node], "done");
      setEdge(graph, RETURN_EDGE[node], "active");
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
      clearNodeBubbles(graph, node);
      setEdge(graph, OUTBOUND[node], "done");
      setEdge(graph, RETURN_EDGE[node], "error");
      graph.in_flight = null;
      return;
    }
    if (kind === "hitl.request") {
      const question = statusMessage || text(payload.question);
      activateNode(graph, "user", question || null);
      graph.nodes.orchestrator.tone = "waiting";
      graph.nodes.orchestrator.bubble = null;
      setEdge(graph, "orch_user", "active", question || null);
      return;
    }
    if (kind === "hitl.answered") {
      markDone(graph, "user");
      setEdge(graph, "orch_user", "done");
      setEdge(graph, "user_orch", "active");
      activateNode(graph, "orchestrator", statusMessage || null);
      return;
    }
    if (kind === "case.finished") {
      for (const [nodeId, item] of Object.entries(graph.nodes)) {
        if (nodeId === "output") continue;
        if (item.tone === "active" || item.tone === "pending" || item.tone === "waiting") item.tone = "done";
        item.bubble = null;
      }
      setCaption(graph, "orchestrator", humanStatus(statusMessage) || graph.last_orch_prompt);
      for (const edge of Object.values(graph.edges)) {
        if (edge.tone === "active") edge.tone = "done";
        edge.bubble = null;
      }
      graph.output.prompt = "";
      graph.output.result = FINISHED_RESULT_TEXT;
      activateNode(graph, "output", null);
      setEdge(graph, "orch_out", "active");
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
      graph.nodes.output.bubble = null;
      clearNodeBubbles(graph, "orchestrator");
      setEdge(graph, "orch_out", "error");
      graph.active_node = "orchestrator";
      graph.in_flight = null;
      return;
    }
    if (kind === "system.node_error") {
      graph.nodes.orchestrator.tone = "error";
      graph.nodes.orchestrator.bubble = statusMessage || null;
      setCaption(graph, "orchestrator", statusMessage);
      graph.active_node = "orchestrator";
      clearNodeBubbles(graph, "orchestrator");
    }
  }

  function buildSchemaFrames(events, state) {
    const graph = blankGraph(state || {});
    const rows = Array.isArray(events) ? events.filter((row) => row && typeof row === "object") : [];
    if (!rows.length) {
      activateNode(graph, "input", null);
      return [{
        index: 0,
        label: START_LABEL,
        kind: "case.created",
        event_id: null,
        nodes: copy(graph.nodes),
        edges: copy(graph.edges),
        input: copy(graph.input),
        output: copy(graph.output),
        active_node: "input",
        active_edge: null,
      }];
    }
    return rows.map((event, idx) => {
      applyEvent(graph, event);
      return snapshot(graph, event, idx);
    });
  }

  function eventsFromFeed(data) {
    if (Array.isArray(data?.events) && data.events.length) return data.events;
    const turns = Array.isArray(data?.activity) ? data.activity : [];
    return turns.map((turn) => {
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

  function shorten(value, max) {
    const raw = text(value);
    if (!raw) return "";
    if (raw.length <= max) return raw;
    return `${raw.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
  }

  function point(box, side) {
    if (side === "left") return { x: box.x, y: box.y + box.h * 0.5 };
    if (side === "right") return { x: box.x + box.w, y: box.y + box.h * 0.5 };
    if (side === "top") return { x: box.x + box.w * 0.5, y: box.y };
    return { x: box.x + box.w * 0.5, y: box.y + box.h };
  }

  function isLiveTone(tone) {
    return tone === "active" || tone === "error";
  }

  function toneRank(tone) {
    if (isLiveTone(tone)) return 2;
    if (tone === "done" || tone === "pending" || tone === "waiting") return 1;
    return 0;
  }

  function pairVisual(outId, edges, activeEdge) {
    const out = (edges && edges[outId]) || { tone: "idle", bubble: null };
    const backId = PAIR_BACK[outId];
    if (!backId) {
      return {
        tone: out.tone || "idle",
        dir: isLiveTone(out.tone) ? "out" : "none",
        bubble: out.bubble || null,
      };
    }
    const back = (edges && edges[backId]) || { tone: "idle", bubble: null };
    const outLive = isLiveTone(out.tone);
    const backLive = isLiveTone(back.tone);
    if (outLive && backLive) {
      if (activeEdge === backId) {
        return { tone: back.tone, dir: "back", bubble: back.bubble || out.bubble || null };
      }
      return { tone: out.tone, dir: "out", bubble: out.bubble || back.bubble || null };
    }
    if (backLive) {
      return { tone: back.tone, dir: "back", bubble: back.bubble || out.bubble || null };
    }
    if (outLive) {
      return { tone: out.tone, dir: "out", bubble: out.bubble || back.bubble || null };
    }
    if (toneRank(back.tone) > toneRank(out.tone)) {
      return { tone: back.tone || "idle", dir: "none", bubble: back.bubble || out.bubble || null };
    }
    return { tone: out.tone || "idle", dir: "none", bubble: out.bubble || back.bubble || null };
  }

  function cubicPoint(p0, c1, c2, p1, t) {
    const u = 1 - t;
    const uu = u * u;
    const tt = t * t;
    return {
      x: uu * u * p0.x + 3 * uu * t * c1.x + 3 * u * tt * c2.x + tt * t * p1.x,
      y: uu * u * p0.y + 3 * uu * t * c1.y + 3 * u * tt * c2.y + tt * t * p1.y,
    };
  }

  function cubicArcMid(p0, c1, c2, p1) {
    const steps = 32;
    const pts = [{ p: p0, len: 0 }];
    let length = 0;
    let prev = p0;
    for (let i = 1; i <= steps; i++) {
      const p = cubicPoint(p0, c1, c2, p1, i / steps);
      length += Math.hypot(p.x - prev.x, p.y - prev.y);
      pts.push({ p, len: length });
      prev = p;
    }
    const half = length / 2;
    for (let i = 1; i < pts.length; i++) {
      if (pts[i].len >= half) {
        const span = pts[i].len - pts[i - 1].len || 1;
        const k = (half - pts[i - 1].len) / span;
        return {
          x: pts[i - 1].p.x + (pts[i].p.x - pts[i - 1].p.x) * k,
          y: pts[i - 1].p.y + (pts[i].p.y - pts[i - 1].p.y) * k,
        };
      }
    }
    return cubicPoint(p0, c1, c2, p1, 0.5);
  }

  function measuredLayout() {
    if (!stage || !nodesEl) return LAYOUT;
    const sr = stage.getBoundingClientRect();
    if (!sr.width || !sr.height) return LAYOUT;
    const map = {};
    for (const id of NODE_KEYS) {
      const el = nodesEl.querySelector(`[data-node="${id}"]`);
      if (!el) {
        map[id] = LAYOUT[id];
        continue;
      }
      const br = el.getBoundingClientRect();
      map[id] = {
        x: ((br.left - sr.left) / sr.width) * 100,
        y: ((br.top - sr.top) / sr.height) * 100,
        w: (br.width / sr.width) * 100,
        h: (br.height / sr.height) * 100,
      };
    }
    return map;
  }

  function edgePath(id, layout) {
    const spec = EDGE_ENDS[id];
    const boxes = layout || LAYOUT;
    const a = boxes[spec[0]] || LAYOUT[spec[0]];
    const b = boxes[spec[2]] || LAYOUT[spec[2]];
    const p1 = point(a, spec[1]);
    const p2 = point(b, spec[3]);
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const c1 = { x: p1.x + dx * 0.38, y: p1.y + dy * 0.12 };
    const c2 = { x: p1.x + dx * 0.62, y: p1.y + dy * 0.88 };
    if (spec[1] === "right" || spec[1] === "left") {
      c1.x = p1.x + (spec[1] === "right" ? 8 : -8);
      c1.y = p1.y;
      c2.x = p2.x + (spec[3] === "left" ? -8 : 8);
      c2.y = p2.y;
    }
    return {
      d: `M ${p1.x} ${p1.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${p2.x} ${p2.y}`,
      dRev: `M ${p2.x} ${p2.y} C ${c2.x} ${c2.y}, ${c1.x} ${c1.y}, ${p1.x} ${p1.y}`,
      mid: cubicArcMid(p1, c1, c2, p2),
    };
  }

  function pathMidpoint(path, fallback) {
    try {
      if (!path || typeof path.getTotalLength !== "function") return fallback;
      const len = path.getTotalLength();
      if (!Number.isFinite(len) || len <= 0) return fallback;
      const stageRect = stage && stage.getBoundingClientRect();
      const ctm = typeof path.getScreenCTM === "function" ? path.getScreenCTM() : null;
      if (!stageRect || !stageRect.width || !stageRect.height || !ctm) {
        const p = path.getPointAtLength(len / 2);
        if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return { x: p.x, y: p.y };
        return fallback;
      }
      const steps = 64;
      const samples = [];
      let acc = 0;
      let prevX = 0;
      let prevY = 0;
      for (let i = 0; i <= steps; i++) {
        const p = path.getPointAtLength((len * i) / steps);
        const sx = ctm.a * p.x + ctm.c * p.y + ctm.e;
        const sy = ctm.b * p.x + ctm.d * p.y + ctm.f;
        if (i) acc += Math.hypot(sx - prevX, sy - prevY);
        samples.push({ sx, sy, acc });
        prevX = sx;
        prevY = sy;
      }
      const half = acc / 2;
      let sx = samples[samples.length - 1].sx;
      let sy = samples[samples.length - 1].sy;
      for (let i = 1; i < samples.length; i++) {
        if (samples[i].acc >= half) {
          const span = samples[i].acc - samples[i - 1].acc || 1;
          const k = (half - samples[i - 1].acc) / span;
          sx = samples[i - 1].sx + (samples[i].sx - samples[i - 1].sx) * k;
          sy = samples[i - 1].sy + (samples[i].sy - samples[i - 1].sy) * k;
          break;
        }
      }
      return {
        x: ((sx - stageRect.left) / stageRect.width) * 100,
        y: ((sy - stageRect.top) / stageRect.height) * 100,
      };
    } catch (_) { /* keep fallback */ }
    return fallback;
  }

  function ensureDom() {
    if (!stage || !nodesEl || !edgesEl || built) return;
    edgesEl.setAttribute("viewBox", "0 0 100 100");
    edgesEl.setAttribute("preserveAspectRatio", "none");
    edgesEl.innerHTML = `
      <defs>
        <marker id="schemaArrowActive" viewBox="0 0 10 10" refX="8.4" refY="5" markerWidth="2.3" markerHeight="2.3" markerUnits="userSpaceOnUse" orient="auto" overflow="visible">
          <path d="M 0 0.8 L 10 5 L 0 9.2 Z" fill="#00B8F0"></path>
        </marker>
        <marker id="schemaArrowDone" viewBox="0 0 10 10" refX="8.4" refY="5" markerWidth="2.3" markerHeight="2.3" markerUnits="userSpaceOnUse" orient="auto" overflow="visible">
          <path d="M 0 0.8 L 10 5 L 0 9.2 Z" fill="#0033A0"></path>
        </marker>
        <marker id="schemaArrowError" viewBox="0 0 10 10" refX="8.4" refY="5" markerWidth="2.3" markerHeight="2.3" markerUnits="userSpaceOnUse" orient="auto" overflow="visible">
          <path d="M 0 0.8 L 10 5 L 0 9.2 Z" fill="#F90D4B"></path>
        </marker>
      </defs>
    `;
    for (const id of DRAW_EDGE_KEYS) {
      const geo = edgePath(id);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", geo.d);
      path.setAttribute("data-edge", id);
      path.setAttribute("fill", "none");
      path.setAttribute("class", "schema-edge-path");
      edgesEl.appendChild(path);
    }
    nodesEl.innerHTML = "";
    for (const id of NODE_KEYS) {
      const meta = NODE_META[id];
      const node = document.createElement("article");
      node.className = "schema-node";
      node.dataset.node = id;
      const head = document.createElement("div");
      head.className = "schema-node-head";
      const kicker = document.createElement("span");
      kicker.className = "schema-kicker";
      kicker.textContent = meta.kicker;
      const title = document.createElement("h2");
      title.textContent = meta.title;
      title.title = meta.title;
      head.append(kicker, title);
      const body = document.createElement("div");
      body.className = "schema-node-body";
      node.append(head, body);
      node.addEventListener("pointerenter", (ev) => {
        if (ev.pointerType === "touch") return;
        clearTimeout(peekTimer);
        showPeek(node);
      });
      node.addEventListener("pointerleave", hidePeekSoon);
      nodesEl.appendChild(node);
    }
    nodesEl.addEventListener("click", (ev) => {
      const node = ev.target.closest(".schema-node");
      if (!node || ev.target.closest("a")) return;
      if (!node.classList.contains("is-clipped")) return;
      if (peekSource === node && peekEl && !peekEl.hidden) hidePeek();
      else showPeek(node);
    });
    nodesEl.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const node = ev.target.closest(".schema-node");
      if (!node || ev.target !== node) return;
      if (!node.classList.contains("is-clipped")) return;
      ev.preventDefault();
      if (peekSource === node && peekEl && !peekEl.hidden) hidePeek();
      else showPeek(node);
    });
    built = true;
    bindPeekChrome();
  }

  function overflowing(el) {
    if (!el) return false;
    return el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
  }

  function markClippedNodes() {
    if (!nodesEl || !root || root.hidden) return;
    for (const node of nodesEl.querySelectorAll(".schema-node")) {
      const body = node.querySelector(".schema-node-body");
      let clipped = overflowing(body);
      if (!clipped && body) {
        for (const el of body.querySelectorAll(".schema-goal, .schema-prompt, .schema-caption")) {
          if (overflowing(el)) {
            clipped = true;
            break;
          }
        }
      }
      node.classList.toggle("is-clipped", clipped);
      if (clipped) {
        node.setAttribute("aria-haspopup", "true");
        node.tabIndex = 0;
        node.title = "Нажмите, чтобы показать полный текст";
      } else {
        node.removeAttribute("aria-haspopup");
        node.removeAttribute("tabindex");
        node.removeAttribute("title");
      }
    }
  }

  function ensurePeek() {
    if (peekEl) return peekEl;
    peekEl = document.createElement("div");
    peekEl.id = "schemaPeek";
    peekEl.className = "schema-peek";
    peekEl.hidden = true;
    peekEl.setAttribute("role", "tooltip");
    peekEl.addEventListener("pointerenter", () => clearTimeout(peekTimer));
    peekEl.addEventListener("pointerleave", hidePeekSoon);
    document.body.appendChild(peekEl);
    return peekEl;
  }

  function hidePeek() {
    clearTimeout(peekTimer);
    if (peekSource) {
      peekSource.classList.remove("is-peeking");
      peekSource.removeAttribute("aria-expanded");
    }
    peekSource = null;
    if (peekEl) {
      peekEl.hidden = true;
      peekEl.innerHTML = "";
    }
  }

  function hidePeekSoon() {
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => {
      if (peekEl && !peekEl.hidden && peekEl.matches(":hover")) return;
      if (peekSource && peekSource.matches(":hover")) return;
      hidePeek();
    }, 140);
  }

  function placePeek(node) {
    const peek = ensurePeek();
    const gap = 10;
    const box = node.getBoundingClientRect();
    peek.style.left = "0px";
    peek.style.top = "0px";
    const width = peek.offsetWidth;
    const height = peek.offsetHeight;
    let left = box.right + gap;
    let top = box.top;
    if (left + width > window.innerWidth - gap) left = box.left - width - gap;
    if (left < gap) left = Math.max(gap, (window.innerWidth - width) / 2);
    if (top + height > window.innerHeight - gap) top = window.innerHeight - height - gap;
    if (top < gap) top = gap;
    peek.style.left = `${Math.round(left)}px`;
    peek.style.top = `${Math.round(top)}px`;
  }

  function showPeek(node) {
    if (!node || !node.classList.contains("is-clipped")) return;
    const body = node.querySelector(".schema-node-body");
    if (!body) return;
    if (peekSource === node && peekEl && !peekEl.hidden) {
      placePeek(node);
      return;
    }
    const peek = ensurePeek();
    peek.innerHTML = "";
    const kicker = node.querySelector(".schema-kicker");
    const title = node.querySelector("h2");
    if (kicker) peek.append(kicker.cloneNode(true));
    if (title) peek.append(title.cloneNode(true));
    const clone = body.cloneNode(true);
    clone.className = "schema-peek-body";
    peek.append(clone);
    if (peekSource && peekSource !== node) {
      peekSource.classList.remove("is-peeking");
      peekSource.removeAttribute("aria-expanded");
    }
    peek.hidden = false;
    peekSource = node;
    node.classList.add("is-peeking");
    node.setAttribute("aria-expanded", "true");
    placePeek(node);
  }

  function bindPeekChrome() {
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") hidePeek();
    });
    document.addEventListener("pointerdown", (ev) => {
      if (!peekEl || peekEl.hidden) return;
      const target = ev.target;
      if (peekEl.contains(target) || (peekSource && peekSource.contains(target))) return;
      hidePeek();
    });
    if (root) {
      new MutationObserver(() => {
        if (root.hidden) hidePeek();
        else if (currentFrame) {
          requestAnimationFrame(() => layoutDiagram(currentFrame, { replaceSlips: true }));
        }
      }).observe(root, { attributes: true, attributeFilter: ["hidden"] });
    }
  }

  function bubbleEl(kind, textValue, x, y, from, animate) {
    const el = document.createElement("div");
    el.className = `schema-slip schema-slip-${kind} schema-slip-from-${from || "south"}`;
    if (animate) el.classList.add("is-live-in");
    el.style.left = `${x}%`;
    el.style.top = `${y}%`;
    el.title = textValue;
    el.textContent = shorten(textValue, 72);
    return el;
  }

  function slipsKey(frame) {
    if (!frame) return "";
    const nodes = NODE_KEYS.map((id) => {
      const spec = frame.nodes?.[id] || {};
      return `${id}:${spec.tone || ""}:${spec.bubble || ""}:${spec.caption || ""}`;
    });
    const edges = EDGE_KEYS.map((id) => {
      const spec = frame.edges?.[id] || {};
      return `${id}:${spec.tone || ""}:${spec.bubble || ""}`;
    });
    const files = Array.isArray(frame.input?.files) ? frame.input.files.join(",") : "";
    return [
      frame.label || "",
      frame.input?.goal || "",
      files,
      frame.output?.result || "",
      frame.output?.prompt || "",
      scheduleDownload?.download_path || "",
      complete ? "1" : "0",
      ...nodes,
      ...edges,
    ].join("\n");
  }

  function paintChrome(frame) {
    if (stepEl) {
      const label = frame?.label || "";
      stepEl.textContent = label;
      stepEl.title = label;
    }
    if (countEl) {
      countEl.textContent = frames.length ? `шаг ${index + 1} / ${frames.length}` : "шаг 0 / 0";
    }
    if (rangeEl) {
      rangeEl.max = String(Math.max(frames.length - 1, 0));
      rangeEl.value = String(index);
      rangeEl.disabled = frames.length < 2;
      const max = Math.max(frames.length - 1, 1);
      const pct = frames.length < 2 ? 0 : (index / max) * 100;
      rangeEl.style.setProperty("--schema-progress", `${pct}%`);
    }
    if (prevBtn) prevBtn.disabled = index <= 0;
    if (nextBtn) nextBtn.disabled = index >= frames.length - 1;
    if (root) root.dataset.complete = complete ? "true" : "false";
    if (endLabelEl) endLabelEl.classList.toggle("is-muted", !complete);
  }

  function syncEdgeGeometry(frame, layout) {
    if (!frame || !edgesEl) return;
    const boxes = layout || measuredLayout();
    const marker = {
      active: "url(#schemaArrowActive)",
      error: "url(#schemaArrowError)",
      done: "url(#schemaArrowDone)",
      pending: "url(#schemaArrowDone)",
    };
    for (const id of DRAW_EDGE_KEYS) {
      const path = edgesEl.querySelector(`[data-edge="${id}"]`);
      if (!path) continue;
      const visual = pairVisual(id, frame.edges, frame.active_edge);
      const tone = visual.tone || "idle";
      const geo = edgePath(id, boxes);
      path.setAttribute("d", visual.dir === "back" ? geo.dRev : geo.d);
      path.setAttribute("data-dir", visual.dir);
      path.setAttribute("class", `schema-edge-path is-${tone}${visual.dir === "back" ? " is-back" : ""}`);
      if (marker[tone]) path.setAttribute("marker-end", marker[tone]);
      else path.removeAttribute("marker-end");
    }
  }

  function placeEdgeSlips(frame, layout, animateSlips) {
    if (!stage || !frame) return;
    stage.querySelectorAll(".schema-slip").forEach((el) => el.remove());
    const boxes = layout || measuredLayout();
    for (const id of DRAW_EDGE_KEYS) {
      const visual = pairVisual(id, frame.edges, frame.active_edge);
      if (!visual.bubble) continue;
      const path = edgesEl.querySelector(`[data-edge="${id}"]`);
      const geo = edgePath(id, boxes);
      const mid = pathMidpoint(path, geo.mid);
      stage.append(bubbleEl("edge", visual.bubble, mid.x, mid.y, "edge", animateSlips));
    }
  }

  function layoutDiagram(frame, { animateSlips = false, replaceSlips = true } = {}) {
    if (!frame || !stage || (root && root.hidden)) return;
    const layout = measuredLayout();
    syncEdgeGeometry(frame, layout);
    if (replaceSlips) placeEdgeSlips(frame, layout, animateSlips);
    markClippedNodes();
  }

  function renderFrame(frame, { animateSlips = false } = {}) {
    if (!root || !frame) return;
    ensureDom();
    currentFrame = frame;
    const key = slipsKey(frame);
    const contentChanged = key !== lastSlipsKey || index !== lastPaintIndex;
    if (!contentChanged) {
      paintChrome(frame);
      requestAnimationFrame(() => layoutDiagram(frame, { replaceSlips: true }));
      return;
    }
    lastSlipsKey = key;
    lastPaintIndex = index;
    for (const id of NODE_KEYS) {
      const node = nodesEl.querySelector(`[data-node="${id}"]`);
      if (!node) continue;
      const spec = frame.nodes[id] || { tone: "idle" };
      node.dataset.tone = spec.tone || "idle";
      const body = node.querySelector(".schema-node-body");
      if (!body) continue;
      body.innerHTML = "";
      if (id === "input") {
        const goal = document.createElement("p");
        goal.className = "schema-goal";
        const goalText = frame.input?.goal || "Нет описания задачи";
        goal.textContent = goalText;
        goal.title = goalText;
        body.append(goal);
        const files = Array.isArray(frame.input?.files) ? frame.input.files : [];
        if (files.length) {
          const list = document.createElement("ul");
          list.className = "schema-files";
          for (const name of files) {
            const li = document.createElement("li");
            li.textContent = name;
            li.title = name;
            list.append(li);
          }
          body.append(list);
        }
      } else if (id === "output") {
        const result = document.createElement("p");
        result.className = "schema-goal";
        const resultCopy = frame.output?.result || (complete ? "Нет текста результата" : "Результат появится после завершения");
        result.textContent = resultCopy;
        result.title = resultCopy;
        body.append(result);
        if (frame.output?.prompt) {
          const prompt = document.createElement("p");
          prompt.className = "schema-prompt";
          prompt.textContent = frame.output.prompt;
          prompt.title = frame.output.prompt;
          body.append(prompt);
        }
        const showDl = scheduleDownload && ["active", "done", "error"].includes(spec.tone);
        if (showDl) {
          const link = document.createElement("a");
          link.className = "schema-download schedule-download";
          link.href = scheduleDownload.download_path;
          link.setAttribute("download", scheduleDownload.filename || "schedule_result.inc");
          link.textContent = "Скачать результат";
          link.title = `Скачать результат SCHEDULE (${scheduleDownload.filename || "schedule_result.inc"})`;
          body.append(link);
        }
      } else {
        const status = document.createElement("span");
        status.className = "schema-status";
        status.textContent = statusLabel(id, spec);
        body.append(status);
        const caption = text(spec.caption || spec.bubble);
        if (caption) {
          const line = document.createElement("p");
          line.className = "schema-caption";
          line.textContent = caption;
          line.title = caption;
          body.append(line);
        }
      }
    }
    paintChrome(frame);
    if (contentChanged) hidePeek();
    requestAnimationFrame(() => {
      layoutDiagram(frame, { animateSlips: animateSlips && contentChanged, replaceSlips: true });
    });
  }

  function showIndex(next, { user = false } = {}) {
    if (!frames.length) return;
    index = Math.max(0, Math.min(frames.length - 1, next));
    if (user) followLive = index === frames.length - 1;
    renderFrame(frames[index]);
  }

  function setFeed(data) {
    const feed = data && typeof data === "object" ? data : {};
    const state = {
      ...(feed.state && typeof feed.state === "object" ? feed.state : {}),
    };
    if (feed.objective && !state.goal) state.goal = feed.objective;
    const attached = Array.isArray(feed.attached_files) ? feed.attached_files : [];
    if (attached.length && (!state.artifacts || !Object.keys(state.artifacts).length)) {
      state.artifacts = Object.fromEntries(attached.map((name, i) => [`file_${i}`, { filename: name }]));
    }
    const events = eventsFromFeed(feed);
    const art = feed.schedule_artifact;
    scheduleDownload = art && typeof art === "object" && art.available && art.download_path ? art : null;
    const prevIndex = index;
    const prevLen = frames.length;
    frames = buildSchemaFrames(events, state);
    complete = Boolean(feed.schema?.complete) || ["done", "failed"].includes(String(feed.status || "")) ||
      ["case.finished", "case.failed"].includes(frames[frames.length - 1]?.kind);
    if (followLive || index >= frames.length) index = Math.max(frames.length - 1, 0);
    index = Math.max(0, Math.min(frames.length - 1, index));
    const liveNewStep = followLive && (index > prevIndex || frames.length > prevLen);
    renderFrame(frames[index] || frames[0], { animateSlips: liveNewStep });
  }

  function reset() {
    frames = buildSchemaFrames([], {});
    index = 0;
    followLive = true;
    complete = false;
    scheduleDownload = null;
    lastSlipsKey = "";
    lastPaintIndex = -1;
    hidePeek();
    renderFrame(frames[0]);
  }

  if (rangeEl) {
    rangeEl.addEventListener("input", () => showIndex(Number(rangeEl.value), { user: true }));
  }
  if (prevBtn) prevBtn.addEventListener("click", () => showIndex(index - 1, { user: true }));
  if (nextBtn) nextBtn.addEventListener("click", () => showIndex(index + 1, { user: true }));
  if (root) {
    root.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        showIndex(index - 1, { user: true });
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        showIndex(index + 1, { user: true });
      }
    });
  }
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (currentFrame) layoutDiagram(currentFrame, { replaceSlips: true });
    }, 80);
  });
  if (typeof ResizeObserver !== "undefined" && stage) {
    const stageWatch = new ResizeObserver(() => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (root && !root.hidden && currentFrame) {
          layoutDiagram(currentFrame, { replaceSlips: true });
        }
      }, 40);
    });
    stageWatch.observe(stage);
  }

  window.MasSchema = {
    setFeed,
    reset,
    buildFrames: buildSchemaFrames,
    showIndex,
    relayout() {
      if (currentFrame) {
        requestAnimationFrame(() => layoutDiagram(currentFrame, { replaceSlips: true }));
      }
    },
    START_LABEL,
    END_LABEL,
  };

  if (root) reset();
})();
