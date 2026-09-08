(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ DOM
  const thread = $("thread");
  const empty = $("empty");
  const notFound = $("notFound");
  const notFoundId = $("notFoundId");
  const title = $("title");
  const titleText = $("titleText");
  const renameTaskBtn = $("renameTaskBtn");
  const renameTaskInput = $("renameTaskInput");
  const statusDot = $("statusDot");
  const headMeta = $("headMeta");
  const statusPill = $("statusPill");
  const headId = $("headId");
  const workspace = $("workspace");
  const chatView = $("chatView");
  const viewChatBtn = $("viewChatBtn");
  const viewSchemaBtn = $("viewSchemaBtn");
  const viewLogBtn = $("viewLogBtn");
  const schemaView = $("schemaView");
  const logView = $("logView");
  const devModeToggle = $("devModeToggle");
  const requestPanel = $("requestPanel");
  const requestText = $("requestText");
  const requestFiles = $("requestFiles");
  const requestPlan = $("requestPlan");
  const requestPlanSteps = $("requestPlanSteps");
  const requestPlanCount = $("requestPlanCount");
  const taskRail = $("taskRail");
  const railList = $("railList");
  const railEmpty = $("railEmpty");
  const railSearch = $("railSearch");
  const railToggle = $("railToggle");
  const flashEl = $("flash");
  const waitBar = $("waitBar");
  const waitLabel = $("waitLabel");
  const waitElapsed = $("waitElapsed");
  const statusBanner = $("statusBanner");
  const statusBannerLabel = $("statusBannerLabel");
  const statusBannerText = $("statusBannerText");
  const gatePanel = $("gatePanel");
  const gateKind = $("gateKind");
  const gateMeta = $("gateMeta");
  const gatePreview = $("gatePreview");
  const gateReason = $("gateReason");
  const gateQuestions = $("gateQuestions");
  const diffExpander = $("diffExpander");
  const diffBody = $("diffBody");
  const composer = $("composer");
  const humanResponse = $("humanResponse");
  const composerHint = $("composerHint");
  const replyBtn = $("replyBtn");
  const restartBtn = $("restartBtn");
  const composerIdle = $("composerIdle");
  const composerIdleText = $("composerIdleText");
  const composerDropHint = $("composerDropHint");
  const newTaskBtn = $("newTaskBtn");
  const brandHome = $("brandHome");
  const startComposer = $("startComposer");
  const taskDescription = $("taskDescription");
  const taskNameInput = $("taskName");
  const scheduleRoot = $("scheduleRoot");
  const scheduleRootField = $("scheduleRootField");
  const startDropzone = $("startDropzone");
  const startFileInput = $("startFileInput");
  const startFileList = $("startFileList");
  const startHint = $("startHint");
  const startCancelBtn = $("startCancelBtn");
  const startSubmitBtn = $("startSubmitBtn");
  const hitlDropzone = $("hitlDropzone");
  const hitlFileInput = $("hitlFileInput");
  const hitlFileList = $("hitlFileList");
  const hitlAttachBtn = $("hitlAttachBtn");
  const inspector = $("inspector");
  const inspectorToggle = $("inspectorToggle");
  const inspectorToggleLabel = $("inspectorToggleLabel");
  const inspectorClose = $("inspectorClose");
  const resultsEmpty = $("resultsEmpty");
  const resultsGroups = $("resultsGroups");
  const inputsPanel = $("inputsPanel");
  const inputsList = $("inputsList");

  const REQUESTED_BY = "mas activity user";
  let resumeWaitHint = false;
  let resumeWaitAnsweredAt = 0;

  function hitlAnsweredCount(feed) {
    return (feed && Array.isArray(feed.events) ? feed.events : []).filter((e) => String(e.kind || "") === "hitl.answered").length;
  }
  function applyResumeWaitHint() {
    if (!resumeWaitHint) return;
    if (!awaitingHuman || hitlAnsweredCount(lastCaseFeed) > resumeWaitAnsweredAt) {
      resumeWaitHint = false;
      return;
    }
    composerHint.hidden = false;
    composerHint.textContent = "Ответ принят, ждём оркестратор…";
  }

  // ------------------------------------------------------------------ labels
  const LIVE_LABELS = { idle: "ожидание", connecting: "подключение", live: "онлайн", reconnecting: "переподключение" };

  /** Raw status code → short RU label; the code itself stays in a title attribute for debugging. */
  const STATUS_LABELS = {
    DELEGATED: "Передано",
    TASK_STARTED: "Создана",
    AWAITING_HUMAN: "Ждём вас",
    HUMAN_REPLY: "Ваш ответ",
    HUMAN_APPROVED: "Одобрено",
    HUMAN_REJECTED: "Отклонено",
    COMPLETED: "Готово",
    NEEDS_INPUT: "Нужны данные",
    NEEDS_DECISION: "Нужно решение",
    NEEDS_APPROVAL: "Ждёт подтверждения",
    result_approval: "Ждёт подтверждения",
    pre_delegation_approval: "Согласование",
    conflict: "Конфликт",
    planning: "Планирование",
    handoff: "Передача",
    running: "В работе",
    new: "Новая",
    done: "Готово",
    failed: "Сбой",
    waiting_user: "Ждём вас",
    waiting_agent: "Агент работает",
    retryable_error: "Ошибка",
    fatal_error: "Сбой",
    error: "Ошибка",
  };
  const CYRILLIC_RE = /[А-Яа-яЁё]/;
  const MACHINE_CODE_RE = /^[A-Z][A-Z0-9_]{3,}$/;

  function statusLabel(code) {
    const raw = String(code || "").trim();
    if (!raw) return "—";
    return STATUS_LABELS[raw] || STATUS_LABELS[raw.toLowerCase()] || STATUS_LABELS[raw.toUpperCase()] || raw;
  }

  /** Visual tone of a case status: idle | running | waiting | done | failed. */
  function toneFor(status, awaiting) {
    const s = String(status || "").trim();
    const lower = s.toLowerCase();
    if (awaiting || /awaiting|waiting_user|needs_|human_gate|hitl|approval/i.test(lower)) return "waiting";
    if (/ошибк|conflict|error|fail|reject|cancel|denied|stall|abort/i.test(lower)) return "failed";
    if (/^(done|completed|succeeded|verified)$/i.test(lower)) return "done";
    if (!s || s === "—" || s === "…") return "idle";
    return "running";
  }

  // ------------------------------------------------------------------ agent registry (UI hardcodes no agent)
  /** @type {Map<string, {title: string, when_to_use: string}>} */
  const agentRegistry = new Map();
  const ROLE_FALLBACKS = {
    orchestrator: "Оркестратор",
    universal_orchestrator: "Оркестратор",
    user: "Вы",
    engineer: "Вы",
    human_operator: "Вы",
    specialist: "Вы",
    "mas activity user": "Вы",
    error_handler: "Обработчик ошибок",
  };

  function isUserRole(role) {
    const raw = String(role || "").trim().toLowerCase();
    return !raw || ["user", "engineer", "human_operator", "specialist", "mas activity user", "вы"].includes(raw);
  }
  function isOrchestratorRole(role) {
    return /orchestrator/i.test(String(role || ""));
  }

  function displayRole(role) {
    const raw = String(role || "").trim();
    if (isUserRole(raw)) return "Вы";
    const reg = agentRegistry.get(raw);
    if (reg && reg.title) return reg.title;
    const lower = raw.toLowerCase();
    if (ROLE_FALLBACKS[lower]) return ROLE_FALLBACKS[lower];
    if (ROLE_FALLBACKS[raw]) return ROLE_FALLBACKS[raw];
    // snake_case agent id without registry row → readable words
    return raw.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function roleSide(role) {
    if (isUserRole(role)) return "user";
    if (isOrchestratorRole(role)) return "orchestrator";
    return "agent";
  }

  function initials(label) {
    const words = String(label || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "·";
    if (words.length === 1) return words[0].slice(0, 2);
    return (words[0][0] + words[1][0]).toUpperCase();
  }

  function svgIcon(id) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.append(use);
    return svg;
  }

  function avatarFor(role) {
    const side = roleSide(role);
    const el = document.createElement("span");
    el.className = "avatar";
    el.setAttribute("aria-hidden", "true");
    if (side === "user") el.append(svgIcon("i-user"));
    else if (side === "orchestrator") el.append(svgIcon("i-orch"));
    else el.textContent = initials(displayRole(role));
    return el;
  }

  async function loadAgents() {
    try {
      const res = await fetch("/agents");
      if (!res.ok) return;
      const data = await res.json();
      for (const row of Array.isArray(data.agents) ? data.agents : []) {
        if (row && row.agent_id) agentRegistry.set(String(row.agent_id), { title: String(row.title || row.agent_id), when_to_use: String(row.when_to_use || "") });
      }
      if (window.MasSchema && typeof window.MasSchema.setAgents === "function") {
        window.MasSchema.setAgents(Array.from(agentRegistry, ([agent_id, v]) => ({ agent_id, ...v })));
      }
    } catch (_) { /* registry is optional for rendering */ }
  }

  // ------------------------------------------------------------------ state
  let streamState = "idle";
  let currentTask = null;
  let currentTaskName = "";
  let renamingTask = false;
  let feedGeneration = 0;
  let lastCaseFeed = emptyFeed();
  let workspaceView = "chat";
  let devMode = false;
  let startResumeTask = null;
  let source = null;
  let feedPollTimer = null;
  let rendered = new Set();
  let lastTurnMeta = null; // { speaker, side, at } of the previous rendered turn (for grouping)
  let lastDayKey = "";
  let gateState = null;
  let selectedChoice = null;
  let taskVersion = null;
  let awaitingHuman = false;
  let restartableCase = false;
  let taskCatalog = [];
  let flashTimer = null;
  let startOpen = false;
  let waitTimer = null;
  let waitStartedAt = 0;
  /** @type {object[]} artifact cards of the open case (inputs + agent results) */
  let artifactCards = [];
  let semanticDiff = null;
  /** @type {{ file: File, kind: string }[]} */
  let pendingFiles = [];
  /** @type {{ file: File, kind: string }[]} */
  let hitlFiles = [];

  function emptyFeed() {
    return { events: [], activity: [], status: "", objective: "", attached_files: [], state: {}, artifacts: [], deliverables: [] };
  }

  function pathTaskId() {
    const m = location.pathname.match(/^\/t\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  // ------------------------------------------------------------------ flash / wait
  function showFlash(message, { ok = false, sticky = false, tone = "" } = {}) {
    if (flashTimer) { clearTimeout(flashTimer); flashTimer = null; }
    const text = String(message || "").trim();
    flashEl.className = "flash";
    if (!text) { flashEl.hidden = true; flashEl.textContent = ""; return; }
    flashEl.hidden = false;
    flashEl.textContent = text;
    flashEl.classList.add(ok ? "info" : tone || "error");
    if (sticky) return;
    flashTimer = setTimeout(() => { flashEl.hidden = true; flashEl.textContent = ""; flashTimer = null; }, ok ? 3200 : 16000);
  }

  function formatStartError(detail, statusCode) {
    const raw = String(detail || "").trim();
    if (/Planner Structured Output|does not match the expected schema|outputParserFailReason/i.test(raw)) {
      return "Оркестратор не смог разобрать решение модели. Исходная ошибка: " + raw;
    }
    if (/timed out|timeout/i.test(raw)) return "Оркестратор не ответил вовремя. " + raw;
    if (raw) return raw;
    return `Старт не принят (${statusCode || "?"}).`;
  }

  function emptyFeedMessage(data) {
    const st = String(data?.status || "").trim().toLowerCase();
    if (st === "planning") return "Задача создана, оркестратор ещё не прислал первое сообщение.";
    if (/conflict|error|fail|cancel|reject/.test(st)) {
      return data?.status_message || "Задача завершилась с ошибкой, а события в ленту не пришли.";
    }
    return "Ждём первые сообщения оркестратора — лента обновится сама.";
  }

  function formatElapsed(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    if (sec < 60) return `${sec} с`;
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }

  function showWait(label) {
    if (!waitBar) return;
    waitBar.hidden = false;
    waitBar.setAttribute("aria-busy", "true");
    if (waitLabel) waitLabel.textContent = label || "Ждём ответ…";
    waitStartedAt = Date.now();
    if (waitElapsed) { waitElapsed.hidden = false; waitElapsed.textContent = "0 с"; }
    if (waitTimer) clearInterval(waitTimer);
    waitTimer = setInterval(() => { if (waitElapsed) waitElapsed.textContent = formatElapsed(Date.now() - waitStartedAt); }, 250);
  }

  function hideWait() {
    if (waitTimer) { clearInterval(waitTimer); waitTimer = null; }
    if (!waitBar) return;
    waitBar.hidden = true;
    waitBar.setAttribute("aria-busy", "false");
    if (waitElapsed) { waitElapsed.hidden = true; waitElapsed.textContent = ""; }
  }

  // ------------------------------------------------------------------ time helpers
  function parseWhen(value) {
    if (!value) return null;
    const d = new Date(value);
    if (!Number.isNaN(d.getTime())) return d;
    const m = String(value).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    return null;
  }
  function fmtTime(d) {
    return d ? d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  }
  function fmtDay(d) {
    if (!d) return "";
    const today = new Date();
    const y = new Date(today); y.setDate(today.getDate() - 1);
    const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (same(d, today)) return "Сегодня";
    if (same(d, y)) return "Вчера";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
  }
  function dayKey(d) {
    return d ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : "";
  }
  function railStamp(task) {
    const d = parseWhen(task?.last_at_abs) || parseWhen(task?.updated_at);
    if (!d) return "";
    const day = fmtDay(d);
    return day === "Сегодня" ? fmtTime(d) : `${day}, ${fmtTime(d)}`;
  }
  function fmtBytes(n) {
    const v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return "";
    if (v < 1024) return `${v} Б`;
    if (v < 1024 * 1024) return `${Math.max(1, Math.round(v / 1024))} КБ`;
    return `${(v / (1024 * 1024)).toFixed(1)} МБ`;
  }
  function fileExt(name) {
    const m = String(name || "").toLowerCase().match(/\.([a-z0-9]{1,6})$/);
    return m ? m[1] : "file";
  }
  function fileBaseName(value) {
    const s = String(value || "").trim().replace(/\\/g, "/");
    if (!s) return "";
    const parts = s.split("/").filter(Boolean);
    return parts[parts.length - 1] || s;
  }

  // ------------------------------------------------------------------ header / status
  function setLive(state) {
    streamState = state || "idle";
    if (title) title.title = LIVE_LABELS[streamState] || streamState;
  }

  function setHeaderStatus(status, awaiting) {
    const tone = toneFor(status, awaiting);
    const st = String(status || "").trim();
    if (!currentTask) {
      statusDot.hidden = true;
      statusDot.removeAttribute("data-tone");
      headMeta.hidden = true;
      statusPill.hidden = true;
      return;
    }
    statusDot.hidden = false;
    statusDot.dataset.tone = tone;
    headMeta.hidden = false;
    if (st && st !== "…") {
      statusPill.hidden = false;
      statusPill.dataset.tone = tone;
      statusPill.textContent = awaiting ? "Ждёт вашего ответа" : statusLabel(st);
      statusPill.title = st;
    } else {
      statusPill.hidden = true;
    }
    headId.textContent = currentTask;
  }

  function catalogTask(taskId) {
    return (taskCatalog || []).find((item) => item && item.task_id === taskId) || null;
  }
  function catalogTaskName(taskId) {
    return String(catalogTask(taskId)?.task_name || "").trim();
  }
  function taskDisplayTitle(taskId) {
    const name = currentTaskName || catalogTaskName(taskId);
    if (name) return name;
    const row = catalogTask(taskId);
    const goal = String(row?.title || lastCaseFeed.objective || "").replace(/\s+/g, " ").trim();
    if (goal) return goal.length > 72 ? `${goal.slice(0, 71)}…` : goal;
    return "Задача";
  }

  function setTaskHeader(taskId, status, opts = {}) {
    const awaiting = Object.prototype.hasOwnProperty.call(opts, "awaiting") ? Boolean(opts.awaiting) : awaitingHuman;
    if (Object.prototype.hasOwnProperty.call(opts, "task_name")) currentTaskName = String(opts.task_name || "").trim();
    if (!taskId) {
      currentTaskName = "";
      renamingTask = false;
      titleText.textContent = "Выберите задачу";
      title.removeAttribute("title");
      renameTaskBtn.hidden = true;
      renameTaskInput.hidden = true;
      renameTaskInput.value = "";
      setHeaderStatus("", false);
      return;
    }
    if (!renamingTask) titleText.textContent = taskDisplayTitle(taskId);
    title.title = currentTaskName && currentTaskName !== taskId ? `${currentTaskName}\n${taskId}` : taskId;
    renameTaskBtn.hidden = renamingTask;
    setHeaderStatus(status, awaiting);
  }

  function beginRenameTask() {
    if (!currentTask || renamingTask) return;
    renamingTask = true;
    titleText.hidden = true;
    renameTaskInput.hidden = false;
    renameTaskInput.value = currentTaskName || catalogTaskName(currentTask) || "";
    renameTaskBtn.hidden = true;
    renameTaskInput.focus();
    renameTaskInput.select();
  }

  function endRenameTask() {
    renamingTask = false;
    titleText.hidden = false;
    renameTaskInput.hidden = true;
    renameTaskInput.value = "";
    if (currentTask) setTaskHeader(currentTask, lastCaseFeed.status, { awaiting: awaitingHuman, task_name: currentTaskName });
  }

  async function saveRenameTask() {
    if (!currentTask || !renamingTask) return;
    const name = renameTaskInput.value.trim();
    const taskId = currentTask;
    renameTaskInput.disabled = true;
    try {
      const res = await fetch(`/cases/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_name: name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { showFlash(data.detail || `Не удалось переименовать задачу (${res.status}).`); return; }
      currentTaskName = String(data.task_name || "").trim();
      endRenameTask();
      setTaskHeader(taskId, data.status, { task_name: currentTaskName });
      await refreshRail();
    } catch (err) {
      showFlash(`Не удалось переименовать задачу: ${err}`);
    } finally {
      renameTaskInput.disabled = false;
    }
  }

  function renderStatusBanner(status, message) {
    const st = String(status || "").trim();
    const msg = String(message || "").trim();
    const isConflict = /conflict/i.test(st);
    const isError = /error|fail|reject|cancel/i.test(st);
    const isDone = /^done$/i.test(st);
    if (!msg && !isConflict && !isError) {
      statusBanner.hidden = true;
      statusBannerText.textContent = "";
      return;
    }
    // The final summary is already the last message of the thread — no need to repeat it above.
    if (isDone && msg && thread.querySelector('.turn[data-kind="case.finished"]')) {
      statusBanner.hidden = true;
      return;
    }
    statusBanner.hidden = false;
    statusBanner.dataset.tone = isConflict || isError ? "failed" : isDone ? "done" : "waiting";
    statusBannerLabel.textContent = isConflict ? "Конфликт" : isError ? "Ошибка" : isDone ? "Итог" : "Статус";
    statusBannerText.textContent = msg || (isConflict ? "Оркестратор отклонил запрос. Создайте задачу заново — причина обычно есть в ленте." : st);
  }

  // ------------------------------------------------------------------ workspace views
  function setWorkspaceView(mode, { persist = true } = {}) {
    // «Лог» exists only in developer mode; without it the request falls back to the chat.
    workspaceView = mode === "schema" ? "schema" : mode === "log" && devMode ? "log" : "chat";
    workspace.classList.toggle("mode-schema", workspaceView === "schema");
    workspace.classList.toggle("mode-chat", workspaceView === "chat");
    workspace.classList.toggle("mode-log", workspaceView === "log");
    chatView.hidden = workspaceView !== "chat";
    schemaView.hidden = workspaceView !== "schema";
    logView.hidden = workspaceView !== "log";
    for (const [btn, name] of [[viewChatBtn, "chat"], [viewSchemaBtn, "schema"], [viewLogBtn, "log"]]) {
      btn.setAttribute("aria-selected", String(workspaceView === name));
      btn.classList.toggle("is-active", workspaceView === name);
    }
    if (persist) { try { sessionStorage.setItem("masActivityView", workspaceView); } catch (_) { /* ignore */ } }
    if (window.MasLog) window.MasLog.setVisible(workspaceView === "log");
    if (workspaceView === "schema") {
      schemaView.focus({ preventScroll: true });
      if (window.MasSchema && typeof window.MasSchema.relayout === "function") requestAnimationFrame(() => window.MasSchema.relayout());
    } else if (workspaceView === "log") {
      logView.focus({ preventScroll: true });
    } else {
      // Turns appended while the chat was hidden could not scroll a 0-height pane — catch up now.
      requestAnimationFrame(() => { chatView.scrollTop = chatView.scrollHeight; });
    }
  }

  // Developer mode: a persistent switch (localStorage) that reveals the «Лог» tab — the technical
  // trace of the case (decisions, handoffs, tool calls, HITL, n8n node errors). Nothing else changes.
  function setDevMode(on, { persist = true } = {}) {
    devMode = Boolean(on);
    devModeToggle.checked = devMode;
    document.body.classList.toggle("dev-mode", devMode);
    viewLogBtn.hidden = !devMode;
    if (persist) { try { localStorage.setItem("masDevMode", devMode ? "1" : "0"); } catch (_) { /* ignore */ } }
    if (!devMode && workspaceView === "log") setWorkspaceView("chat");
  }

  function setInspectorOpen(open) {
    const next = Boolean(open);
    inspector.classList.toggle("is-open", next);
    inspectorToggle.setAttribute("aria-expanded", String(next));
  }

  function setRailOpen(open) {
    taskRail.classList.toggle("is-open", Boolean(open));
    if (railToggle) railToggle.setAttribute("aria-expanded", String(Boolean(open)));
  }

  function bumpFeedGeneration() { feedGeneration += 1; return feedGeneration; }

  function feedMatchesOpenTask(data) {
    if (startOpen || !currentTask) return false;
    if (!data || typeof data !== "object") return false;
    const id = String(data.task_id || data.case_id || "").trim();
    return !id || id === currentTask;
  }

  function syncSchema(data) {
    if (startOpen) return;
    if (window.MasSchema && typeof window.MasSchema.setFeed === "function") window.MasSchema.setFeed(data || lastCaseFeed);
  }

  // ------------------------------------------------------------------ HITL helpers
  const GATE_GENERIC_REASON = "Задача остановилась: не хватает исходных данных. Прикрепите недостающие файлы или напишите, как продолжать.";
  const GATE_GENERIC_ITEM = "Нужны исходные данные. Прикрепите недостающие файлы или напишите, как продолжать.";

  function questionText(q) {
    if (!q || typeof q !== "object") return String(q || "");
    return String(q.text || q.question || q.message || q.id || "").trim();
  }
  function looksMachineAsk(text) {
    const t = String(text || "").trim();
    return !t || MACHINE_CODE_RE.test(t) || !CYRILLIC_RE.test(t);
  }
  function questionOptions(q) {
    const raw = q && Array.isArray(q.options) ? q.options : [];
    const out = [];
    for (const opt of raw) {
      if (opt && typeof opt === "object") {
        const value = String(opt.value ?? opt.id ?? opt.label ?? "").trim();
        const label = String(opt.label ?? opt.value ?? "").trim();
        if (value && label) out.push({ value, label, hint: String(opt.hint || opt.consequence || "").trim() });
      } else if (typeof opt === "string" && opt.trim()) {
        out.push({ value: opt.trim(), label: opt.trim(), hint: "" });
      }
    }
    return out.slice(0, 8);
  }
  function humanizeGateReason(reason, questions) {
    if (!looksMachineAsk(reason)) return String(reason || "").trim() || GATE_GENERIC_REASON;
    const blob = [reason, ...(Array.isArray(questions) ? questions.map((q) => q && (q.code || q.id)) : [])].join(" ");
    if (/INCLUDE_NOT_FOUND/.test(blob)) return "Тела INCLUDE не приложены — ссылки в корне оставляем как есть на той же дате.";
    if (/EXCEL_WORKBOOK_REQUIRED|xlsx|workbook/i.test(blob)) return "Нет книги Excel. Прикрепите файл .xlsx к ответу.";
    return GATE_GENERIC_REASON;
  }
  function humanizeQuestion(q) {
    const raw = questionText(q);
    const code = String(q && (q.code || (MACHINE_CODE_RE.test(raw) ? raw : "")) || "").trim();
    if (!looksMachineAsk(raw)) return raw;
    const path = fileBaseName(q && (q.path || q.target_file_ref));
    const from = fileBaseName(q && (q.file_ref || q.from || q.source_file_ref));
    if (code === "INCLUDE_NOT_FOUND") {
      return path
        ? `INCLUDE «${path}» без тела${from ? `, ссылка из «${from}»` : ""}: оставляем вызов на той же дате. Приложите файл, только если нужно править его содержимое.`
        : "INCLUDE без тела: оставляем вызов на той же дате. Приложите файл, только если нужно править содержимое.";
    }
    if (code === "INCLUDE_BODY_REQUIRED") {
      return path
        ? `Нужно тело INCLUDE «${path}»${from ? `, ссылка из «${from}»` : ""}: приложите этот .inc, иначе содержимое нечем править.`
        : "Нужно тело INCLUDE. Приложите этот .inc, иначе содержимое нечем править.";
    }
    if (code === "EXCEL_WORKBOOK_REQUIRED" || /xlsx|workbook/i.test(raw)) return "Прикрепите книгу Excel (.xlsx или .xls) к ответу.";
    if (code === "BASELINE_REQUIRED") return "Прикрепите предыдущий schedule (.inc / .data).";
    const bits = [path, from, q && q.keyword, q && (q.entity || q.well)].map((x) => String(x || "").trim()).filter(Boolean);
    return bits.length ? `Нужны данные: ${bits.join(", ")}. Прикрепите файл или напишите уточнение.` : GATE_GENERIC_ITEM;
  }

  // ------------------------------------------------------------------ files (compose / reply)
  function classifyFile(file) {
    const name = (file.name || "").toLowerCase();
    if (/\.(xlsx|xls|xlsm|xltx|xltm)$/.test(name)) return "excel";
    if (/\.dev$/.test(name)) return "trajectory";
    if (/\.(cps3|grd|grid)$/.test(name)) return "surface";
    if (/\.(data|inc|sch|txt|grdecl)$/.test(name)) return "schedule";
    return "other";
  }

  function setComposerArmed(armed) {
    awaitingHuman = Boolean(armed);
    const showComposer = !startOpen && (awaitingHuman || restartableCase);
    composer.hidden = !showComposer;
    composer.classList.toggle("is-disabled", !awaitingHuman);
    hitlDropzone.hidden = !awaitingHuman;
    composerDropHint.hidden = !awaitingHuman;
    hitlFileList.hidden = !awaitingHuman || !hitlFiles.length;
    replyBtn.disabled = !awaitingHuman || startOpen;
    humanResponse.disabled = !awaitingHuman;
    hitlAttachBtn.disabled = !awaitingHuman;
    composerIdle.hidden = awaitingHuman || !restartableCase || startOpen;
    restartBtn.disabled = !restartableCase || startOpen || !currentTask;
    if (!composerIdle.hidden) {
      const tone = toneFor(lastCaseFeed.status, false);
      composerIdleText.textContent = tone === "failed"
        ? "Задача остановилась с ошибкой. Можно перезапустить её с теми же исходными файлами."
        : "Задача завершена. Если нужно пересчитать — перезапустите её с теми же файлами.";
    }
    applyResumeWaitHint();
  }

  function setRestartable(flag) {
    restartableCase = Boolean(flag);
    setComposerArmed(awaitingHuman);
  }

  function isIncOrDataName(name) { return /\.(data|inc)$/i.test(String(name || "")); }

  function syncScheduleRootField() {
    const files = pendingFiles.filter((entry) => isIncOrDataName(entry.file && entry.file.name));
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
    if (prev && files.some((entry) => entry.file.name === prev)) scheduleRoot.value = prev;
  }

  function addToBucket(bucket, fileList) {
    for (const file of Array.from(fileList || [])) {
      if (!file || !file.name) continue;
      const kind = classifyFile(file);
      if (kind === "other") { showFlash(`Неизвестный тип файла: ${file.name}`); continue; }
      if (kind === "surface" && bucket.some((f) => f.kind === "surface")) { showFlash("Можно прикрепить только один файл поверхности."); continue; }
      if (bucket.some((f) => f.file.name === file.name && f.file.size === file.size)) continue;
      if (bucket.length >= 40) { showFlash("Слишком много файлов (максимум 40)."); break; }
      bucket.push({ file, kind });
    }
  }

  function fileChip({ name, bytes, href, download, onRemove, title: tip }) {
    const el = document.createElement(href ? "a" : "li");
    el.className = "file-chip";
    if (href) { el.href = href; if (download) el.setAttribute("download", download); }
    const ext = document.createElement("span");
    ext.className = "ext";
    ext.dataset.ext = fileExt(name);
    ext.textContent = fileExt(name).slice(0, 4);
    const label = document.createElement("span");
    label.className = "name";
    label.textContent = name;
    el.title = tip || name;
    el.append(ext, label);
    const size = fmtBytes(bytes);
    if (size) {
      const s = document.createElement("span");
      s.className = "size";
      s.textContent = size;
      el.append(s);
    }
    if (href) el.append(svgIcon("i-download"));
    if (onRemove) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove";
      remove.setAttribute("aria-label", `Убрать ${name}`);
      remove.append(svgIcon("i-close"));
      remove.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); onRemove(); });
      el.append(remove);
    }
    return el;
  }

  function renderFileChips(listEl, bucket, onMutate) {
    listEl.innerHTML = "";
    listEl.hidden = !bucket.length;
    bucket.forEach((entry, index) => {
      listEl.append(fileChip({
        name: entry.file.name,
        bytes: entry.file.size,
        onRemove: () => { bucket.splice(index, 1); onMutate(); },
      }));
    });
  }

  function renderPendingFiles() { renderFileChips(startFileList, pendingFiles, renderPendingFiles); syncScheduleRootField(); }
  function renderHitlFiles() { renderFileChips(hitlFileList, hitlFiles, renderHitlFiles); }
  function addFiles(fileList) { addToBucket(pendingFiles, fileList); renderPendingFiles(); }
  function addHitlFiles(fileList) { addToBucket(hitlFiles, fileList); renderHitlFiles(); }

  // ------------------------------------------------------------------ HITL gate
  const GATE_KIND_LABELS = {
    needs_input: "Нужны данные",
    needs_decision: "Нужно решение",
    needs_approval: "Ждёт подтверждения",
    result_approval: "Ждёт подтверждения",
    pre_delegation_approval: "Согласование",
    human_gate: "Вопрос",
  };

  function renderGate(gate, { awaiting } = {}) {
    gateState = gate && typeof gate === "object" ? gate : null;
    const armed = Boolean(awaiting && gateState && gateState.gate_id);
    setComposerArmed(armed);
    if (!gateState) {
      gatePanel.hidden = true;
      gatePanel.open = false;
      return;
    }
    gatePanel.hidden = false;
    gatePanel.open = armed;
    const rawKind = String(gateState.kind || "human_gate");
    gateKind.textContent = GATE_KIND_LABELS[rawKind] || GATE_KIND_LABELS[rawKind.toLowerCase()] || "Вопрос";
    gateKind.title = rawKind;
    gateMeta.textContent = armed ? "" : "ответ уже получен";
    const questions = Array.isArray(gateState.questions) ? gateState.questions : [];
    const reason = humanizeGateReason(gateState.reason, questions) || "Ожидается ваше решение.";
    gateReason.textContent = reason;
    gateQuestions.innerHTML = "";
    const prevChoice = selectedChoice && selectedChoice.gate_id === gateState.gate_id ? selectedChoice : null;
    selectedChoice = null;
    const seenAsk = new Set();
    for (const q of questions) {
      const text = humanizeQuestion(q);
      if (!text || seenAsk.has(text)) continue;
      seenAsk.add(text);
      const li = document.createElement("li");
      li.className = "gate-question";
      if (text !== reason) {
        const p = document.createElement("p");
        p.className = "gate-question-text";
        p.textContent = text;
        li.append(p);
      }
      const options = questionOptions(q);
      if (options.length) {
        const row = document.createElement("div");
        row.className = "gate-options";
        row.setAttribute("role", "group");
        const qid = String(q.question_id || q.id || gateState.gate_id || "");
        for (const opt of options) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "gate-option";
          btn.textContent = opt.label;
          btn.disabled = !armed;
          if (opt.hint) btn.title = opt.hint;
          if (prevChoice && prevChoice.question_id === qid && prevChoice.value === opt.value) {
            btn.classList.add("is-selected");
            selectedChoice = { gate_id: gateState.gate_id, question_id: qid, value: opt.value, label: opt.label };
          }
          btn.addEventListener("click", () => {
            const already = btn.classList.contains("is-selected");
            row.querySelectorAll(".gate-option").forEach((b) => b.classList.remove("is-selected"));
            if (already) { selectedChoice = null; return; }
            btn.classList.add("is-selected");
            selectedChoice = { gate_id: gateState.gate_id, question_id: qid, value: opt.value, label: opt.label };
            if (!humanResponse.value.trim()) humanResponse.focus();
          });
          row.append(btn);
        }
        li.append(row);
      }
      const columns = Array.isArray(q?.accepts?.table?.columns) ? q.accepts.table.columns : [];
      if (columns.length) {
        const hint = document.createElement("p");
        hint.className = "gate-note";
        hint.textContent = `Колонки таблицы: ${columns.map((c) => (c && (c.label || c.key)) || "").filter(Boolean).join(" · ")}`;
        li.append(hint);
      }
      gateQuestions.append(li);
    }
    gateQuestions.hidden = Boolean(questions.length) && !gateQuestions.childElementCount;
    const line = String(reason).replace(/\s+/g, " ").trim();
    gatePreview.textContent = (line.length > 140 ? `${line.slice(0, 139)}…` : line) + (seenAsk.size > 1 ? ` · ${seenAsk.size} пунктов` : "");
    gatePreview.title = reason;
    if (armed) {
      composerHint.hidden = true;
      composerHint.textContent = "";
      const kind = String(gateState.kind || "").toLowerCase();
      const hasOptions = questions.some((q) => questionOptions(q).length);
      humanResponse.placeholder = (kind === "result_approval" || kind === "needs_approval")
        ? "Своими словами: принять результат — или что проверить и доработать"
        : hasOptions ? "Выберите вариант выше и/или напишите своими словами…" : "Уточнения, недостающие данные, что ещё поправить…";
    } else {
      composerHint.hidden = true;
      composerHint.textContent = "";
    }
    applyResumeWaitHint();
  }

  // ------------------------------------------------------------------ deliverable / artifact cards
  function cardsByKind(kind) {
    const rows = (artifactCards || []).filter((c) => c && String(c.kind || "") === kind);
    if (kind !== "input") return rows;
    const rank = (card) => {
      const role = String(card.role || "");
      const name = String(card.filename || "").toLowerCase();
      if (role === "excel" || /\.(xlsx|xls|xlsm|xltx|xltm)$/i.test(name)) return 0;
      if (role === "schedule_source") return 1;
      return 2;
    };
    return rows.slice().sort((a, b) => rank(a) - rank(b));
  }
  function cardById(id) {
    return (artifactCards || []).find((c) => c && c.artifact_id === id) || null;
  }
  function cardDownloadPath(card) {
    if (!card) return "";
    if (card.download_path) return card.download_path;
    if (currentTask && card.artifact_id) return `/cases/${encodeURIComponent(currentTask)}/artifacts/${encodeURIComponent(card.artifact_id)}`;
    return "";
  }
  function cardFilename(card) {
    return String(card?.filename || card?.artifact_id || "файл");
  }

  function deliverableChips(cards) {
    const ul = document.createElement("ul");
    ul.className = "file-chips turn-files";
    for (const raw of cards) {
      const card = (raw && raw.artifact_id && cardById(raw.artifact_id)) || raw;
      if (!card) continue;
      const li = document.createElement("li");
      li.append(fileChip({
        name: cardFilename(card),
        bytes: card.bytes,
        href: cardDownloadPath(card),
        download: cardFilename(card),
        title: card.summary || cardFilename(card),
      }));
      ul.append(li);
    }
    return ul.childElementCount ? ul : null;
  }

  function renderInspector() {
    const deliverables = cardsByKind("deliverable");
    const inputs = cardsByKind("input");
    resultsGroups.innerHTML = "";
    resultsEmpty.hidden = Boolean(deliverables.length);
    if (!currentTask) {
      resultsEmpty.textContent = "Откройте задачу — здесь будут файлы, которые подготовили агенты.";
    } else if (toneFor(lastCaseFeed.status, awaitingHuman) === "running") {
      resultsEmpty.textContent = "Агенты работают. Как только кто-то из них закончит, его файлы появятся здесь.";
    } else {
      resultsEmpty.textContent = "Результаты появятся здесь, когда агенты закончат работу. У каждого — свои файлы.";
    }
    const groups = new Map();
    for (const card of deliverables) {
      const key = String(card.producer || "");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(card);
    }
    for (const [producer, cards] of groups) {
      const group = document.createElement("section");
      group.className = "result-group";
      const head = document.createElement("div");
      head.className = "result-group-head";
      const copy = document.createElement("div");
      const t = document.createElement("div");
      t.className = "result-group-title";
      t.textContent = producer ? displayRole(producer) : "Результаты";
      const sub = document.createElement("div");
      sub.className = "result-group-sub";
      sub.textContent = pluralFiles(cards.length);
      copy.append(t, sub);
      head.append(producer ? avatarFor(producer) : avatarFor("orchestrator"), copy);
      group.append(head);
      for (const card of cards) {
        const a = document.createElement("a");
        a.className = "result-file";
        a.href = cardDownloadPath(card);
        a.setAttribute("download", cardFilename(card));
        a.title = card.summary || cardFilename(card);
        const ext = document.createElement("span");
        ext.className = "ext";
        ext.dataset.ext = fileExt(cardFilename(card));
        ext.textContent = fileExt(cardFilename(card)).slice(0, 4);
        const c = document.createElement("span");
        c.className = "result-file-copy";
        const n = document.createElement("div");
        n.className = "result-file-name";
        n.textContent = cardFilename(card);
        const m = document.createElement("div");
        m.className = "result-file-meta";
        m.textContent = [fmtBytes(card.bytes), roleLabel(card.role)].filter(Boolean).join(" · ");
        c.append(n, m);
        a.append(ext, c, svgIcon("i-download"));
        group.append(a);
      }
      resultsGroups.append(group);
    }
    inputsPanel.hidden = !inputs.length;
    inputsList.innerHTML = "";
    for (const card of inputs) {
      const li = document.createElement("li");
      li.append(fileChip({ name: cardFilename(card), bytes: card.bytes, href: cardDownloadPath(card), download: cardFilename(card) }));
      inputsList.append(li);
    }
    inspectorToggleLabel.textContent = deliverables.length ? `Результаты · ${deliverables.length}` : "Результаты";
  }

  function pluralFiles(n) {
    const mod10 = n % 10; const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} файл`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} файла`;
    return `${n} файлов`;
  }
  function roleLabel(role) {
    return ({
      schedule_out: "итоговый schedule",
      diff: "изменения относительно исходного",
      schedule_source: "исходный schedule",
      excel: "книга Excel",
      trajectory: "траектория",
      surface: "поверхность",
      include: "INCLUDE",
    })[String(role || "")] || "";
  }

  function setArtifactCards(cards) {
    artifactCards = Array.isArray(cards) ? cards.filter((c) => c && typeof c === "object") : [];
    renderInspector();
    renderRequest(lastCaseFeed.objective, attachedFilesFromFeed(lastCaseFeed));
  }

  // ------------------------------------------------------------------ semantic diff
  function editLine(edit) {
    if (!edit || typeof edit !== "object") return "";
    if (edit.summary) return String(edit.summary);
    return [edit.keyword, edit.well || edit.entity, edit.operation || edit.op, edit.message].map((x) => (x == null ? "" : String(x).trim())).filter(Boolean).join(" · ");
  }

  function renderSemanticDiff() {
    const diff = semanticDiff;
    const keywords = Array.isArray(diff?.changed_keywords) ? diff.changed_keywords.filter(Boolean) : [];
    const wells = Array.isArray(diff?.commissioning_wells) ? diff.commissioning_wells.filter(Boolean) : [];
    const edits = Array.isArray(diff?.edits) ? diff.edits : [];
    const summary = String(diff?.summary || "").trim();
    const hasContent = Boolean(summary || keywords.length || wells.length || edits.length || diff?.include_graph_changed);
    diffBody.replaceChildren();
    diffExpander.hidden = !hasContent;
    if (!hasContent) return;
    if (summary) {
      const p = document.createElement("p");
      p.className = "diff-summary";
      p.textContent = summary;
      diffBody.append(p);
    }
    const chipRow = (label, values) => {
      const row = document.createElement("div");
      row.className = "diff-row";
      const l = document.createElement("span");
      l.className = "diff-label";
      l.textContent = label;
      const chips = document.createElement("div");
      chips.className = "diff-chips";
      for (const v of values) {
        const chip = document.createElement("span");
        chip.className = "diff-chip";
        chip.textContent = v;
        chips.append(chip);
      }
      row.append(l, chips);
      return row;
    };
    if (keywords.length) diffBody.append(chipRow("Ключевые слова", keywords));
    if (wells.length) diffBody.append(chipRow("Скважины", wells));
    if (diff?.include_graph_changed) {
      const note = document.createElement("p");
      note.className = "diff-note";
      note.textContent = "Изменён граф INCLUDE.";
      diffBody.append(note);
    }
    if (edits.length) {
      const list = document.createElement("ul");
      list.className = "diff-edits";
      for (const ed of edits) {
        const line = typeof ed === "string" ? ed.trim() : editLine(ed);
        if (!line) continue;
        const li = document.createElement("li");
        li.textContent = line;
        list.append(li);
      }
      if (list.childNodes.length) diffBody.append(list);
    }
  }
  function setSemanticDiff(diff) { semanticDiff = diff && typeof diff === "object" ? diff : null; renderSemanticDiff(); }

  // ------------------------------------------------------------------ request panel
  function attachedFilesFromFeed(data) {
    if (!data || typeof data !== "object") return [];
    if (Array.isArray(data.attached_files) && data.attached_files.length) return data.attached_files.map((f) => String(f || "").trim()).filter(Boolean);
    for (const turn of Array.isArray(data.activity) ? data.activity : []) {
      const details = turn && typeof turn.details === "object" ? turn.details : null;
      if (!details) continue;
      const started = String(turn.status || "").toUpperCase() === "TASK_STARTED" || details.action === "start";
      if (!started) continue;
      if (Array.isArray(details.files)) return details.files.map((f) => String(f || "").trim()).filter(Boolean);
      if (typeof details.files === "string" && details.files.trim()) return details.files.split(",").map((s) => s.trim()).filter(Boolean);
    }
    return [];
  }

  // ------------------------------------------------------------------ plan (O13)
  const PLAN_STATUS_LABEL = { pending: "не начат", active: "в работе", done: "готово", blocked: "нужен инженер", dropped: "не понадобилось" };
  let currentPlan = [];

  function renderPlan(plan) {
    currentPlan = Array.isArray(plan) ? plan.filter((p) => p && typeof p === "object" && p.id) : [];
    requestPlanSteps.innerHTML = "";
    if (!currentPlan.length) { requestPlan.hidden = true; requestPlanCount.textContent = ""; return; }
    const done = currentPlan.filter((p) => p.status === "done").length;
    const counted = currentPlan.filter((p) => p.status !== "dropped").length;
    requestPlanCount.textContent = counted ? `${done} из ${counted}` : "";
    for (const step of currentPlan) {
      const li = document.createElement("li");
      li.className = "plan-step";
      li.dataset.status = String(step.status || "pending");
      li.dataset.planId = String(step.id);
      const mark = document.createElement("span");
      mark.className = "plan-step-mark";
      mark.setAttribute("aria-hidden", "true");
      const body = document.createElement("div");
      const title = document.createElement("span");
      title.className = "plan-step-title";
      title.textContent = String(step.title || step.id);
      body.append(title);
      const meta = [];
      if (step.agent_id) meta.push(displayRole(step.agent_id));
      meta.push(PLAN_STATUS_LABEL[step.status] || String(step.status || ""));
      if (step.note && (step.status === "blocked" || step.status === "dropped")) meta.push(String(step.note));
      const metaEl = document.createElement("span");
      metaEl.className = "plan-step-meta";
      metaEl.textContent = meta.filter(Boolean).join(" · ");
      body.append(metaEl);
      li.append(mark, body);
      requestPlanSteps.append(li);
    }
    requestPlan.hidden = false;
    requestPanel.hidden = false;
  }

  function renderRequest(objective, attached) {
    const text = String(objective || "").trim();
    const names = Array.isArray(attached) ? attached.map((f) => String(f || "").trim()).filter(Boolean) : [];
    const inputs = cardsByKind("input");
    if (!text && !names.length && !inputs.length && !currentPlan.length) {
      requestText.textContent = "";
      requestFiles.innerHTML = "";
      requestFiles.hidden = true;
      requestPanel.hidden = true;
      return;
    }
    requestText.textContent = text;
    requestFiles.innerHTML = "";
    const chips = inputs.length
      ? inputs.map((card) => fileChip({ name: cardFilename(card), bytes: card.bytes, href: cardDownloadPath(card), download: cardFilename(card) }))
      : names.map((name) => fileChip({ name }));
    const LIMIT = 4;
    chips.forEach((chip, i) => {
      const li = document.createElement("li");
      if (i >= LIMIT) li.hidden = true;
      li.append(chip);
      requestFiles.append(li);
    });
    if (chips.length > LIMIT) {
      const li = document.createElement("li");
      const more = document.createElement("button");
      more.type = "button";
      more.className = "file-chip file-chip-more";
      more.textContent = `ещё ${pluralFiles(chips.length - LIMIT)}`;
      more.addEventListener("click", () => {
        const collapsed = more.dataset.open !== "1";
        requestFiles.querySelectorAll("li").forEach((row, i) => { if (i >= LIMIT && row !== li) row.hidden = !collapsed; });
        more.dataset.open = collapsed ? "1" : "0";
        more.textContent = collapsed ? "свернуть" : `ещё ${pluralFiles(chips.length - LIMIT)}`;
      });
      li.append(more);
      requestFiles.append(li);
    }
    requestFiles.hidden = !requestFiles.childElementCount;
    requestPanel.hidden = false;
  }

  // ------------------------------------------------------------------ thread
  function turnEventKind(turn) {
    return String(turn.event_type || turn.details?.kind || turn.stage || turn.status || "").trim();
  }

  function turnKicker(eventKind, isHitlAsk) {
    if (/^case\.finished$/i.test(eventKind)) return ["Итог", "final"];
    if (/^agent\.result$/i.test(eventKind)) return ["Результат", "result"];
    if (/hitl\.request/i.test(eventKind) || isHitlAsk) return ["Вопрос вам", "request"];
    if (/hitl\.answered|TASK_STARTED/i.test(eventKind)) return ["", ""];
    if (/^agent\.handoff$/i.test(eventKind)) return ["Поручение", "decision"];
    if (/^orchestrator\.decision$/i.test(eventKind)) return ["Решение", "decision"];
    if (/^agent\.failed$|^case\.failed$|^system\.node_error$/i.test(eventKind)) return ["Ошибка", "error"];
    if (/^case\.created$/i.test(eventKind)) return ["Принято", "decision"];
    return ["", ""];
  }

  function turnRenderKey(turn) {
    const eid = turn?.event_id ?? turn?.details?.event_id ?? turn?.turn_id;
    if (eid != null && String(eid).trim() !== "") return `eid:${eid}`;
    return `${turn.turn_id}:${turn.at}:${turn.status}:${turn.duration_ms || ""}:${turn.kind || ""}`;
  }

  function turnDeliverables(turn, eventKind) {
    const payload = turn?.details?.payload && typeof turn.details.payload === "object" ? turn.details.payload : {};
    if (Array.isArray(payload.deliverables) && payload.deliverables.length) return payload.deliverables;
    if (/^case\.finished$/i.test(eventKind)) return cardsByKind("deliverable");
    if (/^agent\.result$/i.test(eventKind)) {
      const agent = String(turn?.details?.agent_id || "");
      const ids = Array.isArray(payload.artifacts) ? payload.artifacts : [];
      return cardsByKind("deliverable").filter((c) => (agent && c.producer === agent) || ids.includes(c.artifact_id));
    }
    return [];
  }

  function renderTurn(turn, { animate = true } = {}) {
    const id = turnRenderKey(turn);
    if (rendered.has(id)) return;
    rendered.add(id);
    empty.hidden = true;

    const eventKind = turnEventKind(turn);
    const fromRole = String(turn.from?.role || "orchestrator");
    const toRole = String(turn.to?.role || turn.from?.role || "user");
    let laneDir = String(turn.lane_dir || turn.details?.lane_dir || "").toLowerCase() || (fromRole === toRole ? "none" : "out");
    if (/^agent\.(accepted|progress)$/i.test(eventKind)) laneDir = "none";
    let speaker = laneDir === "in" ? toRole : fromRole;
    let listener = laneDir === "none" ? "" : (laneDir === "in" ? fromRole : toRole);
    if (/^case\.created$/i.test(eventKind)) {
      // "Принял задачу: …" is the orchestrator acknowledging the engineer's request.
      speaker = isOrchestratorRole(toRole) ? toRole : "orchestrator";
      listener = isUserRole(fromRole) ? fromRole : "user";
    }
    const isHitlAsk = /NEEDS_|AWAITING_HUMAN|RESULT_APPROVAL|PRE_DELEGATION/i.test(String(turn.status || "")) || String(turn.stage || "") === "hitl";
    const isError = turn.outcome === "error" || /^agent\.failed$|^case\.failed$|^system\.node_error$/i.test(eventKind);
    const side = isError && roleSide(speaker) === "orchestrator" ? "system" : roleSide(speaker);
    const when = parseWhen(turn.at) || parseWhen(turn.at_abs);
    const quiet = /^agent\.(accepted|progress)$/i.test(eventKind);

    const key = dayKey(when);
    if (key && key !== lastDayKey) {
      lastDayKey = key;
      const sep = document.createElement("li");
      sep.className = "day-sep";
      sep.textContent = fmtDay(when);
      thread.append(sep);
      lastTurnMeta = null;
    }

    const li = document.createElement("li");
    li.className = "turn";
    li.dataset.side = side;
    li.dataset.kind = eventKind;
    if (quiet) li.classList.add("is-quiet");
    if (/^case\.finished$/i.test(eventKind)) li.classList.add("is-final");
    if (!animate) li.style.animation = "none";
    li._masTurn = turn;

    const [kickerText, kickerTone] = turnKicker(eventKind, isHitlAsk);
    const cont = lastTurnMeta
      && lastTurnMeta.speaker === speaker
      && lastTurnMeta.side === side
      && !kickerText
      && !lastTurnMeta.kicker
      && when && lastTurnMeta.at && Math.abs(when - lastTurnMeta.at) < 4 * 60 * 1000;
    if (cont) li.classList.add("is-cont");
    lastTurnMeta = { speaker, side, at: when, kicker: kickerText };

    li.append(avatarFor(speaker));
    const main = document.createElement("div");
    main.className = "turn-main";

    const head = document.createElement("div");
    head.className = "turn-head";
    const name = document.createElement("span");
    name.className = "turn-name";
    name.textContent = displayRole(speaker);
    head.append(name);
    if (listener && listener !== speaker) {
      const to = document.createElement("span");
      to.className = "turn-to";
      to.append(document.createTextNode("→ "));
      const b = document.createElement("b");
      b.textContent = displayRole(listener);
      to.append(b);
      head.append(to);
    }
    if (kickerText) {
      const kicker = document.createElement("span");
      kicker.className = "turn-kicker";
      kicker.dataset.tone = kickerTone;
      kicker.textContent = kickerText;
      head.append(kicker);
    }
    const time = document.createElement("time");
    time.className = "turn-time";
    time.textContent = fmtTime(when);
    time.title = when ? when.toLocaleString("ru-RU") : "";
    if (turn.duration_label) time.title += `${time.title ? "\n" : ""}${turn.duration_label} с предыдущего события`;
    head.append(time);
    main.append(head);

    const body = document.createElement("div");
    body.className = "turn-body";
    if (isError) body.classList.add("turn-error");
    // The speaker is already named in the header — drop reporter prefixes like «Пользователь ответил:».
    const stripReporter = (s) => (/^hitl\.answered$/i.test(eventKind) ? String(s || "").replace(/^(Пользователь|Инженер)\s+ответил[а]?:\s*/i, "") : String(s || ""));
    body.textContent = stripReporter(turn.brief || turn.text || "");
    main.append(body);

    if (turn.handoff_message && turn.handoff_message !== body.textContent) {
      const note = document.createElement("p");
      note.className = "turn-note";
      note.textContent = turn.handoff_message;
      main.append(note);
    }
    const text = stripReporter(turn.text).trim();
    if (text && text !== body.textContent && CYRILLIC_RE.test(text) && !/^case\.finished$/i.test(eventKind)) {
      const note = document.createElement("p");
      note.className = "turn-note";
      note.textContent = text;
      main.append(note);
    }

    const files = turnDeliverables(turn, eventKind);
    if (files.length) {
      const chips = deliverableChips(files);
      if (chips) main.append(chips);
    }
    if (isError && turn.event_id != null) {
      // Visible in developer mode only (CSS): jumps to this event's record — node, execution, stack.
      const link = document.createElement("a");
      link.className = "turn-log-link";
      link.href = "#log";
      link.dataset.seq = String(turn.event_id);
      link.textContent = "Подробности в логе";
      main.append(link);
    }

    li.append(main);

    const transcript = thread.closest(".transcript");
    const isAtBottom = !transcript || transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
    thread.append(li);
    if (isAtBottom && transcript) transcript.scrollTop = transcript.scrollHeight;
  }

  function clearThread() {
    rendered = new Set();
    lastTurnMeta = null;
    lastDayKey = "";
    thread.innerHTML = "";
  }

  // ------------------------------------------------------------------ rail
  function railFilter() {
    return String(railSearch?.value || "").trim().toLowerCase();
  }

  function renderRail() {
    railList.innerHTML = "";
    const q = railFilter();
    const rows = (taskCatalog || []).filter((task) => {
      if (!q) return true;
      return [task.task_name, task.title, task.task_id].some((v) => String(v || "").toLowerCase().includes(q));
    });
    railEmpty.hidden = Boolean(rows.length) || Boolean(q);
    if (!rows.length && q) {
      const p = document.createElement("p");
      p.className = "rail-empty";
      p.textContent = "Ничего не найдено.";
      railList.append(p);
    }
    const attention = rows.filter((t) => t.awaiting_human);
    const rest = rows.filter((t) => !t.awaiting_human);
    const sections = attention.length ? [["Ждут вашего ответа", attention], ["Все задачи", rest]] : [["", rows]];
    for (const [label, tasks] of sections) {
      if (!tasks.length) continue;
      if (label) {
        const h = document.createElement("div");
        h.className = "rail-section";
        h.textContent = label;
        railList.append(h);
      }
      for (const task of tasks) railList.append(railItem(task));
    }
    const active = railList.querySelector(".rail-item.is-active");
    if (active) setTimeout(() => active.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
  }

  function railItem(task) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rail-item" + (task.task_id === currentTask ? " is-active" : "") + (task.awaiting_human ? " attention" : "");
    btn.setAttribute("role", "listitem");
    btn.dataset.taskId = task.task_id;
    const st = task.status || task.last_status || "";
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.dataset.tone = toneFor(st, task.awaiting_human);
    const copy = document.createElement("span");
    const name = String(task.task_name || "").trim();
    const t = document.createElement("span");
    t.className = "rail-title";
    t.textContent = name || String(task.title || "").trim() || task.task_id;
    const meta = document.createElement("span");
    meta.className = "rail-meta";
    const s = document.createElement("span");
    s.textContent = task.awaiting_human ? "Ждёт ответа" : statusLabel(st);
    meta.append(s);
    const stamp = railStamp(task);
    if (stamp) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "·";
      const time = document.createElement("span");
      time.textContent = stamp;
      meta.append(sep, time);
    }
    copy.append(t, meta);
    btn.append(dot, copy);
    btn.title = [name, task.title && task.title !== name ? task.title : "", task.task_id].filter(Boolean).join("\n");
    btn.addEventListener("click", () => {
      startResumeTask = null;
      setStartOpen(false, { resume: false });
      setRailOpen(false);
      openTask(task.task_id);
    });
    return btn;
  }

  async function refreshRail() {
    const res = await fetch("/cases");
    const data = await res.json();
    taskCatalog = Array.isArray(data.tasks) ? data.tasks : [];
    renderRail();
    return data;
  }

  // ------------------------------------------------------------------ feed / stream
  function closeStream() {
    if (feedPollTimer) { clearInterval(feedPollTimer); feedPollTimer = null; }
    if (source) { source.close(); source = null; }
  }

  function applyFeedMeta(data) {
    if (Object.prototype.hasOwnProperty.call(data, "task_name")) currentTaskName = String(data.task_name || "").trim();
    else if (data.state && typeof data.state === "object" && data.state.task_name) currentTaskName = String(data.state.task_name || "").trim();
    if (Object.prototype.hasOwnProperty.call(data, "artifacts") && Array.isArray(data.artifacts)) {
      artifactCards = data.artifacts.filter((c) => c && typeof c === "object");
      renderInspector();
    }
    if (currentTask) setTaskHeader(currentTask, data.status, { awaiting: data.awaiting_human, task_name: currentTaskName });
    if (Array.isArray(data.plan)) renderPlan(data.plan);
    else if (data.state && typeof data.state === "object" && Array.isArray(data.state.plan)) renderPlan(data.state.plan);
    if (Object.prototype.hasOwnProperty.call(data, "objective") || Object.prototype.hasOwnProperty.call(data, "attached_files") || Array.isArray(data.activity) || Array.isArray(data.artifacts)) {
      const objective = Object.prototype.hasOwnProperty.call(data, "objective") ? data.objective : (requestText.textContent || lastCaseFeed.objective || null);
      renderRequest(objective, attachedFilesFromFeed(data.attached_files ? data : lastCaseFeed));
    }
    renderStatusBanner(data.status, data.status_message || data.message);
    setRestartable(Boolean(data.restartable) || Boolean(data.human_gate?.restartable) || String(data.status || "").toLowerCase() === "retryable_error");
    if (data.version != null) taskVersion = data.version;
    else if (data.human_gate?.expected_version != null) taskVersion = data.human_gate.expected_version;
    renderGate(data.human_gate ?? data.gate ?? null, { awaiting: data.awaiting_human });
    if (Object.prototype.hasOwnProperty.call(data, "semantic_diff")) setSemanticDiff(data.semantic_diff);
    if (Object.prototype.hasOwnProperty.call(data, "status")) renderInspector();
  }

  function mergeFeed(data, { animateTurns = false } = {}) {
    if (!feedMatchesOpenTask(data)) return;
    lastCaseFeed = { ...lastCaseFeed, ...data };
    if (Array.isArray(data.events)) lastCaseFeed.events = data.events;
    applyFeedMeta(data);
    const turns = Array.isArray(data.activity) ? data.activity : [];
    for (const turn of turns) renderTurn(turn, { animate: animateTurns });
    if (turns.length) { empty.hidden = true; empty.textContent = ""; }
    // Deliverables can arrive after the finish message rendered — refresh the chips of the last turn.
    if (Array.isArray(data.artifacts)) refreshFinalChips();
    syncSchema(lastCaseFeed);
  }

  function refreshFinalChips() {
    const last = thread.querySelector('.turn[data-kind="case.finished"]');
    if (!last || !last._masTurn) return;
    const main = last.querySelector(".turn-main");
    const old = main.querySelector(".turn-files");
    const chips = deliverableChips(cardsByKind("deliverable"));
    if (old) old.remove();
    if (chips) main.append(chips);
  }

  async function pollFeed() {
    if (!currentTask || startOpen) return;
    const taskId = currentTask;
    const gen = feedGeneration;
    try {
      const res = await fetch(`/cases/${encodeURIComponent(taskId)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (currentTask !== taskId || feedGeneration !== gen || startOpen) return;
      mergeFeed(data, { animateTurns: true });
    } catch (_) { /* SSE stays the source of truth */ }
  }

  function attachLive(taskId) {
    closeStream();
    source = new EventSource(`/cases/${encodeURIComponent(taskId)}/stream`);
    source.onopen = () => {
      setLive("live");
      if (feedPollTimer) { clearInterval(feedPollTimer); feedPollTimer = null; }
    };
    source.onerror = () => {
      setLive("reconnecting");
      if (!feedPollTimer) feedPollTimer = setInterval(() => pollFeed(), 5000);
    };
    source.onmessage = (ev) => {
      if (startOpen || currentTask !== taskId) return;
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "ping") return;
        if (msg.type === "snapshot") {
          hideNotFound();
          lastCaseFeed = { ...emptyFeed(), ...msg };
          applyFeedMeta(msg);
          const turns = Array.isArray(msg.activity) ? msg.activity : [];
          for (const turn of turns) renderTurn(turn, { animate: false });
          if (!turns.length && !thread.querySelector("li.turn")) { empty.hidden = false; empty.textContent = emptyFeedMessage(msg); }
          refreshFinalChips();
          syncSchema(lastCaseFeed);
        } else if (msg.type === "meta") {
          lastCaseFeed = { ...lastCaseFeed, ...msg };
          applyFeedMeta(msg);
          refreshFinalChips();
          syncSchema(lastCaseFeed);
        } else if (msg.type === "trace") {
          // Developer-only rows (tool calls, technical notes): never in the chat, only in «Лог».
          if (window.MasLog) window.MasLog.notify(msg.log);
          if (msg.status) lastCaseFeed.status = msg.status;
        } else if (msg.type === "turn") {
          renderTurn(msg.turn, { animate: true });
          applyFeedMeta(msg);
          if (window.MasLog) window.MasLog.notify(msg.log);
          if (msg.event && msg.event.event_id != null) {
            const events = Array.isArray(lastCaseFeed.events) ? lastCaseFeed.events.slice() : [];
            if (!events.some((row) => row && row.event_id === msg.event.event_id)) events.push(msg.event);
            lastCaseFeed.events = events;
          }
          if (msg.turn) lastCaseFeed.activity = (Array.isArray(lastCaseFeed.activity) ? lastCaseFeed.activity : []).concat([msg.turn]);
          if (msg.status) lastCaseFeed.status = msg.status;
          syncSchema(lastCaseFeed);
        } else if (msg.type === "gate") {
          applyFeedMeta(msg);
        }
      } catch (_) { /* ignore malformed SSE */ }
    };
  }

  // ------------------------------------------------------------------ open / clear / not found
  function clearWorkspaceView({ titleLabel = "Выберите задачу", composing = false } = {}) {
    bumpFeedGeneration();
    closeStream();
    setLive("idle");
    clearThread();
    hideNotFound();
    artifactCards = [];
    lastCaseFeed = emptyFeed();
    if (window.MasLog) window.MasLog.clear();
    renderPlan([]);
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    renderStatusBanner(null, null);
    setSemanticDiff(null);
    setComposerArmed(false);
    setTaskHeader(null);
    titleText.textContent = titleLabel;
    renderInspector();
    if (composing) {
      empty.hidden = true;
    } else {
      empty.hidden = false;
      empty.textContent = "Выберите задачу слева или создайте новую.";
    }
    if (window.MasSchema && typeof window.MasSchema.reset === "function") window.MasSchema.reset();
    for (const btn of railList.querySelectorAll(".rail-item.is-active")) btn.classList.remove("is-active");
  }

  function setStartOpen(open, { resume = true } = {}) {
    const next = Boolean(open);
    if (next && !startOpen) {
      startResumeTask = currentTask;
      currentTask = null;
      history.replaceState({}, "", "/");
      clearWorkspaceView({ titleLabel: "Новая задача", composing: true });
    }
    if (!next && startOpen) {
      startOpen = false;
      startComposer.hidden = true;
      workspace.classList.remove("is-composing");
      setComposerArmed(awaitingHuman);
      const resumeId = resume ? startResumeTask : null;
      startResumeTask = null;
      if (resumeId && !currentTask) { openTask(resumeId); return; }
      if (!currentTask) clearWorkspaceView();
      return;
    }
    startOpen = next;
    startComposer.hidden = !startOpen;
    workspace.classList.toggle("is-composing", startOpen);
    startHint.hidden = true;
    startHint.textContent = "";
    setComposerArmed(awaitingHuman);
    if (startOpen) {
      setRailOpen(false);
      setWorkspaceView("chat", { persist: false });
      taskDescription.focus();
    }
  }

  function showNotFound(taskId) {
    currentTask = null;
    currentTaskName = "";
    closeStream();
    setLive("idle");
    clearThread();
    empty.hidden = true;
    artifactCards = [];
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setComposerArmed(false);
    setSemanticDiff(null);
    renderInspector();
    titleText.textContent = "Задача не найдена";
    setHeaderStatus("", false);
    notFoundId.textContent = taskId || "";
    notFound.hidden = false;
    setWorkspaceView("chat", { persist: false });
    for (const btn of railList.querySelectorAll(".rail-item.is-active")) btn.classList.remove("is-active");
  }

  function hideNotFound() { notFound.hidden = true; notFoundId.textContent = ""; }

  function showLoadError(taskId, message) {
    hideNotFound();
    currentTask = taskId || currentTask;
    closeStream();
    setLive("idle");
    clearThread();
    empty.hidden = false;
    empty.textContent = "Не удалось загрузить задачу. Возможно, сервер временно недоступен — попробуйте обновить страницу.";
    if (taskId) setTaskHeader(taskId, "error");
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setComposerArmed(false);
    setSemanticDiff(null);
    showFlash(message || "Не удалось загрузить задачу.");
  }

  function formatHydrateError(err) {
    const text = String(err || "");
    if (/webhook .* is not registered|not registered/i.test(text) || /HTTP 404/.test(text)) return "Система пока не готова. Убедитесь, что все рабочие процессы активированы.";
    if (/task not found in Data Table/i.test(text)) return "";
    return text.length > 220 ? `${text.slice(0, 220)}…` : text;
  }

  async function openTask(taskId) {
    if (!taskId) return;
    startResumeTask = null;
    setStartOpen(false, { resume: false });
    hideNotFound();
    currentTask = taskId;
    currentTaskName = catalogTaskName(taskId);
    if (window.MasLog) window.MasLog.setCase(taskId);
    const gen = bumpFeedGeneration();
    history.replaceState({}, "", `/t/${encodeURIComponent(taskId)}`);
    clearThread();
    artifactCards = [];
    lastCaseFeed = emptyFeed();
    empty.hidden = false;
    empty.textContent = "Загружаем задачу…";
    setTaskHeader(taskId, "…", { task_name: currentTaskName });
    renderRequest(null);
    renderStatusBanner(null, null);
    closeStream();
    setLive("connecting");
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setSemanticDiff(null);
    renderInspector();
    showFlash("");
    for (const btn of railList.querySelectorAll(".rail-item")) btn.classList.toggle("is-active", btn.dataset.taskId === taskId);
    attachLive(taskId);

    try {
      const snap = await fetch(`/cases/${encodeURIComponent(taskId)}`);
      if (snap.status === 404) { showNotFound(taskId); await refreshRail(); return; }
      if (!snap.ok) { showLoadError(taskId, `Не удалось загрузить задачу (${snap.status}).`); await refreshRail(); return; }
      const data = await snap.json();
      if (currentTask !== taskId || feedGeneration !== gen) return;
      hideNotFound();
      mergeFeed(data, { animateTurns: false });
      if (!Array.isArray(data.activity) || !data.activity.length) {
        empty.hidden = false;
        empty.textContent = emptyFeedMessage(data);
        renderStatusBanner(data.status, data.status_message || (["running", "new"].includes(String(data.status || "").toLowerCase()) ? "Оркестратор ещё не прислал события — лента и схема обновятся сами." : null));
      }
      if (data.hydrate?.ok === false) {
        const msg = formatHydrateError(data.hydrate.error || "hydrate_failed");
        if (msg) showFlash(msg, { sticky: true });
      } else if (data.hydrate?.truncated) {
        showFlash("История обрезана: показаны только самые свежие сообщения.", { tone: "warn" });
      }
    } catch (_) {
      showLoadError(taskId, "Сеть недоступна при загрузке задачи.");
      await refreshRail();
      return;
    } finally {
      hideWait();
    }
    await refreshRail();
  }

  // ------------------------------------------------------------------ submit: HITL answer
  async function submitHitl() {
    if (!currentTask) { showFlash("Сначала выберите задачу в списке слева."); return; }
    const responseText = humanResponse.value.trim();
    const choice = selectedChoice && selectedChoice.gate_id === gateState?.gate_id ? selectedChoice : null;
    if (!responseText && !hitlFiles.length && !choice) {
      humanResponse.focus();
      showFlash("Выберите вариант, напишите ответ или приложите файл.");
      return;
    }
    composer.classList.add("busy");
    replyBtn.disabled = true;
    composerHint.hidden = false;
    composerHint.textContent = "Отправляем ответ…";
    let resumePending = false;
    try {
      const form = new FormData();
      form.append("action", "reply");
      form.append("requested_by", REQUESTED_BY);
      if (responseText) form.append("human_response", responseText);
      if (gateState?.gate_id) form.append("gate_id", gateState.gate_id);
      form.append("question_id", choice?.question_id || gateState?.gate_id || gateState?.questions?.[0]?.question_id || "Q-1");
      if (choice) form.append("choice", choice.value);
      form.append("answer", responseText || (choice ? choice.label : "(файл)"));
      const ver = gateState?.expected_version ?? taskVersion;
      if (ver != null && ver !== "") form.append("expected_version", String(ver));
      for (const entry of hitlFiles) {
        if (entry.kind === "excel") form.append("file", entry.file, entry.file.name);
        else if (entry.kind === "surface") form.append("surface_file", entry.file, entry.file.name);
        else if (entry.kind === "trajectory") form.append("trajectory_files", entry.file, entry.file.name);
        else form.append("schedule_files", entry.file, entry.file.name);
      }
      const res = await fetch(`/cases/${encodeURIComponent(currentTask)}/answer`, { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFlash(typeof data.detail === "string" ? data.detail : `Не удалось принять ответ (${res.status})`);
        composerHint.hidden = false;
        composerHint.textContent = "Не удалось отправить. Проверьте статус задачи и соединение.";
        return;
      }
      humanResponse.value = "";
      autosize(humanResponse);
      hitlFiles = [];
      selectedChoice = null;
      renderHitlFiles();
      showFlash(choice ? `Вы решили: ${choice.label}` : "Ответ отправлен.", { ok: true });
      resumeWaitHint = true;
      resumeWaitAnsweredAt = hitlAnsweredCount(lastCaseFeed);
      const snap = await fetch(`/cases/${encodeURIComponent(currentTask)}`);
      if (snap.ok) mergeFeed(await snap.json(), { animateTurns: true });
      await refreshRail();
      if (awaitingHuman) resumePending = true;
    } catch (_) {
      showFlash("Сеть недоступна при отправке ответа.");
      composerHint.hidden = false;
      composerHint.textContent = "Не удалось отправить. Проверьте сеть и соединение с сервером.";
    } finally {
      composer.classList.remove("busy");
      setComposerArmed(awaitingHuman);
      if (resumePending) {
        composerHint.hidden = false;
        composerHint.textContent = "Ответ принят, ждём оркестратор…";
      }
    }
  }

  // ------------------------------------------------------------------ submit: new task
  async function submitStart(e) {
    e.preventDefault();
    const description = taskDescription.value.trim();
    if (!description) { taskDescription.focus(); showFlash("Опишите задачу — без этого оркестратору нечего планировать."); return; }

    const form = new FormData();
    form.append("task_description", description);
    form.append("requested_by", REQUESTED_BY);
    const givenName = taskNameInput.value.trim();
    if (givenName) form.append("task_name", givenName);

    const excels = [];
    let surface = null;
    const schedules = [];
    const trajectories = [];
    for (const entry of pendingFiles) {
      if (entry.kind === "excel") excels.push(entry.file);
      else if (entry.kind === "surface") surface = entry.file;
      else if (entry.kind === "trajectory") trajectories.push(entry.file);
      else schedules.push(entry.file);
    }
    const root = !scheduleRootField.hidden ? scheduleRoot.value.trim() : "";
    if (schedules.length >= 2 && !root) {
      showFlash("Укажите, какой из приложенных schedule-файлов главный.");
      scheduleRoot.focus();
      return;
    }
    if (root) {
      form.append("schedule_root", root);
      const idx = schedules.findIndex((f) => f && f.name === root);
      if (idx > 0) { const [chosen] = schedules.splice(idx, 1); schedules.unshift(chosen); }
    }
    // First workbook goes to `file` (Activity contract); the rest ride as attachments (`excel_N` cards).
    excels.forEach((f, i) => form.append(i === 0 ? "file" : "attachments", f, f.name));
    if (surface) form.append("surface_file", surface, surface.name);
    for (const f of schedules) form.append("schedule_files", f, f.name);
    for (const f of trajectories) form.append("trajectory_files", f, f.name);

    if (startSubmitBtn.disabled) return;
    startComposer.classList.add("busy");
    startSubmitBtn.disabled = true;
    startSubmitBtn.setAttribute("aria-busy", "true");
    startCancelBtn.disabled = true;
    startHint.hidden = false;
    startHint.textContent = "Создаём задачу…";
    showWait("Записываем задачу…");
    try {
      const res = await fetch("/cases", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail.map((d) => d.msg || d).join("; ") : "";
        const msg = formatStartError(detail, res.status);
        showFlash(msg, { sticky: true });
        startHint.hidden = false;
        startHint.textContent = msg;
        refreshRail();
        return;
      }
      pendingFiles = [];
      renderPendingFiles();
      taskDescription.value = "";
      taskNameInput.value = "";
      scheduleRoot.value = "";
      startHint.hidden = true;
      startHint.textContent = "";
      setStartOpen(false, { resume: false });
      showFlash(data.task_id ? `Задача «${data.task_name || taskDisplayTitle(data.task_id)}» создана — оркестратор уже работает.` : "Задача создана — оркестратор уже работает.", { ok: true });
      hideWait();
      refreshRail();
      await openTask(data.case_id || data.task_id);
    } catch (_) {
      const msg = "Сеть недоступна при создании задачи.";
      showFlash(msg, { sticky: true });
      startHint.hidden = false;
      startHint.textContent = msg;
    } finally {
      hideWait();
      startComposer.classList.remove("busy");
      startSubmitBtn.disabled = false;
      startSubmitBtn.removeAttribute("aria-busy");
      startCancelBtn.disabled = false;
      if (startHint.textContent === "Создаём задачу…") { startHint.hidden = true; startHint.textContent = ""; }
    }
  }

  // ------------------------------------------------------------------ restart
  restartBtn.addEventListener("click", async () => {
    if (!currentTask) { showFlash("Сначала выберите задачу."); return; }
    if (!window.confirm("Перезапустить задачу с теми же исходными файлами?")) return;
    composer.classList.add("busy");
    showWait("Перезапускаем задачу…");
    try {
      const res = await fetch(`/cases/${encodeURIComponent(currentTask)}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "retry",
          requested_by: REQUESTED_BY,
          human_response: "restart",
          gate_id: gateState?.gate_id || null,
          expected_version: gateState?.expected_version ?? taskVersion ?? null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { showFlash(typeof data.detail === "string" ? data.detail : `Перезапуск не принят (${res.status}).`); return; }
      if (data.ok === false || data.skipped || data.accepted === false) { showFlash(`Перезапуск не выполнен: ${data.reason || data.status || "пропущен"}`); return; }
      applyFeedMeta({
        status: data.orchestrator?.status || data.status,
        version: data.orchestrator?.version || data.version,
        awaiting_human: data.awaiting_human,
        restartable: data.restartable,
        human_gate: data.human_gate,
        status_message: data.orchestrator?.message,
        semantic_diff: data.semantic_diff,
      });
      if (data.turn) renderTurn(data.turn);
      showFlash("Перезапуск принят — лента обновится сама.", { ok: true });
      await pollFeed();
      await refreshRail();
    } catch (err) {
      showFlash(`Не удалось перезапустить задачу: ${err}`);
    } finally {
      composer.classList.remove("busy");
      hideWait();
    }
  });

  // ------------------------------------------------------------------ wiring
  function autosize(el) {
    el.style.height = "auto";
    el.style.height = `${Math.min(220, Math.max(36, el.scrollHeight))}px`;
  }
  humanResponse.addEventListener("input", () => autosize(humanResponse));
  humanResponse.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submitHitl(); }
  });
  composer.addEventListener("submit", (e) => { e.preventDefault(); submitHitl(); });
  hitlAttachBtn.addEventListener("click", () => hitlFileInput.click());
  hitlFileInput.addEventListener("change", () => { addHitlFiles(hitlFileInput.files); hitlFileInput.value = ""; });

  newTaskBtn.addEventListener("click", () => setStartOpen(!startOpen));
  startCancelBtn.addEventListener("click", () => setStartOpen(false));
  startComposer.addEventListener("submit", submitStart);

  renameTaskBtn.addEventListener("click", beginRenameTask);
  renameTaskInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); saveRenameTask(); }
    else if (ev.key === "Escape") { ev.preventDefault(); endRenameTask(); }
  });
  renameTaskInput.addEventListener("blur", () => { if (renamingTask && !renameTaskInput.disabled) saveRenameTask(); });

  viewChatBtn.addEventListener("click", () => setWorkspaceView("chat"));
  viewSchemaBtn.addEventListener("click", () => setWorkspaceView("schema"));
  viewLogBtn.addEventListener("click", () => setWorkspaceView("log"));
  devModeToggle.addEventListener("change", () => setDevMode(devModeToggle.checked));
  // Chat → «в логе»: an error turn links to its record in the developer log.
  thread.addEventListener("click", (e) => {
    const link = e.target.closest(".turn-log-link");
    if (!link) return;
    e.preventDefault();
    if (!devMode) setDevMode(true);
    setWorkspaceView("log");
    if (window.MasLog) window.MasLog.focus(link.dataset.seq);
  });
  inspectorToggle.addEventListener("click", () => setInspectorOpen(!inspector.classList.contains("is-open")));
  inspectorClose.addEventListener("click", () => setInspectorOpen(false));
  if (railToggle) railToggle.addEventListener("click", () => setRailOpen(!taskRail.classList.contains("is-open")));
  if (railSearch) railSearch.addEventListener("input", renderRail);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { setInspectorOpen(false); setRailOpen(false); }
  });

  brandHome.addEventListener("click", async (e) => {
    e.preventDefault();
    startResumeTask = null;
    setStartOpen(false, { resume: false });
    currentTask = null;
    history.replaceState({}, "", "/");
    clearWorkspaceView();
    try { await refreshRail(); } catch (_) { showFlash("Не удалось обновить список задач."); }
  });

  function wireDropzone(zone, input, onFiles, { clickOpens = true } = {}) {
    if (clickOpens) {
      zone.addEventListener("click", () => input.click());
      zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
      input.addEventListener("change", () => { onFiles(input.files); input.value = ""; });
    }
    ["dragenter", "dragover"].forEach((evt) => zone.addEventListener(evt, (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("is-dragover"); }));
    ["dragleave", "drop"].forEach((evt) => zone.addEventListener(evt, (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove("is-dragover"); }));
    zone.addEventListener("drop", (e) => onFiles(e.dataTransfer?.files));
  }
  wireDropzone(startDropzone, startFileInput, addFiles);
  wireDropzone(hitlDropzone, hitlFileInput, addHitlFiles, { clickOpens: false });

  try { setDevMode(localStorage.getItem("masDevMode") === "1", { persist: false }); } catch (_) { /* ignore */ }
  try {
    const saved = sessionStorage.getItem("masActivityView");
    if (saved === "schema" || saved === "log") setWorkspaceView(saved, { persist: false });
  } catch (_) { /* ignore */ }
  setWorkspaceView(workspaceView, { persist: false });
  renderInspector();

  loadAgents().then(() => {
    const initial = pathTaskId();
    if (initial) openTask(initial);
    else refreshRail().catch(() => showFlash("Не удалось загрузить список задач."));
  });
})();
