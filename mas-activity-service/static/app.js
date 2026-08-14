(() => {
  const thread = document.getElementById("thread");
  const empty = document.getElementById("empty");
  const title = document.getElementById("title");
  const subtitle = document.getElementById("subtitle");
  const taskInput = document.getElementById("taskInput");
  const openBtn = document.getElementById("openBtn");
  const demoBtn = document.getElementById("demoBtn");
  const taskRail = document.getElementById("taskRail");
  const liveDot = document.getElementById("liveDot");
  const liveLabel = document.getElementById("liveLabel");

  const ACTIVITY_KEY = localStorage.getItem("mas_activity_key") || "dev-local";
  let currentTask = null;
  let source = null;
  let rendered = new Set();

  function pathTaskId() {
    const m = location.pathname.match(/^\/t\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function setLive(state) {
    liveDot.hidden = state !== "live";
    liveLabel.textContent = state;
  }

  function roleClass(role) {
    return /orchestrator/i.test(role || "") ? "orch" : "spec";
  }

  function renderTurn(turn, { animate = true } = {}) {
    const id = `${turn.turn_id}:${turn.at}:${turn.status}:${turn.duration_ms || ""}`;
    if (rendered.has(id)) return;
    rendered.add(id);
    empty.hidden = true;

    const li = document.createElement("li");
    li.className = `turn outcome-${turn.outcome || "info"}`;
    if (!animate) {
      li.style.animation = "none";
      li.style.opacity = "1";
    }

    const fromRole = turn.from?.role || "Orchestrator";
    const toRole = turn.to?.role || "Specialist";
    const who = document.createElement("div");
    who.className = `who ${roleClass(fromRole)}`;
    who.innerHTML = `<span class="role">${escapeHtml(fromRole)}</span><span class="arrow">→</span><span class="role">${escapeHtml(toRole)}</span>`;

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const top = document.createElement("div");
    top.className = "turn-top";
    const status = document.createElement("div");
    status.className = "status";
    status.textContent = turn.status || turn.kind || "handoff";
    top.append(status);
    if (turn.duration_label) {
      const dur = document.createElement("span");
      dur.className = "duration";
      dur.textContent = turn.duration_label;
      dur.title = "Время работы specialist до этого handoff";
      top.append(dur);
    }
    bubble.append(top);

    const brief = document.createElement("p");
    brief.className = "brief";
    brief.textContent = turn.brief || turn.text || "";
    bubble.append(brief);

    if (turn.text && turn.brief && turn.text !== turn.brief) {
      const text = document.createElement("p");
      text.className = "text muted-line";
      text.textContent = turn.text;
      bubble.append(text);
    }

    if (Array.isArray(turn.chips) && turn.chips.length) {
      const chips = document.createElement("div");
      chips.className = "chips";
      for (const chip of turn.chips) {
        const el = document.createElement("span");
        el.className = "chip";
        el.innerHTML = `<b>${escapeHtml(chip.label)}</b> ${escapeHtml(String(chip.value))}`;
        chips.append(el);
      }
      bubble.append(chips);
    }

    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = [turn.at_abs || turn.at, turn.stage].filter(Boolean).join(" · ");
    bubble.append(meta);

    li.append(who, bubble);
    thread.append(li);
    thread.scrollTop = thread.scrollHeight;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function refreshRail() {
    const res = await fetch("/v1/tasks");
    const data = await res.json();
    taskRail.innerHTML = "";
    for (const task of data.tasks || []) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = task.task_id === currentTask ? "active" : "";
      btn.innerHTML = `<span class="id">${escapeHtml(task.task_id)}</span><span class="meta">${task.turn_count} turns · ${escapeHtml(task.last_at_abs || task.updated_at || "")}</span>`;
      btn.addEventListener("click", () => openTask(task.task_id));
      taskRail.append(btn);
    }
  }

  function closeStream() {
    if (source) {
      source.close();
      source = null;
    }
  }

  async function openTask(taskId) {
    if (!taskId) return;
    currentTask = taskId;
    taskInput.value = taskId;
    history.replaceState({}, "", `/t/${encodeURIComponent(taskId)}`);
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = false;
    title.textContent = taskId;
    subtitle.textContent = "Краткий ход работы: кто → кому, абсолютное время и длительность specialist.";
    closeStream();
    setLive("connecting");

    const snap = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}`);
    if (snap.ok) {
      const data = await snap.json();
      title.textContent = data.title || taskId;
      for (const turn of data.activity || []) renderTurn(turn, { animate: false });
    }

    source = new EventSource(`/v1/tasks/${encodeURIComponent(taskId)}/stream`);
    source.onopen = () => setLive("live");
    source.onerror = () => setLive("reconnecting");
    source.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") {
          thread.innerHTML = "";
          rendered = new Set();
          empty.hidden = !(msg.activity || []).length;
          for (const turn of msg.activity || []) renderTurn(turn, { animate: false });
        } else if (msg.type === "turn") {
          renderTurn(msg.turn, { animate: true });
        }
      } catch (_) { /* ignore malformed SSE */ }
    };
    await refreshRail();
  }

  async function seedDemo() {
    const res = await fetch("/v1/demo/seed", {
      method: "POST",
      headers: { "X-Activity-Key": ACTIVITY_KEY },
    });
    if (!res.ok) {
      alert("Demo seed failed. Check MAS_ACTIVITY_KEY (default dev-local).");
      return;
    }
    const data = await res.json();
    await openTask(data.task_id);
  }

  openBtn.addEventListener("click", () => openTask(taskInput.value.trim()));
  demoBtn.addEventListener("click", seedDemo);
  taskInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") openTask(taskInput.value.trim());
  });

  const initial = pathTaskId();
  if (initial) openTask(initial);
  else refreshRail();
})();
