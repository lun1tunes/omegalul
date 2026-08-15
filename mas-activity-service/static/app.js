(() => {
  const thread = document.getElementById("thread");
  const empty = document.getElementById("empty");
  const title = document.getElementById("title");
  const subtitle = document.getElementById("subtitle");
  const taskRail = document.getElementById("taskRail");
  const liveDot = document.getElementById("liveDot");
  const liveLabel = document.getElementById("liveLabel");
  const backendLabel = document.getElementById("backendLabel");
  const flashEl = document.getElementById("flash");
  const gatePanel = document.getElementById("gatePanel");
  const gateKind = document.getElementById("gateKind");
  const gateMeta = document.getElementById("gateMeta");
  const gateReason = document.getElementById("gateReason");
  const gateQuestions = document.getElementById("gateQuestions");
  const composer = document.getElementById("composer");
  const requestedBy = document.getElementById("requestedBy");
  const humanResponse = document.getElementById("humanResponse");
  const composerHint = document.getElementById("composerHint");
  const approveBtn = document.getElementById("approveBtn");
  const rejectBtn = document.getElementById("rejectBtn");
  const replyBtn = document.getElementById("replyBtn");
  const cancelBtn = document.getElementById("cancelBtn");
  const hitlButtons = [approveBtn, rejectBtn, replyBtn, cancelBtn];

  const newTaskBtn = document.getElementById("newTaskBtn");
  const startComposer = document.getElementById("startComposer");
  const startRequestedBy = document.getElementById("startRequestedBy");
  const taskDescription = document.getElementById("taskDescription");
  const scheduleRoot = document.getElementById("scheduleRoot");
  const startDropzone = document.getElementById("startDropzone");
  const startFileInput = document.getElementById("startFileInput");
  const startFileList = document.getElementById("startFileList");
  const startHint = document.getElementById("startHint");
  const startCancelBtn = document.getElementById("startCancelBtn");

  const ACTIVITY_KEY = localStorage.getItem("mas_activity_key") || "dev-local";
  const storedBy = localStorage.getItem("mas_requested_by") || "";
  if (storedBy) {
    requestedBy.value = storedBy;
    startRequestedBy.value = storedBy;
  }

  const LIVE_LABELS = {
    idle: "ожидание",
    connecting: "подключение",
    live: "онлайн",
    reconnecting: "переподключение",
  };

  let currentTask = null;
  let source = null;
  let rendered = new Set();
  let gateState = null;
  let awaitingHuman = false;
  let submitAction = "reply";
  let taskCatalog = [];
  let flashTimer = null;
  let startOpen = false;
  /** @type {{ file: File, kind: string }[]} */
  let pendingFiles = [];

  function pathTaskId() {
    const m = location.pathname.match(/^\/t\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function showFlash(message, { ok = false } = {}) {
    if (flashTimer) {
      clearTimeout(flashTimer);
      flashTimer = null;
    }
    const text = String(message || "").trim();
    if (!text) {
      flashEl.hidden = true;
      flashEl.textContent = "";
      flashEl.classList.remove("flash-ok");
      return;
    }
    flashEl.hidden = false;
    flashEl.textContent = text;
    flashEl.classList.toggle("flash-ok", Boolean(ok));
    flashTimer = setTimeout(() => {
      flashEl.hidden = true;
      flashEl.textContent = "";
      flashEl.classList.remove("flash-ok");
      flashTimer = null;
    }, ok ? 3200 : 8000);
  }

  function setLive(state) {
    liveDot.hidden = state !== "live";
    liveLabel.textContent = LIVE_LABELS[state] || state;
  }

  function roleClass(role) {
    if (/human|инженер|operator/i.test(role || "")) return "human";
    return /orchestrator/i.test(role || "") ? "orch" : "spec";
  }

  function questionText(q) {
    if (!q || typeof q !== "object") return String(q || "");
    return String(q.text || q.question || q.message || q.id || "").trim();
  }

  function classifyFile(file) {
    const name = (file.name || "").toLowerCase();
    if (/\.(xlsx|xls)$/.test(name)) return "excel";
    if (/\.dev$/.test(name)) return "trajectory";
    if (/\.(cps3|grd|grid)$/.test(name)) return "surface";
    if (/\.(data|inc|sch|txt|grdecl)$/.test(name)) return "schedule";
    return "other";
  }

  function kindLabel(kind) {
    return ({
      excel: "excel",
      schedule: "schedule",
      trajectory: "dev",
      surface: "surface",
      other: "file",
    })[kind] || "file";
  }

  function setComposerArmed(armed) {
    awaitingHuman = Boolean(armed);
    // Start composer takes the bottom slot when open.
    composer.hidden = startOpen || !awaitingHuman;
    composer.classList.toggle("armed", awaitingHuman && !startOpen);
    for (const btn of hitlButtons) btn.disabled = !awaitingHuman || startOpen;
  }

  function setStartOpen(open) {
    startOpen = Boolean(open);
    startComposer.hidden = !startOpen;
    newTaskBtn.classList.toggle("active-start", startOpen);
    setComposerArmed(awaitingHuman);
    if (startOpen) {
      if (!startRequestedBy.value.trim() && requestedBy.value.trim()) {
        startRequestedBy.value = requestedBy.value.trim();
      }
      taskDescription.focus();
    }
  }

  function renderPendingFiles() {
    startFileList.innerHTML = "";
    if (!pendingFiles.length) {
      startFileList.hidden = true;
      return;
    }
    startFileList.hidden = false;
    pendingFiles.forEach((entry, index) => {
      const li = document.createElement("li");
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = kindLabel(entry.kind);
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = entry.file.name;
      name.title = entry.file.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `Убрать ${entry.file.name}`);
      remove.textContent = "×";
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        pendingFiles.splice(index, 1);
        renderPendingFiles();
      });
      li.append(kind, name, remove);
      startFileList.append(li);
    });
  }

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    for (const file of incoming) {
      if (!file || !file.name) continue;
      const kind = classifyFile(file);
      if (kind === "other") {
        showFlash(`Неизвестный тип файла: ${file.name}`);
        continue;
      }
      if (kind === "excel" && pendingFiles.some((f) => f.kind === "excel")) {
        showFlash("Можно прикрепить только один Excel workbook.");
        continue;
      }
      if (kind === "surface" && pendingFiles.some((f) => f.kind === "surface")) {
        showFlash("Можно прикрепить только один surface файл.");
        continue;
      }
      if (pendingFiles.some((f) => f.file.name === file.name && f.file.size === file.size)) continue;
      if (pendingFiles.length >= 40) {
        showFlash("Слишком много файлов (макс. 40).");
        break;
      }
      pendingFiles.push({ file, kind });
    }
    renderPendingFiles();
  }

  function renderGate(gate, { status, version, awaiting } = {}) {
    gateState = gate && typeof gate === "object" ? gate : null;
    const armed = Boolean(awaiting && gateState && gateState.gate_id);
    setComposerArmed(armed);

    if (!gateState) {
      gatePanel.hidden = true;
      return;
    }

    gatePanel.hidden = false;
    gateKind.textContent = gateState.kind || "human_gate";
    gateMeta.textContent = [
      gateState.gate_id ? `gate ${gateState.gate_id}` : null,
      version != null ? `v${version}` : gateState.expected_version != null ? `v${gateState.expected_version}` : null,
      status || null,
    ].filter(Boolean).join(" · ");
    gateReason.textContent = gateState.reason || "Требуется решение человека.";
    gateQuestions.innerHTML = "";
    const questions = Array.isArray(gateState.questions) ? gateState.questions : [];
    for (const q of questions) {
      const li = document.createElement("li");
      const text = questionText(q);
      li.append(document.createTextNode(text || "Вопрос"));
      if (q.expected_format || q.required) {
        const meta = document.createElement("span");
        meta.className = "q-meta";
        meta.textContent = [
          q.required ? "обязательно" : "необязательно",
          q.expected_format ? `формат: ${q.expected_format}` : null,
        ].filter(Boolean).join(" · ");
        li.append(meta);
      }
      gateQuestions.append(li);
    }
    composerHint.textContent = armed
      ? "Утвердить / отклонить / ответить / отменить — gate_id и version подставятся сами."
      : "Gate закрыт. Можно открыть другую задачу или дождаться следующего HITL.";
  }

  function renderTurn(turn, { animate = true } = {}) {
    const id = `${turn.turn_id}:${turn.at}:${turn.status}:${turn.duration_ms || ""}:${turn.kind || ""}`;
    if (rendered.has(id)) return;
    rendered.add(id);
    empty.hidden = true;

    const li = document.createElement("li");
    const fromRole = turn.from?.role || "Orchestrator";
    const toRole = turn.to?.role || "Specialist";
    const isHuman = turn.kind === "hitl" || /human/i.test(fromRole) || /^HUMAN_/.test(turn.status || "") || turn.status === "TASK_STARTED";
    li.className = `turn outcome-${turn.outcome || "info"}${isHuman ? " human" : ""}`;
    if (!animate) {
      li.style.animation = "none";
      li.style.opacity = "1";
    }

    const who = document.createElement("div");
    who.className = `who ${roleClass(fromRole)}`;
    const fromEl = document.createElement("span");
    fromEl.className = "role";
    fromEl.textContent = fromRole;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "→";
    const toEl = document.createElement("span");
    toEl.className = "role";
    toEl.textContent = toRole;
    who.append(fromEl, arrow, toEl);

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
        const b = document.createElement("b");
        b.textContent = chip.label;
        el.append(b, document.createTextNode(` ${String(chip.value)}`));
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

  async function refreshRail() {
    const res = await fetch("/v1/tasks");
    const data = await res.json();
    taskCatalog = Array.isArray(data.tasks) ? data.tasks : [];

    taskRail.innerHTML = "";
    if (!taskCatalog.length) {
      const emptyMsg = document.createElement("p");
      emptyMsg.className = "rail-empty";
      emptyMsg.id = "railEmpty";
      emptyMsg.textContent = "Нет задач. Создайте новую или дождитесь handoff от Trace Writer.";
      taskRail.append(emptyMsg);
      return;
    }

    for (const task of taskCatalog) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = task.task_id === currentTask ? "active" : "";
      const id = document.createElement("span");
      id.className = "id";
      id.textContent = task.task_id;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `${task.turn_count} turns · ${task.last_at_abs || task.updated_at || ""}`;
      btn.append(id, meta);
      if (task.awaiting_human) {
        const flag = document.createElement("span");
        flag.className = "hitl-flag";
        flag.textContent = "HITL";
        btn.append(flag);
      }
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

  function applyFeedMeta(data) {
    if (data.hitl_backend) backendLabel.textContent = `HITL: ${data.hitl_backend}`;
    renderGate(data.human_gate ?? data.gate ?? null, {
      status: data.status,
      version: data.version,
      awaiting: data.awaiting_human,
    });
  }

  async function openTask(taskId) {
    if (!taskId) return;
    currentTask = taskId;
    history.replaceState({}, "", `/t/${encodeURIComponent(taskId)}`);
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = false;
    title.textContent = taskId;
    subtitle.textContent = "Краткий ход работы и HITL: утвердить / отклонить / ответить / отменить без ручного копирования CAS-полей.";
    closeStream();
    setLive("connecting");
    renderGate(null, { awaiting: false });
    showFlash("");

    try {
      const snap = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}`);
      if (snap.ok) {
        const data = await snap.json();
        title.textContent = data.title || taskId;
        applyFeedMeta(data);
        for (const turn of data.activity || []) renderTurn(turn, { animate: false });
      } else {
        showFlash(`Не удалось загрузить задачу (${snap.status}).`);
      }
    } catch (_) {
      showFlash("Сеть недоступна при загрузке задачи.");
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
          applyFeedMeta(msg);
          for (const turn of msg.activity || []) renderTurn(turn, { animate: false });
        } else if (msg.type === "turn") {
          renderTurn(msg.turn, { animate: true });
        } else if (msg.type === "gate") {
          applyFeedMeta(msg);
        }
      } catch (_) { /* ignore malformed SSE */ }
    };
    await refreshRail();
  }

  async function submitHitl(action) {
    if (!currentTask) {
      showFlash("Сначала выберите задачу в списке слева.");
      return;
    }
    const by = requestedBy.value.trim();
    if (!by) {
      requestedBy.focus();
      showFlash("Укажите requested_by — именной accountable инженер.");
      return;
    }
    localStorage.setItem("mas_requested_by", by);
    startRequestedBy.value = by;
    const responseText = humanResponse.value.trim();
    if (action === "reply" && !responseText) {
      humanResponse.focus();
      showFlash("Для ответа нужен текст указаний.");
      return;
    }
    if (action === "cancel" && !window.confirm("Отменить задачу на HITL-gate?")) {
      return;
    }

    composer.classList.add("busy");
    composerHint.textContent = "Отправляем HITL…";
    try {
      const res = await fetch(`/v1/tasks/${encodeURIComponent(currentTask)}/hitl`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Activity-Key": ACTIVITY_KEY,
        },
        body: JSON.stringify({
          action,
          requested_by: by,
          human_response: responseText || null,
          gate_id: gateState?.gate_id || null,
          expected_version: gateState?.expected_version ?? null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string"
          ? data.detail
          : `HITL не принят (${res.status})`;
        showFlash(detail);
        composerHint.textContent = "Не удалось отправить. Проверьте статус задачи и backend.";
        return;
      }
      if (data.turn) renderTurn(data.turn, { animate: true });
      applyFeedMeta({
        human_gate: data.human_gate,
        status: data.orchestrator?.status,
        version: data.orchestrator?.version,
        awaiting_human: data.awaiting_human,
        hitl_backend: data.backend,
      });
      if (action === "reply") humanResponse.value = "";
      showFlash(
        action === "approve" ? "Утверждено."
          : action === "reject" ? "Отклонено."
            : action === "cancel" ? "Задача отменена."
              : "Ответ отправлен.",
        { ok: true },
      );
      await refreshRail();
    } catch (_) {
      showFlash("Сеть недоступна при отправке HITL.");
      composerHint.textContent = "Не удалось отправить. Проверьте сеть и backend.";
    } finally {
      composer.classList.remove("busy");
    }
  }

  async function submitStart(e) {
    e.preventDefault();
    const by = startRequestedBy.value.trim();
    const description = taskDescription.value.trim();
    if (!by) {
      startRequestedBy.focus();
      showFlash("Укажите requested_by — именной accountable инженер.");
      return;
    }
    if (!description) {
      taskDescription.focus();
      showFlash("Нужно описание задачи.");
      return;
    }
    localStorage.setItem("mas_requested_by", by);
    requestedBy.value = by;

    const form = new FormData();
    form.append("task_description", description);
    form.append("requested_by", by);
    const root = scheduleRoot.value.trim();
    if (root) form.append("schedule_root", root);

    let excel = null;
    let surface = null;
    const schedules = [];
    const trajectories = [];
    for (const entry of pendingFiles) {
      if (entry.kind === "excel") excel = entry.file;
      else if (entry.kind === "surface") surface = entry.file;
      else if (entry.kind === "trajectory") trajectories.push(entry.file);
      else schedules.push(entry.file);
    }
    if (excel) form.append("file", excel, excel.name);
    if (surface) form.append("surface_file", surface, surface.name);
    for (const f of schedules) form.append("schedule_files", f, f.name);
    for (const f of trajectories) form.append("trajectory_files", f, f.name);

    startComposer.classList.add("busy");
    startHint.textContent = "Создаём задачу…";
    try {
      const res = await fetch("/v1/tasks/start", {
        method: "POST",
        headers: { "X-Activity-Key": ACTIVITY_KEY },
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map((d) => d.msg || d).join("; ")
            : `Старт не принят (${res.status})`;
        showFlash(detail);
        startHint.textContent = "Не удалось создать. Проверьте backend и вложения.";
        return;
      }
      pendingFiles = [];
      renderPendingFiles();
      taskDescription.value = "";
      scheduleRoot.value = "";
      setStartOpen(false);
      showFlash(
        data.backend === "local"
          ? `Локальная задача ${data.task_id} (Orchestrator не вызван).`
          : `Задача ${data.task_id} создана.`,
        { ok: true },
      );
      await openTask(data.task_id);
    } catch (_) {
      showFlash("Сеть недоступна при создании задачи.");
      startHint.textContent = "Не удалось создать. Проверьте сеть и backend.";
    } finally {
      startComposer.classList.remove("busy");
      startHint.textContent = "Как Form — MAS Entry: objective + вложения. Live backend отправит start в Orchestrator.";
    }
  }

  for (const btn of hitlButtons) {
    btn.addEventListener("click", () => {
      submitAction = btn.dataset.action || "reply";
    });
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    submitHitl(submitAction || "reply");
  });

  newTaskBtn.addEventListener("click", () => setStartOpen(!startOpen));
  startCancelBtn.addEventListener("click", () => setStartOpen(false));
  startComposer.addEventListener("submit", submitStart);

  startDropzone.addEventListener("click", () => startFileInput.click());
  startDropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      startFileInput.click();
    }
  });
  startFileInput.addEventListener("change", () => {
    addFiles(startFileInput.files);
    startFileInput.value = "";
  });
  ["dragenter", "dragover"].forEach((evt) => {
    startDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      startDropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    startDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      startDropzone.classList.remove("dragover");
    });
  });
  startDropzone.addEventListener("drop", (e) => {
    addFiles(e.dataTransfer?.files);
  });

  const initial = pathTaskId();
  if (initial) openTask(initial);
  else {
    refreshRail();
    fetch("/health").then((r) => r.json()).then((h) => {
      if (h.hitl_backend) backendLabel.textContent = `HITL: ${h.hitl_backend}`;
    }).catch(() => {});
  }
})();
