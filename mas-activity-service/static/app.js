(() => {
  const thread = document.getElementById("thread");
  const empty = document.getElementById("empty");
  const notFound = document.getElementById("notFound");
  const notFoundId = document.getElementById("notFoundId");
  const title = document.getElementById("title");
  const titleText = document.getElementById("titleText");
  const statusDot = document.getElementById("statusDot");
  const requestPanel = document.getElementById("requestPanel");
  const requestText = document.getElementById("requestText");
  const taskRail = document.getElementById("taskRail");
  const railList = document.getElementById("railList");
  const flashEl = document.getElementById("flash");
  const waitBar = document.getElementById("waitBar");
  const waitLabel = document.getElementById("waitLabel");
  const waitElapsed = document.getElementById("waitElapsed");
  const statusBanner = document.getElementById("statusBanner");
  const statusBannerLabel = document.getElementById("statusBannerLabel");
  const statusBannerText = document.getElementById("statusBannerText");
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
  const brandHome = document.getElementById("brandHome");
  const startComposer = document.getElementById("startComposer");
  const startRequestedBy = document.getElementById("startRequestedBy");
  const taskDescription = document.getElementById("taskDescription");
  const scheduleRoot = document.getElementById("scheduleRoot");
  const scheduleRootField = document.getElementById("scheduleRootField");
  const startDropzone = document.getElementById("startDropzone");
  const startFileInput = document.getElementById("startFileInput");
  const startFileList = document.getElementById("startFileList");
  const startHint = document.getElementById("startHint");
  const startCancelBtn = document.getElementById("startCancelBtn");
  const startSubmitBtn = document.getElementById("startSubmitBtn");

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
  /** Raw status code → short RU badge; title keeps the code for debug. */
  const STATUS_LABELS = {
    DELEGATED: "Передано специалисту",
    EXCEL_EVIDENCE_READY: "Факты из Excel",
    INVALID_SOURCE_FACTS_PACKET: "Пакет Excel неполный",
    CALCULATION_DATA_READY: "Расчёт готов",
    SCHEDULE_EVIDENCE_GAP: "Не хватает полей",
    MALFORMED_EVIDENCE_GAP: "Некорректный пропуск",
    STALLED_EVIDENCE_LOOP: "Петля остановлена",
    EXCEL_EVIDENCE_BUDGET_EXHAUSTED: "Лимит Excel",
    BUILDER_ITERATION_BUDGET_EXHAUSTED: "Лимит Builder",
    RESUME_SCHEDULE: "Продолжаем schedule",
    INVALID_EXCEL_EVIDENCE_SNAPSHOT: "Снимок не совпал",
    TASK_STARTED: "Задача создана",
    AWAITING_HUMAN: "Ждём вас",
    HUMAN_REPLY: "Ваш ответ",
    HUMAN_APPROVED: "Утверждено",
    HUMAN_REJECTED: "Отклонено",
    SCHEDULE_DRAFT_READY: "Черновик schedule",
    VERIFIED: "Проверено",
    SUCCEEDED: "Успешно",
    COMPLETED: "Завершено",
    NEEDS_INPUT: "Нужны данные",
    NEEDS_DECISION: "Нужно решение",
    NEEDS_APPROVAL: "Нужно утверждение",
    ORCH_CONFLICT: "Отклонено оркестратором",
    CONFLICT: "Конфликт",
    conflict: "Конфликт",
    planning: "Планирование",
    PLANNING: "Планирование",
    handoff: "Handoff",
  };
  const CYRILLIC_RE = /[А-Яа-яЁё]/;
  function statusLabel(code) {
    const raw = String(code || "").trim();
    if (!raw) return "handoff";
    return STATUS_LABELS[raw] || STATUS_LABELS[raw.toUpperCase()] || raw;
  }
  /** Hide muted secondary line when it is English machine jargon (no Cyrillic). */
  function shouldShowMutedText(turn) {
    const text = String(turn.text || "").trim();
    const brief = String(turn.brief || "").trim();
    if (!text || !brief || text === brief) return false;
    if (!CYRILLIC_RE.test(text)) return false;
    return true;
  }
  let streamState = "idle";

  let currentTask = null;
  let startResumeTask = null;
  let source = null;
  let rendered = new Set();
  let gateState = null;
  let awaitingHuman = false;
  let submitAction = "reply";
  let taskCatalog = [];
  let flashTimer = null;
  let startOpen = false;
  let waitTimer = null;
  let waitStartedAt = 0;
  /** @type {{ available?: boolean, filename?: string, byte_length?: number, download_path?: string } | null} */
  let scheduleArtifact = null;
  /** @type {{ file: File, kind: string }[]} */
  let pendingFiles = [];

  // Turns where engineer should inspect the current SCHEDULE .INC.
  const SCHEDULE_FILE_REVIEW_STATUSES = new Set([
    "SCHEDULE_DRAFT_READY",
    "VERIFIED",
    "HUMAN_APPROVED",
    "RELEASE_APPROVED",
    "SCHEDULE_RELEASE_READY",
    "succeeded",
  ]);

  function pathTaskId() {
    const m = location.pathname.match(/^\/t\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function showFlash(message, { ok = false, sticky = false } = {}) {
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
    if (sticky) return;
    flashTimer = setTimeout(() => {
      flashEl.hidden = true;
      flashEl.textContent = "";
      flashEl.classList.remove("flash-ok");
      flashTimer = null;
    }, ok ? 3200 : 20000);
  }

  function formatStartError(detail, statusCode) {
    const raw = String(detail || "").trim();
    if (/Planner Structured Output|does not match the expected schema|outputParserFailReason/i.test(raw)) {
      return (
        "Оркестратор упал на шаге Planner: модель вернула JSON не по схеме. "
        + "В списке слева могут появиться «фантомные» eng_* со статусом planning и пустой лентой — это не готовая задача. "
        + "Исходная ошибка: " + raw
      );
    }
    if (/timed out|timeout/i.test(raw)) {
      return "Оркестратор не ответил вовремя (timeout). " + raw;
    }
    if (raw) return raw;
    return `Старт не принят (${statusCode || "?"}).`;
  }

  function emptyFeedMessage(data) {
    const st = String(data?.status || "").trim().toLowerCase();
    if (st === "planning") {
      return (
        "Лента пустая: задача зависла на planning. "
        + "Оркестратор успел завести eng_* в каталоге, но handoff ещё не записал (часто после сбоя Planner). "
        + "Это не результат — попробуйте создать задачу снова, когда Planner починен."
      );
    }
    if (/conflict|error|fail|cancel|reject/.test(st)) {
      return data?.status_message
        || "Задача завершилась с ошибкой/конфликтом, а handoff в ленту не пришёл. Смотрите баннер статуса выше.";
    }
    return "Лента пока пуста — ждём первые handoff от оркестратора.";
  }

  function formatElapsed(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    if (sec < 60) return `${sec} с`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function showWait(label) {
    if (!waitBar) return;
    waitBar.hidden = false;
    waitBar.setAttribute("aria-busy", "true");
    if (waitLabel) waitLabel.textContent = label || "Ждём ответ…";
    waitStartedAt = Date.now();
    if (waitElapsed) {
      waitElapsed.hidden = false;
      waitElapsed.textContent = "0 с";
    }
    if (waitTimer) clearInterval(waitTimer);
    waitTimer = setInterval(() => {
      if (waitElapsed) waitElapsed.textContent = formatElapsed(Date.now() - waitStartedAt);
    }, 250);
  }

  function hideWait() {
    if (waitTimer) {
      clearInterval(waitTimer);
      waitTimer = null;
    }
    if (!waitBar) return;
    waitBar.hidden = true;
    waitBar.setAttribute("aria-busy", "false");
    if (waitElapsed) {
      waitElapsed.hidden = true;
      waitElapsed.textContent = "";
    }
  }

  function renderStatusBanner(status, message) {
    if (!statusBanner || !statusBannerText) return;
    const st = String(status || "").trim();
    const msg = String(message || "").trim();
    const isConflict = /conflict/i.test(st);
    const isError = /error|fail|reject|cancel/i.test(st);
    if (!msg && !isConflict && !isError) {
      statusBanner.hidden = true;
      statusBannerText.textContent = "";
      statusBanner.classList.remove("tone-wait");
      return;
    }
    statusBanner.hidden = false;
    statusBanner.classList.toggle("tone-wait", !isConflict && !isError);
    if (statusBannerLabel) {
      statusBannerLabel.textContent = isConflict
        ? "Конфликт"
        : isError
          ? "Ошибка"
          : "Статус";
    }
    statusBannerText.textContent = msg
      || (isConflict
        ? "Оркестратор отклонил запрос (conflict). Создайте задачу заново — в ленте выше обычно есть причина."
        : st);
  }

  function statusTone(status, awaiting) {
    const s = String(status || "").trim();
    const lower = s.toLowerCase();
    if (
      awaiting
      || /^AWAITING_HUMAN$/i.test(s)
      || /needs_input|needs_approval|awaiting|human_gate|hitl/i.test(lower)
    ) {
      return "hitl";
    }
    if (/ошибк|conflict/i.test(s) || /error|fail|reject|cancel|denied|stall|abort|conflict/i.test(lower)) {
      return "error";
    }
    if (!s || s === "—" || s === "…" || /idle|select|выбер/i.test(lower)) {
      return "idle";
    }
    return "ok";
  }

  function setLive(state) {
    // Connection state kept for a11y title; the visible pulse follows task status.
    streamState = state || "idle";
    if (title) title.title = LIVE_LABELS[streamState] || streamState;
  }

  function setStatusDot(tone) {
    if (!statusDot) return;
    const t = tone || "idle";
    if (t === "idle" && !currentTask) {
      statusDot.hidden = true;
      statusDot.className = "status-dot";
      return;
    }
    statusDot.hidden = false;
    statusDot.className = `status-dot pulse tone-${t}`;
  }

  function displayRole(role) {
    const raw = String(role || "").trim();
    if (!raw || /^specialist$/i.test(raw)) return "User";
    return raw;
  }

  function roleClass(role) {
    const label = displayRole(role);
    if (/human|инженер|operator|^user$/i.test(label) || /human|инженер|operator|^user$/i.test(role || "")) {
      return "human";
    }
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

  function clearWorkspaceView({ titleLabel = "Выберите задачу" } = {}) {
    closeStream();
    setLive("idle");
    rendered = new Set();
    thread.innerHTML = "";
    hideNotFound();
    renderRequest(null);
    renderGate(null, { awaiting: false });
    renderStatusBanner(null, null);
    setScheduleArtifact(null);
    setComposerArmed(false);
    setTaskHeader(null);
    if (titleText) titleText.textContent = titleLabel;
    else title.textContent = titleLabel;
    empty.hidden = true;
    empty.textContent = "";
    const list = railList || taskRail;
    if (list) {
      for (const btn of list.querySelectorAll("button.active")) btn.classList.remove("active");
    }
  }

  function setStartOpen(open, { resume = true } = {}) {
    const next = Boolean(open);
    if (next && !startOpen) {
      // Detach from previous task so its request/thread/SSE don't stay on screen.
      startResumeTask = currentTask;
      currentTask = null;
      history.replaceState({}, "", "/");
      clearWorkspaceView({ titleLabel: "Новая задача" });
    }
    if (!next && startOpen) {
      startOpen = false;
      startComposer.hidden = true;
      newTaskBtn.classList.toggle("active-start", false);
      setComposerArmed(awaitingHuman);
      const resumeId = resume ? startResumeTask : null;
      startResumeTask = null;
      if (resumeId && !currentTask) {
        openTask(resumeId);
        return;
      }
      if (!currentTask) {
        clearWorkspaceView();
      }
      return;
    }
    startOpen = next;
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

  function isIncOrDataName(name) {
    return /\.(data|inc)$/i.test(String(name || ""));
  }

  function scheduleIncDataEntries() {
    return pendingFiles.filter((entry) => isIncOrDataName(entry.file && entry.file.name));
  }

  function syncScheduleRootField() {
    if (!scheduleRootField || !scheduleRoot) return;
    const files = scheduleIncDataEntries();
    const prev = scheduleRoot.value;
    if (files.length < 2) {
      scheduleRootField.hidden = true;
      scheduleRoot.innerHTML = '<option value="">Какой .data / .INC главный?</option>';
      scheduleRoot.value = "";
      return;
    }
    scheduleRootField.hidden = false;
    scheduleRoot.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Какой .data / .INC главный?";
    scheduleRoot.append(placeholder);
    for (const entry of files) {
      const opt = document.createElement("option");
      opt.value = entry.file.name;
      opt.textContent = entry.file.name;
      scheduleRoot.append(opt);
    }
    if (prev && files.some((entry) => entry.file.name === prev)) {
      scheduleRoot.value = prev;
    }
  }

  function renderPendingFiles() {
    startFileList.innerHTML = "";
    if (!pendingFiles.length) {
      startFileList.hidden = true;
      syncScheduleRootField();
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
    syncScheduleRootField();
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
    if (armed) {
      composerHint.hidden = true;
      composerHint.textContent = "";
    } else {
      composerHint.hidden = false;
      composerHint.textContent = "Сейчас отвечать не нужно. Можно открыть другую задачу или дождаться следующего запроса.";
    }
  }

  function renderTurn(turn, { animate = true } = {}) {
    const id = `${turn.turn_id}:${turn.at}:${turn.status}:${turn.duration_ms || ""}:${turn.kind || ""}`;
    if (rendered.has(id)) return;
    rendered.add(id);
    empty.hidden = true;

    const li = document.createElement("li");
    const fromRole = displayRole(turn.from?.role || "Orchestrator");
    const toRole = displayRole(turn.to?.role || "User");
    const isHuman = turn.kind === "hitl" || /human|^user$/i.test(fromRole) || /^HUMAN_/.test(turn.status || "") || turn.status === "TASK_STARTED";
    li.className = `turn outcome-${turn.outcome || "info"}${isHuman ? " human" : ""}`;
    li._masTurn = turn;
    if (!animate) {
      li.style.animation = "none";
      li.style.opacity = "1";
    }

    const who = document.createElement("div");
    who.className = `who ${roleClass(turn.from?.role || fromRole)}`;
    who.title = `${fromRole} → ${toRole}`;
    const kicker = document.createElement("span");
    kicker.className = "who-kicker";
    kicker.textContent = isHuman ? "Ответ" : "Handoff";
    const fromEl = document.createElement("span");
    fromEl.className = "role from";
    fromEl.textContent = fromRole;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.setAttribute("aria-hidden", "true");
    const toEl = document.createElement("span");
    toEl.className = "role to";
    toEl.textContent = toRole;
    who.append(kicker, fromEl, arrow, toEl);

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const top = document.createElement("div");
    top.className = "turn-top";
    const status = document.createElement("div");
    status.className = "status";
    const rawStatus = turn.status || turn.kind || "handoff";
    status.textContent = statusLabel(rawStatus);
    status.title = String(rawStatus);
    top.append(status);
    if (turn.duration_label) {
      const dur = document.createElement("span");
      dur.className = "duration";
      dur.textContent = turn.duration_label;
      dur.title = "Время работы до этого handoff";
      top.append(dur);
    }
    bubble.append(top);

    const brief = document.createElement("p");
    brief.className = "brief";
    brief.textContent = turn.brief || turn.text || "";
    bubble.append(brief);

    if (shouldShowMutedText(turn)) {
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

    appendScheduleDownload(bubble, turn);

    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = [turn.at_abs || turn.at, turn.stage].filter(Boolean).join(" · ");
    bubble.append(meta);

    li.append(who, bubble);
    thread.append(li);
    thread.scrollTop = thread.scrollHeight;
  }

  async function refreshRail({ durable = false } = {}) {
    const res = await fetch(`/v1/tasks${durable ? "?durable=1" : ""}`);
    const data = await res.json();
    taskCatalog = Array.isArray(data.tasks) ? data.tasks : [];

    const list = railList || taskRail;
    list.innerHTML = "";
    if (!taskCatalog.length) {
      const emptyMsg = document.createElement("p");
      emptyMsg.className = "rail-empty";
      emptyMsg.id = "railEmpty";
      emptyMsg.textContent = "Пока нет задач — создайте первую, нажав на кнопку выше.";
      list.append(emptyMsg);
      if (newTaskBtn) newTaskBtn.classList.toggle("active-start", startOpen);
      return data;
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
      const turnsLabel =
        task.turn_count == null ? "лента…" : `${task.turn_count} turns`;
      const stLabel = statusLabel(task.status || task.last_status || "");
      meta.textContent = `${stLabel || "—"} · ${turnsLabel}`;
      btn.title = `${task.task_id}\n${task.title || ""}\n${task.updated_at || ""}`;
      btn.append(id, meta);
      if (task.awaiting_human) {
        const flag = document.createElement("span");
        flag.className = "hitl-flag";
        flag.textContent = "HITL";
        btn.append(flag);
      }
      btn.addEventListener("click", () => {
        startResumeTask = null;
        setStartOpen(false, { resume: false });
        openTask(task.task_id);
      });
      list.append(btn);
    }
    if (newTaskBtn) newTaskBtn.classList.toggle("active-start", startOpen);
    return data;
  }

  function closeStream() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function renderRequest(objective) {
    const text = String(objective || "").trim();
    if (!text) {
      requestText.textContent = "";
      requestPanel.hidden = true;
      return;
    }
    requestText.textContent = text;
    requestPanel.hidden = false;
  }

  function setTaskHeader(taskId, status, opts = {}) {
    const awaiting = Object.prototype.hasOwnProperty.call(opts, "awaiting")
      ? Boolean(opts.awaiting)
      : awaitingHuman;
    if (!taskId) {
      title.classList.remove("task-line");
      if (titleText) titleText.textContent = "Выберите задачу";
      else title.textContent = "Выберите задачу";
      setStatusDot("idle");
      return;
    }
    title.classList.add("task-line");
    const st = String(status || "").trim() || "—";
    const stRu = statusLabel(st);
    const line = `task_id: ${taskId} · ${stRu}${stRu !== st ? ` (${st})` : ""}`;
    if (titleText) titleText.textContent = line;
    else title.textContent = line;
    setStatusDot(statusTone(st, awaiting));
  }

  function setScheduleArtifact(meta) {
    const prevPath = scheduleArtifact && scheduleArtifact.download_path;
    const art = meta && typeof meta === "object" && meta.available && meta.download_path ? meta : null;
    scheduleArtifact = art;
    if (art && art.download_path !== prevPath) {
      refreshScheduleDownloadsOnThread();
    }
  }

  function turnWantsScheduleDownload(turn) {
    if (!scheduleArtifact) return false;
    const status = String(turn.status || "").trim();
    if (SCHEDULE_FILE_REVIEW_STATUSES.has(status)) return true;
    if (/SCHEDULE_.*READY|RELEASE|VERIF/i.test(status)) return true;
    const chips = Array.isArray(turn.chips) ? turn.chips : [];
    if (chips.some((c) => c && (c.id === "release_ready" || c.label === "release_ready") && (c.value === true || c.value === "true" || c.value === "yes"))) {
      return true;
    }
    // HITL approval gate for schedule release
    if (/^AWAITING_HUMAN$/i.test(status) && chips.some((c) => c && c.id === "gate_kind" && /approval|release/i.test(String(c.value || "")))) {
      return true;
    }
    return false;
  }

  function appendScheduleDownload(bubble, turn) {
    if (!turnWantsScheduleDownload(turn)) return;
    if (bubble.querySelector(".schedule-download-row")) return;
    const art = scheduleArtifact;
    const name = String(art.filename || "schedule.inc");
    const kb = art.byte_length ? ` · ${Math.max(1, Math.round(art.byte_length / 1024))} KB` : "";
    const row = document.createElement("div");
    row.className = "schedule-download-row";
    const link = document.createElement("a");
    link.className = "btn btn-quiet schedule-download";
    link.href = art.download_path;
    link.setAttribute("download", name);
    link.textContent = `Скачать ${name}${kb}`;
    link.title = "Проверить текущий SCHEDULE .INC для этого события";
    row.append(link);
    // Insert before meta-line when present so late refresh keeps layout.
    const meta = bubble.querySelector(".meta-line");
    if (meta) bubble.insertBefore(row, meta);
    else bubble.append(row);
  }

  /** After HITL/SSE captures schedule_artifact, paint download on already-rendered review turns. */
  function refreshScheduleDownloadsOnThread() {
    if (!scheduleArtifact) return;
    for (const li of thread.querySelectorAll("li.turn")) {
      const turn = li._masTurn;
      const bubble = li.querySelector(".bubble");
      if (!turn || !bubble) continue;
      appendScheduleDownload(bubble, turn);
    }
  }

  function applyFeedMeta(data) {
    if (currentTask) {
      setTaskHeader(currentTask, data.status, { awaiting: data.awaiting_human });
    }
    if (Object.prototype.hasOwnProperty.call(data, "objective")) {
      renderRequest(data.objective);
    }
    renderStatusBanner(data.status, data.status_message || data.message);
    renderGate(data.human_gate ?? data.gate ?? null, {
      status: data.status,
      version: data.version,
      awaiting: data.awaiting_human,
    });
    if (Object.prototype.hasOwnProperty.call(data, "schedule_artifact")) {
      setScheduleArtifact(data.schedule_artifact);
    }
  }

  function showNotFound(taskId) {
    currentTask = null;
    closeStream();
    setLive("idle");
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = true;
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setComposerArmed(false);
    setScheduleArtifact(null);
    title.classList.remove("task-line");
    if (titleText) titleText.textContent = "Задача не найдена";
    else title.textContent = "Задача не найдена";
    setStatusDot("error");
    if (notFoundId) notFoundId.textContent = taskId ? `id: ${taskId}` : "";
    if (notFound) notFound.hidden = false;
    const list = railList || taskRail;
    if (list) {
      for (const btn of list.querySelectorAll("button.active")) btn.classList.remove("active");
    }
  }

  function hideNotFound() {
    if (notFound) notFound.hidden = true;
    if (notFoundId) notFoundId.textContent = "";
  }

  /** Infrastructure / network failure — not a missing task (keep /t/<id> for retry). */
  function showLoadError(taskId, message) {
    hideNotFound();
    currentTask = taskId || currentTask;
    closeStream();
    setLive("idle");
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = false;
    empty.textContent =
      "Не удалось загрузить задачу (сервер или n8n временно недоступны). Повторите через бренд NOVATEK RE MAS / Workspace или обновите страницу.";
    if (taskId) setTaskHeader(taskId, "ошибка");
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setComposerArmed(false);
    showFlash(message || "Не удалось загрузить задачу.");
  }

  async function openTask(taskId) {
    if (!taskId) return;
    startResumeTask = null;
    setStartOpen(false, { resume: false });
    hideNotFound();
    currentTask = taskId;
    history.replaceState({}, "", `/t/${encodeURIComponent(taskId)}`);
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = false;
    empty.textContent = "Загрузка задачи…";
    setTaskHeader(taskId, "…");
    renderRequest(null);
    renderStatusBanner(null, null);
    closeStream();
    setLive("connecting");
    renderGate(null, { awaiting: false });
    setScheduleArtifact(null);
    showFlash("");
    showWait("Загружаем задачу…");

    try {
      const snap = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}?durable=1`);
      if (snap.status === 404) {
        showNotFound(taskId);
        await refreshRail();
        return;
      }
      if (!snap.ok) {
        showLoadError(taskId, `Не удалось загрузить задачу (${snap.status}).`);
        await refreshRail();
        return;
      }
      const data = await snap.json();
      const turns = Array.isArray(data.activity) ? data.activity : [];
      setTaskHeader(taskId, data.status, { awaiting: data.awaiting_human });
      applyFeedMeta(data);
      for (const turn of turns) renderTurn(turn, { animate: false });
      if (!turns.length) {
        empty.hidden = false;
        empty.textContent = emptyFeedMessage(data);
        renderStatusBanner(
          data.status,
          data.status_message
            || (String(data.status || "").toLowerCase() === "planning"
              ? "Планирование не завершилось — лента пустая."
              : null),
        );
      } else {
        empty.hidden = true;
        empty.textContent = "";
      }
      if (data.hydrate?.ok === false) {
        const msg = formatHydrateError(data.hydrate.error || "hydrate_failed");
        if (msg) showFlash(msg, { sticky: true });
      } else if (data.hydrate?.truncated) {
        showFlash("Транскрипт усечён: в Data Tables больше лимита handoff (500). Показаны последние turns.");
      }
    } catch (_) {
      showLoadError(taskId, "Сеть недоступна при загрузке задачи.");
      await refreshRail();
      return;
    } finally {
      hideWait();
    }

    source = new EventSource(`/v1/tasks/${encodeURIComponent(taskId)}/stream`);
    source.onopen = () => setLive("live");
    source.onerror = () => setLive("reconnecting");
    source.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") {
          hideNotFound();
          thread.innerHTML = "";
          rendered = new Set();
          empty.hidden = true;
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
      showFlash("Укажите, кто ставит задачу — ФИО инженера.");
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
    if (action === "cancel" && !window.confirm("Отменить задачу? Система больше не будет ждать ответа по этому запросу.")) {
      return;
    }

    composer.classList.add("busy");
    composerHint.hidden = false;
    composerHint.textContent = "Отправляем ответ…";
    showWait("Отправляем ответ оркестратору…");
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
          : `Не удалось принять ответ (${res.status})`;
        showFlash(detail);
        composerHint.hidden = false;
        composerHint.textContent = "Не удалось отправить. Проверьте статус задачи и соединение.";
        return;
      }
      if (data.turn) renderTurn(data.turn, { animate: true });
      applyFeedMeta({
        human_gate: data.human_gate,
        status: data.orchestrator?.status,
        version: data.orchestrator?.version,
        awaiting_human: data.awaiting_human,
        hitl_backend: data.backend,
        schedule_artifact: data.schedule_artifact,
        status_message: data.orchestrator?.message || data.status_message,
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
      showFlash("Сеть недоступна при отправке ответа.");
      composerHint.hidden = false;
      composerHint.textContent = "Не удалось отправить. Проверьте сеть и соединение с сервером.";
    } finally {
      hideWait();
      composer.classList.remove("busy");
    }
  }

  async function submitStart(e) {
    e.preventDefault();
    const by = startRequestedBy.value.trim();
    const description = taskDescription.value.trim();
    if (!by) {
      startRequestedBy.focus();
      showFlash("Укажите, кто ставит задачу — ФИО инженера.");
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
    const root = scheduleRootField && !scheduleRootField.hidden
      ? scheduleRoot.value.trim()
      : "";
    if (schedules.length >= 2 && !root) {
      showFlash("Выберите корневой schedule (.INC), когда прикреплено несколько файлов пакета.");
      if (scheduleRoot) scheduleRoot.focus();
      return;
    }
    if (root) form.append("schedule_root", root);
    if (excel) form.append("file", excel, excel.name);
    if (surface) form.append("surface_file", surface, surface.name);
    for (const f of schedules) form.append("schedule_files", f, f.name);
    for (const f of trajectories) form.append("trajectory_files", f, f.name);

    if (startSubmitBtn?.disabled) return;
    startComposer.classList.add("busy");
    if (startSubmitBtn) {
      startSubmitBtn.disabled = true;
      startSubmitBtn.setAttribute("aria-busy", "true");
    }
    if (startCancelBtn) startCancelBtn.disabled = true;
    startHint.hidden = false;
    startHint.textContent = "Создаём задачу…";
    showWait("Создаём задачу — оркестратор принимает ввод и вложения…");
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
            : "";
        const msg = formatStartError(detail, res.status);
        showFlash(msg, { sticky: true });
        startHint.hidden = false;
        startHint.textContent = msg;
        renderStatusBanner("error", msg);
        await refreshRail({ durable: true });
        return;
      }
      pendingFiles = [];
      renderPendingFiles();
      taskDescription.value = "";
      scheduleRoot.value = "";
      setStartOpen(false, { resume: false });
      const orchStatus = String(data.orchestrator?.status || "").toLowerCase();
      const orchMsg = String(data.status_message || data.orchestrator?.message || "").trim();
      if (orchStatus === "conflict") {
        showFlash(orchMsg || "Оркестратор отклонил старт (conflict).", { sticky: true });
        renderStatusBanner("conflict", orchMsg || null);
      } else {
        showFlash(
          data.backend === "local"
            ? `Задача ${data.task_id} создана (локальный режим).`
            : `Задача ${data.task_id} создана.`,
          { ok: true },
        );
      }
      await refreshRail({ durable: true });
      await openTask(data.task_id);
    } catch (_) {
      const msg = "Сеть недоступна при создании задачи.";
      showFlash(msg, { sticky: true });
      startHint.hidden = false;
      startHint.textContent = msg;
      renderStatusBanner("error", msg);
    } finally {
      hideWait();
      startComposer.classList.remove("busy");
      if (startSubmitBtn) {
        startSubmitBtn.disabled = false;
        startSubmitBtn.removeAttribute("aria-busy");
      }
      if (startCancelBtn) startCancelBtn.disabled = false;
      if (!startHint.textContent || startHint.textContent === "Создаём задачу…") {
        startHint.hidden = true;
        startHint.textContent = "";
      }
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

  function formatHydrateError(err) {
    const text = String(err || "");
    if (/webhook .* is not registered|not registered/i.test(text) || /HTTP 404/.test(text)) {
      return "Workflow «Activity — List Tasks» не активирован в n8n (webhook mas-activity-list-tasks). Обновите страницу после активации.";
    }
    // Local presentation tasks (act_/demo_) are not CAS rows — DT miss is expected.
    if (/task not found in Data Table/i.test(text)) {
      return "";
    }
    if (text.length > 220) return `${text.slice(0, 220)}…`;
    return text;
  }

  async function hydrateFromDataTables({ flash = false, reloadFeed = true } = {}) {
    try {
      const data = await refreshRail({ durable: true });
      if (data?.hydrate?.error) {
        if (flash) showFlash(formatHydrateError(data.hydrate.error));
        return data;
      }
      if (reloadFeed && currentTask) {
        const snap = await fetch(`/v1/tasks/${encodeURIComponent(currentTask)}?durable=1`);
        if (snap.ok) {
          const body = await snap.json();
          rendered = new Set();
          thread.innerHTML = "";
          setTaskHeader(currentTask, body.status, { awaiting: body.awaiting_human });
          applyFeedMeta(body);
          for (const turn of body.activity || []) renderTurn(turn, { animate: false });
          if (body.hydrate?.ok === false) {
            showFlash(formatHydrateError(body.hydrate.error || "hydrate_failed"));
          } else if (body.hydrate?.truncated) {
            showFlash("Транскрипт усечён: в Data Tables больше лимита handoff (500). Показаны последние turns.");
          }
        } else if (flash) {
          showFlash(`Не удалось обновить ленту (${snap.status}).`);
        }
      }
      return data;
    } catch (_) {
      if (flash) showFlash("Не удалось обратиться к n8n Data Tables.");
      return null;
    }
  }

  if (brandHome) {
    brandHome.addEventListener("click", async (e) => {
      e.preventDefault();
      brandHome.classList.add("busy");
      try {
        await hydrateFromDataTables({ flash: false, reloadFeed: true });
        if (!currentTask) history.replaceState({}, "", "/");
      } finally {
        brandHome.classList.remove("busy");
      }
    });
  }

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
  if (initial) {
    openTask(initial).then(() => hydrateFromDataTables({ flash: false, reloadFeed: false }));
  } else {
    hydrateFromDataTables({ flash: false, reloadFeed: false });
  }
})();
