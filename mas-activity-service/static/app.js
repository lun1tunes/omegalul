(() => {
  const thread = document.getElementById("thread");
  const empty = document.getElementById("empty");
  const notFound = document.getElementById("notFound");
  const notFoundId = document.getElementById("notFoundId");
  const title = document.getElementById("title");
  const titleText = document.getElementById("titleText");
  const renameTaskBtn = document.getElementById("renameTaskBtn");
  const renameTaskInput = document.getElementById("renameTaskInput");
  const statusDot = document.getElementById("statusDot");
  const workspace = document.getElementById("workspace");
  const chatView = document.getElementById("chatView");
  const viewChatBtn = document.getElementById("viewChatBtn");
  const viewSchemaBtn = document.getElementById("viewSchemaBtn");
  const scheduleDownloadHead = document.getElementById("scheduleDownloadHead");
  const schemaView = document.getElementById("schemaView");
  const requestPanel = document.getElementById("requestPanel");
  const requestText = document.getElementById("requestText");
  const requestFiles = document.getElementById("requestFiles");
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
  const diffExpander = document.getElementById("diffExpander");
  const diffBody = document.getElementById("diffBody");
  const composer = document.getElementById("composer");
  const REQUESTED_BY = "mas activity user";
  const humanResponse = document.getElementById("humanResponse");
  const composerHint = document.getElementById("composerHint");
  const replyBtn = document.getElementById("replyBtn");
  const restartBtn = document.getElementById("restartBtn");

  let restartableCase = false;

  const newTaskBtn = document.getElementById("newTaskBtn");
  const brandHome = document.getElementById("brandHome");
  const startComposer = document.getElementById("startComposer");
  const taskDescription = document.getElementById("taskDescription");
  const taskNameInput = document.getElementById("taskName");
  const scheduleRoot = document.getElementById("scheduleRoot");
  const scheduleRootField = document.getElementById("scheduleRootField");
  const startDropzone = document.getElementById("startDropzone");
  const startFileInput = document.getElementById("startFileInput");
  const startFileList = document.getElementById("startFileList");
  const startHint = document.getElementById("startHint");
  const startCancelBtn = document.getElementById("startCancelBtn");
  const startSubmitBtn = document.getElementById("startSubmitBtn");
  const hitlDropzone = document.getElementById("hitlDropzone");
  const hitlFileInput = document.getElementById("hitlFileInput");
  const hitlFileList = document.getElementById("hitlFileList");

  const LIVE_LABELS = {
    idle: "ожидание",
    connecting: "подключение",
    live: "онлайн",
    reconnecting: "переподключение",
  };
  /** Raw status code → short RU badge; title keeps the code for debug. */
  const STATUS_LABELS = {
    DELEGATED: "Передано",
    EXCEL_EVIDENCE_READY: "Факты Excel",
    INVALID_SOURCE_FACTS_PACKET: "Пакет неполный",
    CALCULATION_DATA_READY: "Расчёт готов",
    SCHEDULE_EVIDENCE_GAP: "Не хватает полей",
    MALFORMED_EVIDENCE_GAP: "Пропуск битый",
    STALLED_EVIDENCE_LOOP: "Петля",
    EXCEL_EVIDENCE_BUDGET_EXHAUSTED: "Лимит Excel",
    BUILDER_ITERATION_BUDGET_EXHAUSTED: "Лимит Builder",
    RESUME_SCHEDULE: "Продолжаем",
    INVALID_EXCEL_EVIDENCE_SNAPSHOT: "Снимок",
    TASK_STARTED: "Создана",
    AWAITING_HUMAN: "Ждём вас",
    HUMAN_REPLY: "Ваш ответ",
    HUMAN_APPROVED: "Одобрено",
    HUMAN_REJECTED: "Отклонено",
    SCHEDULE_DRAFT_READY: "Черновик",
    VERIFIED: "Проверено",
    SUCCEEDED: "Успешно",
    COMPLETED: "Готово",
    NEEDS_INPUT: "Нужны данные",
    NEEDS_DECISION: "Нужно решение",
    NEEDS_APPROVAL: "Ждёт подтверждения",
    result_approval: "Ждёт подтверждения",
    RESULT_APPROVAL: "Ждёт подтверждения",
    pre_delegation_approval: "Согласование",
    PRE_DELEGATION_APPROVAL: "Согласование",
    ORCH_CONFLICT: "Конфликт",
    ORCH_DISPATCHED: "В оркестратор",
    ORCH_FAILED: "Сбой оркестратора",
    CONFLICT: "Конфликт",
    conflict: "Конфликт",
    planning: "План",
    PLANNING: "План",
    handoff: "Передача",
    running: "В работе",
    RUNNING: "В работе",
    new: "Новая",
    done: "Готово",
    failed: "Сбой",
    waiting_user: "Ждём вас",
    retryable_error: "Ошибка",
    RETRYABLE_ERROR: "Ошибка",
    fatal_error: "Сбой",
    FATAL_ERROR: "Сбой",
    error: "Ошибка",
    ERROR: "Ошибка",
  };
  const CYRILLIC_RE = /[А-Яа-яЁё]/;
  function statusLabel(code) {
    const raw = String(code || "").trim();
    if (!raw) return "—";
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
  let currentTaskName = "";
  let renamingTask = false;
  let feedGeneration = 0;

  let lastCaseFeed = { events: [], activity: [], status: "", objective: "", attached_files: [], state: {} };
  let workspaceView = "chat";
  let startResumeTask = null;
  let source = null;
  let feedPollTimer = null;
  let rendered = new Set();
  let gateState = null;
  let taskVersion = null;
  let awaitingHuman = false;
  let taskCatalog = [];
  let flashTimer = null;
  let startOpen = false;
  let waitTimer = null;
  let waitStartedAt = 0;
  /** @type {{ available?: boolean, filename?: string, byte_length?: number, download_path?: string } | null} */
  let scheduleArtifact = null;
  /** @type {{ summary?: string, changed_keywords?: string[], commissioning_wells?: string[], edits?: object[], include_graph_changed?: boolean } | null} */
  let semanticDiff = null;
  /** @type {{ file: File, kind: string }[]} */
  let pendingFiles = [];
  /** @type {{ file: File, kind: string }[]} */
  let hitlFiles = [];

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
        "Оркестратор не смог разобрать решение модели (JSON не по схеме). "
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
      return "Лента пустая: задача ещё не получила события от оркестратора.";
    }
    if (/conflict|error|fail|cancel|reject/.test(st)) {
      return data?.status_message
        || "Задача завершилась с ошибкой, а события в ленту не пришли. Смотрите баннер статуса выше.";
    }
    return "Лента пока пуста — ждём первые события оркестратора.";
  }

  function formatElapsed(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    if (sec < 60) return `${sec} с`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  /** HH:MM and DD.MM.YY of last turn (or task updated_at fallback) for the rail. */
  function railLastStamp(task) {
    const abs = String(task?.last_at_abs || "").trim();
    if (abs) {
      const m = abs.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2})/);
      if (m) {
        return { time: m[4], date: `${m[3]}.${m[2]}.${m[1].slice(2)}` };
      }
      const t = abs.match(/\b(\d{2}:\d{2})(?::\d{2})?\b/);
      if (t) return { time: t[1], date: "" };
    }
    const iso = String(task?.updated_at || "").trim();
    if (!iso) return { time: "", date: "" };
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { time: "", date: "" };
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yy = String(d.getFullYear()).slice(2);
    const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
    return { time, date: `${dd}.${mm}.${yy}` };
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
      || lower === "waiting_user"
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
    if (!raw || /^specialist$/i.test(raw)) return "Вы";
    if (/^mas activity user$/i.test(raw)) return "Вы";
    const labels = {
      User: "Вы",
      user: "Вы",
      Engineer: "Вы",
      human_operator: "Вы",
      "mas activity user": "Вы",
      Orchestrator: "Оркестратор",
      orchestrator: "Оркестратор",
      universal_orchestrator: "Оркестратор",
      excel_extractor: "Excel",
      calculation_agent: "Расчёт",
      schedule_builder: "Schedule",
      error_handler: "Обработчик ошибок",
    };
    return labels[raw] || raw;
  }

  function bumpFeedGeneration() {
    feedGeneration += 1;
    return feedGeneration;
  }

  function feedMatchesOpenTask(data) {
    if (startOpen || !currentTask) return false;
    if (!data || typeof data !== "object") return false;
    const id = String(data.task_id || data.case_id || "").trim();
    return !id || id === currentTask;
  }

  function syncSchema(data) {
    if (startOpen) return;
    if (window.MasSchema && typeof window.MasSchema.setFeed === "function") {
      window.MasSchema.setFeed(data || lastCaseFeed);
    }
  }

  function setWorkspaceView(mode, { persist = true } = {}) {
    workspaceView = mode === "schema" ? "schema" : "chat";
    if (workspace) {
      workspace.classList.toggle("mode-schema", workspaceView === "schema");
      workspace.classList.toggle("mode-chat", workspaceView === "chat");
    }
    if (chatView) chatView.hidden = workspaceView === "schema";
    if (schemaView) schemaView.hidden = workspaceView !== "schema";
    if (viewChatBtn) viewChatBtn.setAttribute("aria-selected", String(workspaceView === "chat"));
    if (viewSchemaBtn) viewSchemaBtn.setAttribute("aria-selected", String(workspaceView === "schema"));
    if (persist) {
      try { sessionStorage.setItem("masActivityView", workspaceView); } catch (_) { /* ignore */ }
    }
    if (workspaceView === "schema" && schemaView) {
      schemaView.focus({ preventScroll: true });
    }
  }

  function roleClass(role) {
    const label = displayRole(role);
    if (
      /^вы$/i.test(label)
      || /human|инженер|operator|^user$/i.test(label)
      || /human|инженер|operator|^user$|mas activity user/i.test(role || "")
    ) {
      return "human";
    }
    return /orchestrator/i.test(role || "") ? "orch" : "spec";
  }

  function questionText(q) {
    if (!q || typeof q !== "object") return String(q || "");
    return String(q.text || q.question || q.message || q.id || "").trim();
  }

  const MACHINE_CODE_RE = /^[A-Z][A-Z0-9_]{3,}$/;
  const GATE_GENERIC_REASON = "Проверка остановила задачу: не хватает исходных данных. Прикрепите недостающие файлы или напишите, как продолжать, и нажмите Ответить.";
  const GATE_GENERIC_ITEM = "Нужны исходные данные. Прикрепите недостающие файлы или напишите, как продолжать.";

  function looksMachineAsk(text) {
    const t = String(text || "").trim();
    if (!t) return true;
    if (MACHINE_CODE_RE.test(t)) return true;
    if (!CYRILLIC_RE.test(t)) return true;
    return false;
  }

  function fileBaseName(value) {
    const s = String(value || "").trim().replace(/\\/g, "/");
    if (!s) return "";
    const parts = s.split("/").filter(Boolean);
    return parts[parts.length - 1] || s;
  }

  function humanizeGateReason(reason, questions) {
    if (!looksMachineAsk(reason)) return String(reason || "").trim() || GATE_GENERIC_REASON;
    const blob = [reason, ...(Array.isArray(questions) ? questions.map((q) => q && (q.code || q.id)) : [])].join(" ");
    if (/INCLUDE_NOT_FOUND/.test(blob)) {
      return "Тела INCLUDE не приложены — ссылки в корне оставляем как есть на той же дате.";
    }
    if (/EXCEL_WORKBOOK_REQUIRED|xlsx|workbook/i.test(blob)) {
      return "Нет книги Excel. Прикрепите файл .xlsx к ответу.";
    }
    return GATE_GENERIC_REASON;
  }

  function humanizeQuestion(q) {
    const raw = questionText(q);
    const code = String(q && (q.code || (MACHINE_CODE_RE.test(raw) ? raw : "")) || "").trim();
    if (!looksMachineAsk(raw)) return raw;
    const path = fileBaseName(q && (q.path || q.target_file_ref));
    const from = fileBaseName(q && (q.file_ref || q.from || q.source_file_ref));
    const keyword = String(q && (q.keyword || "") || "").trim();
    const entity = String(q && (q.entity || q.well || "") || "").trim();
    if (code === "INCLUDE_NOT_FOUND" || raw === "INCLUDE_NOT_FOUND") {
      if (path) {
        return `INCLUDE «${path}» без тела${from ? `, ссылка из «${from}»` : ""}: оставляем вызов на той же дате. Приложите файл, только если нужно править его содержимое.`;
      }
      return "INCLUDE без тела: оставляем вызов на той же дате. Приложите файл, только если нужно править содержимое.";
    }
    if (code === "INCLUDE_BODY_REQUIRED" || raw === "INCLUDE_BODY_REQUIRED") {
      if (path) {
        return `Нужно тело INCLUDE «${path}»${from ? `, ссылка из «${from}»` : ""}: приложите этот .inc, иначе содержимое нечем править.`;
      }
      return "Нужно тело INCLUDE. Приложите этот .inc, иначе содержимое нечем править.";
    }
    if (code === "EXCEL_WORKBOOK_REQUIRED" || /xlsx|workbook/i.test(raw)) {
      return "Прикрепите книгу Excel (.xlsx или .xls) к ответу.";
    }
    if (code === "BASELINE_REQUIRED") {
      return "Прикрепите предыдущий schedule (.inc / .data) для режима REVISE.";
    }
    const bits = [path, from, keyword, entity].filter(Boolean);
    if (bits.length) {
      return `Нужны данные: ${bits.join(", ")}. Прикрепите файл или напишите уточнение.`;
    }
    return GATE_GENERIC_ITEM;
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
    const showComposer = !startOpen && (awaitingHuman || restartableCase);
    composer.hidden = !showComposer;
    composer.classList.toggle("armed", awaitingHuman && !startOpen);
    if (replyBtn) replyBtn.disabled = !awaitingHuman || startOpen;
    if (restartBtn) {
      restartBtn.hidden = !restartableCase || startOpen;
      restartBtn.disabled = !restartableCase || startOpen || !currentTask;
    }
  }

  function setRestartable(flag) {
    restartableCase = Boolean(flag);
    setComposerArmed(awaitingHuman);
  }

  function clearWorkspaceView({ titleLabel = "Выберите задачу", composing = false } = {}) {
    bumpFeedGeneration();
    closeStream();
    setLive("idle");
    rendered = new Set();
    thread.innerHTML = "";
    hideNotFound();
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    renderStatusBanner(null, null);
    setScheduleArtifact(null);
    setSemanticDiff(null);
    setComposerArmed(false);
    setTaskHeader(null);
    if (titleText) titleText.textContent = titleLabel;
    else title.textContent = titleLabel;
    if (composing) {
      empty.hidden = false;
      empty.textContent = "Опишите задачу в форме ниже — чат и схема этой задачи появятся после создания.";
    } else {
      empty.hidden = true;
      empty.textContent = "";
    }
    lastCaseFeed = { events: [], activity: [], status: "", objective: "", attached_files: [], state: {} };
    if (window.MasSchema && typeof window.MasSchema.reset === "function") {
      window.MasSchema.reset();
    }
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
      clearWorkspaceView({ titleLabel: "Новая задача", composing: true });
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
    startHint.hidden = true;
    startHint.textContent = "";
    newTaskBtn.classList.toggle("active-start", startOpen);
    setComposerArmed(awaitingHuman);
    if (startOpen) {
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

  function addToBucket(bucket, fileList) {
    const incoming = Array.from(fileList || []);
    for (const file of incoming) {
      if (!file || !file.name) continue;
      const kind = classifyFile(file);
      if (kind === "other") {
        showFlash(`Неизвестный тип файла: ${file.name}`);
        continue;
      }
      if (kind === "excel" && bucket.some((f) => f.kind === "excel")) {
        showFlash("Можно прикрепить только один Excel workbook.");
        continue;
      }
      if (kind === "surface" && bucket.some((f) => f.kind === "surface")) {
        showFlash("Можно прикрепить только один surface файл.");
        continue;
      }
      if (bucket.some((f) => f.file.name === file.name && f.file.size === file.size)) continue;
      if (bucket.length >= 40) {
        showFlash("Слишком много файлов (макс. 40).");
        break;
      }
      bucket.push({ file, kind });
    }
  }

  function renderFileChips(listEl, bucket, onMutate) {
    if (!listEl) return;
    listEl.innerHTML = "";
    if (!bucket.length) {
      listEl.hidden = true;
      return;
    }
    listEl.hidden = false;
    bucket.forEach((entry, index) => {
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
        bucket.splice(index, 1);
        onMutate();
      });
      li.append(kind, name, remove);
      listEl.append(li);
    });
  }

  function renderPendingFiles() {
    renderFileChips(startFileList, pendingFiles, renderPendingFiles);
    syncScheduleRootField();
  }

  function renderHitlFiles() {
    renderFileChips(hitlFileList, hitlFiles, renderHitlFiles);
  }

  function addFiles(fileList) {
    addToBucket(pendingFiles, fileList);
    renderPendingFiles();
  }

  function addHitlFiles(fileList) {
    addToBucket(hitlFiles, fileList);
    renderHitlFiles();
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
    const GATE_KIND_LABELS = {
      needs_input: "Нужны данные",
      needs_decision: "Нужно решение",
      needs_approval: "Ждёт подтверждения",
      result_approval: "Ждёт подтверждения",
      pre_delegation_approval: "Согласование",
      human_gate: "Запрос",
    };
    const rawKind = String(gateState.kind || "human_gate");
    gateKind.textContent = GATE_KIND_LABELS[rawKind] || GATE_KIND_LABELS[rawKind.toLowerCase()] || "Запрос";
    gateKind.title = rawKind;
    gateMeta.textContent = [
      version != null ? `v${version}` : gateState.expected_version != null ? `v${gateState.expected_version}` : null,
    ].filter(Boolean).join(" · ");
    const questions = Array.isArray(gateState.questions) ? gateState.questions : [];
    gateReason.textContent = humanizeGateReason(gateState.reason, questions) || "Ожидается ваше решение.";
    gateQuestions.innerHTML = "";
    const seenAsk = new Set();
    for (const q of questions) {
      const text = humanizeQuestion(q);
      if (!text || seenAsk.has(text)) continue;
      seenAsk.add(text);
      const li = document.createElement("li");
      li.append(document.createTextNode(text));
      gateQuestions.append(li);
    }
    if (armed) {
      composerHint.hidden = true;
      composerHint.textContent = "";
      const kind = String(gateState.kind || "").toLowerCase();
      humanResponse.placeholder = (kind === "result_approval" || kind === "needs_approval")
        ? "Своими словами: выпуск принять — или что проверить / доработать"
        : "Уточнения, недостающие данные, что ещё поправить…";
    } else {
      composerHint.hidden = false;
      composerHint.textContent = "Ответ пока не требуется. Можно подождать или открыть другую задачу.";
    }
  }

  function turnEventKind(turn) {
    return String(turn.event_type || turn.details?.kind || turn.stage || turn.status || "").trim();
  }

  function turnKicker(eventKind, laneDir, isHitlAsk) {
    if (/hitl\.answered|TASK_STARTED/i.test(eventKind)) return "Ответ";
    if (/hitl\.request/i.test(eventKind) || isHitlAsk) return "Запрос";
    if (/^agent\.handoff$/i.test(eventKind) || /^case\.created$/i.test(eventKind)) return "Передача";
    if (/^agent\.result$/i.test(eventKind)) return "Результат";
    if (/^agent\.failed$|^case\.failed$|^system\.node_error$/i.test(eventKind)) return "Ошибка";
    if (/^agent\.(accepted|progress)$/i.test(eventKind)) return "Сообщение";
    if (/^orchestrator\.|^case\.finished$/i.test(eventKind)) return "Статус";
    if (laneDir === "out") return "Передача";
    if (laneDir === "in") return "Результат";
    return "Статус";
  }

  function renderTurn(turn, { animate = true } = {}) {
    const id = `${turn.turn_id}:${turn.at}:${turn.status}:${turn.duration_ms || ""}:${turn.kind || ""}`;
    if (rendered.has(id)) return;
    rendered.add(id);
    empty.hidden = true;

    const li = document.createElement("li");
    const eventKind = turnEventKind(turn);
    const fromRole = displayRole(turn.from?.role || "Orchestrator");
    const toRole = displayRole(turn.to?.role || turn.from?.role || "User");
    let laneDir = String(turn.lane_dir || turn.details?.lane_dir || "").toLowerCase()
      || (fromRole === toRole ? "none" : "out");
    if (/^agent\.(accepted|progress)$/i.test(eventKind)) laneDir = "none";
    const sameParty = laneDir === "none" && fromRole === toRole;
    const isHuman = turn.kind === "hitl" || /human|^вы$/i.test(fromRole) || /^HUMAN_/.test(turn.status || "") || turn.status === "TASK_STARTED";
    const isHitlAsk = /NEEDS_|AWAITING_HUMAN|RESULT_APPROVAL|PRE_DELEGATION/i.test(String(turn.status || "")) || String(turn.stage || "") === "hitl";
    li.className = `turn outcome-${turn.outcome || "info"}${isHuman ? " human" : ""} dir-${laneDir}`;
    li._masTurn = turn;

    const who = document.createElement("div");
    who.className = `who ${roleClass(turn.from?.role || fromRole)} dir-${laneDir}`;
    who.title = sameParty ? fromRole : (laneDir === "in" ? `${toRole} → ${fromRole}` : `${fromRole} → ${toRole}`);
    const kicker = document.createElement("span");
    kicker.className = "who-kicker";
    kicker.textContent = turnKicker(eventKind, laneDir, isHitlAsk);
    const fromEl = document.createElement("span");
    fromEl.className = "role from" + (/^вы$/i.test(fromRole) ? " you" : "");
    fromEl.textContent = fromRole;
    who.append(kicker, fromEl);
    if (!sameParty) {
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.setAttribute("aria-hidden", "true");
      const toEl = document.createElement("span");
      toEl.className = "role to" + (/^вы$/i.test(toRole) ? " you" : "");
      toEl.textContent = toRole;
      who.append(arrow, toEl);
    }
    if (turn.duration_label) {
      const dur = document.createElement("span");
      dur.className = "duration";
      dur.textContent = turn.duration_label;
      dur.title = /handoff|case\.created/i.test(eventKind)
        ? "Время работы до этой передачи"
        : "Время до этого события";
      who.append(dur);
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const brief = document.createElement("p");
    brief.className = "brief";
    brief.textContent = turn.brief || turn.text || "";
    bubble.append(brief);
    if (turn.handoff_message) {
      const handoff = document.createElement("p");
      handoff.className = "text";
      handoff.textContent = turn.handoff_message;
      bubble.append(handoff);
    }
    if (String(turn.status || turn.event_type || "") === "system.node_error") {
      li.classList.add("node-error");
    }

    if (shouldShowMutedText(turn)) {
      const text = document.createElement("p");
      text.className = "text muted-line";
      text.textContent = turn.text;
      bubble.append(text);
    }

    if (Array.isArray(turn.chips) && turn.chips.length) {
      const HIDDEN_CHIP_IDS = new Set([
        "action", "requested_by", "gate_id", "gate_kind", "file_count", "backend",
        "kind", "agent_id",
      ]);
      const visible = turn.chips.filter((c) => c && !HIDDEN_CHIP_IDS.has(String(c.id || "")));
      if (visible.length) {
        const chips = document.createElement("div");
        chips.className = "chips";
        for (const chip of visible) {
          const el = document.createElement("span");
          el.className = "chip";
          const b = document.createElement("b");
          b.textContent = chip.label;
          el.append(b, document.createTextNode(` ${String(chip.value)}`));
          chips.append(el);
        }
        bubble.append(chips);
      }
    }

    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = [turn.at_abs || turn.at, turn.stage].filter(Boolean).join(" · ");
    bubble.append(meta);

    li.append(who, bubble);
    
    // Auto-scroll logic: only scroll if we were already near the bottom
    // We check the scroll position *before* appending
    const transcript = thread.closest('.transcript');
    let isAtBottom = true;
    if (transcript) {
      isAtBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 50;
    }
    thread.append(li);
    if (isAtBottom && transcript) {
      transcript.scrollTop = transcript.scrollHeight;
    }
  }

  async function refreshRail({ durable = false } = {}) {
    const res = await fetch("/cases");
    const data = await res.json();
    taskCatalog = Array.isArray(data.tasks) ? data.tasks : [];

    const list = railList || taskRail;
    list.innerHTML = "";
    if (!taskCatalog.length) {
      if (newTaskBtn) newTaskBtn.classList.toggle("active-start", startOpen);
      return data;
    }

    for (const task of taskCatalog) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = task.task_id === currentTask ? "active" : "";
      const name = String(task.task_name || "").trim();
      const id = document.createElement("span");
      id.className = name ? "id named" : "id";
      id.textContent = name || task.task_id;
      const meta = document.createElement("span");
      meta.className = "meta";
      const stLabel = statusLabel(task.status || task.last_status || "");
      const parts = [stLabel || "—"];
      if (task.turn_count != null) parts.push(`${task.turn_count} turn`);
      const stamp = railLastStamp(task);
      if (stamp.time) parts.push(stamp.time);
      if (stamp.date) parts.push(stamp.date);
      meta.textContent = parts.join(" · ");
      btn.title = [
        name && task.task_id !== name ? `${name}\n${task.task_id}` : task.task_id,
        task.title || "",
        parts.join(" · "),
        task.last_at_abs || task.updated_at || "",
      ].filter(Boolean).join("\n");
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
      
      if (task.task_id === currentTask) {
        setTimeout(() => btn.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" }), 50);
      }
    }
    if (newTaskBtn) newTaskBtn.classList.toggle("active-start", startOpen);
    return data;
  }

  function closeStream() {
    if (feedPollTimer) {
      clearInterval(feedPollTimer);
      feedPollTimer = null;
    }
    if (source) {
      source.close();
      source = null;
    }
  }

  function mergeFeed(data, { animateTurns = false } = {}) {
    if (!feedMatchesOpenTask(data)) return;
    lastCaseFeed = { ...lastCaseFeed, ...data };
    if (Array.isArray(data.events)) lastCaseFeed.events = data.events;
    applyFeedMeta(data);
    const turns = Array.isArray(data.activity) ? data.activity : [];
    for (const turn of turns) renderTurn(turn, { animate: animateTurns });
    if (turns.length) {
      empty.hidden = true;
      empty.textContent = "";
    }
    syncSchema(lastCaseFeed);
  }

  async function pollFeed({ durable = false } = {}) {
    if (!currentTask || startOpen) return;
    const taskId = currentTask;
    const gen = feedGeneration;
    try {
      const res = await fetch(
        `/cases/${encodeURIComponent(taskId)}`,
      );
      if (!res.ok) return;
      const data = await res.json();
      if (currentTask !== taskId || feedGeneration !== gen || startOpen) return;
      mergeFeed(data, { animateTurns: true });
    } catch (_) { /* keep SSE as source of truth */ }
  }

  function attachLive(taskId) {
    if (source) {
      source.close();
      source = null;
    }
    if (feedPollTimer) {
      clearInterval(feedPollTimer);
      feedPollTimer = null;
    }
    source = new EventSource(`/cases/${encodeURIComponent(taskId)}/stream`);
    source.onopen = () => setLive("live");
    source.onerror = () => setLive("reconnecting");
    source.onmessage = (ev) => {
      if (startOpen || currentTask !== taskId) return;
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "ping") return;
        if (msg.type === "snapshot") {
          hideNotFound();
          lastCaseFeed = { ...msg };
          applyFeedMeta(msg);
          const turns = Array.isArray(msg.activity) ? msg.activity : [];
          for (const turn of turns) renderTurn(turn, { animate: false });
          if (!turns.length && !thread.querySelector("li.turn")) {
            empty.hidden = false;
            empty.textContent = emptyFeedMessage(msg);
          }
          syncSchema(lastCaseFeed);
        } else if (msg.type === "turn") {
          renderTurn(msg.turn, { animate: true });
          applyFeedMeta(msg);
          if (msg.event && msg.event.event_id != null) {
            const events = Array.isArray(lastCaseFeed.events) ? lastCaseFeed.events.slice() : [];
            if (!events.some((row) => row && row.event_id === msg.event.event_id)) events.push(msg.event);
            lastCaseFeed.events = events;
          }
          if (msg.turn) {
            const activity = Array.isArray(lastCaseFeed.activity) ? lastCaseFeed.activity : [];
            lastCaseFeed.activity = activity.concat([msg.turn]);
          }
          if (msg.status) lastCaseFeed.status = msg.status;
          syncSchema(lastCaseFeed);
        } else if (msg.type === "gate") {
          applyFeedMeta(msg);
        }
      } catch (_) { /* ignore malformed SSE */ }
    };
    feedPollTimer = setInterval(() => pollFeed({ durable: false }), 2500);
  }

  function attachedFilesFromFeed(data) {
    if (!data || typeof data !== "object") return [];
    if (Array.isArray(data.attached_files) && data.attached_files.length) {
      return data.attached_files.map((f) => String(f || "").trim()).filter(Boolean);
    }
    const turns = Array.isArray(data.activity) ? data.activity : [];
    for (const turn of turns) {
      if (!turn || typeof turn !== "object") continue;
      const details = turn.details && typeof turn.details === "object" ? turn.details : null;
      if (!details) continue;
      const started =
        String(turn.status || "").toUpperCase() === "TASK_STARTED" || details.action === "start";
      if (!started) continue;
      if (Array.isArray(details.files)) {
        return details.files.map((f) => String(f || "").trim()).filter(Boolean);
      }
      if (typeof details.files === "string" && details.files.trim()) {
        return details.files.split(",").map((s) => s.trim()).filter(Boolean);
      }
    }
    return [];
  }

  function renderRequest(objective, attached) {
    const text = String(objective || "").trim();
    const files = Array.isArray(attached)
      ? attached.map((f) => String(f || "").trim()).filter(Boolean)
      : [];
    if (!text && !files.length) {
      requestText.textContent = "";
      if (requestFiles) {
        requestFiles.textContent = "";
        requestFiles.hidden = true;
      }
      requestPanel.hidden = true;
      return;
    }
    requestText.textContent = text;
    if (requestFiles) {
      if (files.length) {
        requestFiles.textContent = files.join(" · ");
        requestFiles.hidden = false;
      } else {
        requestFiles.textContent = "";
        requestFiles.hidden = true;
      }
    }
    requestPanel.hidden = false;
  }

  function catalogTaskName(taskId) {
    const row = (taskCatalog || []).find((item) => item && item.task_id === taskId);
    return String(row?.task_name || "").trim();
  }

  function setTaskHeader(taskId, status, opts = {}) {
    const awaiting = Object.prototype.hasOwnProperty.call(opts, "awaiting")
      ? Boolean(opts.awaiting)
      : awaitingHuman;
    if (Object.prototype.hasOwnProperty.call(opts, "task_name")) {
      currentTaskName = String(opts.task_name || "").trim();
    }
    if (!taskId) {
      currentTaskName = "";
      renamingTask = false;
      title.classList.remove("task-line", "is-renaming");
      if (titleText) titleText.textContent = "Выберите задачу";
      else title.textContent = "Выберите задачу";
      setStatusDot("idle");
      if (title) title.removeAttribute("title");
      if (renameTaskBtn) renameTaskBtn.hidden = true;
      if (renameTaskInput) {
        renameTaskInput.hidden = true;
        renameTaskInput.value = "";
      }
      return;
    }
    title.classList.add("task-line");
    const st = String(status || "").trim() || "—";
    const stRu = statusLabel(st);
    const name = currentTaskName || catalogTaskName(taskId);
    const ident = name || taskId;
    const prefix = name ? "task_name" : "task_id";
    const line = `${prefix}: ${ident} · ${stRu}${stRu !== st ? ` (${st})` : ""}`;
    if (!renamingTask) {
      if (titleText) titleText.textContent = line;
      else title.textContent = line;
    }
    if (title) title.title = name && name !== taskId ? `${name}\n${taskId}` : taskId;
    setStatusDot(statusTone(st, awaiting));
    if (renameTaskBtn) renameTaskBtn.hidden = false;
  }

  function beginRenameTask() {
    if (!currentTask || !renameTaskInput || renamingTask) return;
    renamingTask = true;
    title.classList.add("is-renaming");
    renameTaskInput.hidden = false;
    renameTaskInput.value = currentTaskName || catalogTaskName(currentTask) || "";
    renameTaskInput.placeholder = "Название задачи";
    if (renameTaskBtn) renameTaskBtn.hidden = true;
    renameTaskInput.focus();
    renameTaskInput.select();
  }

  function cancelRenameTask() {
    if (!renamingTask) return;
    renamingTask = false;
    title.classList.remove("is-renaming");
    if (renameTaskInput) {
      renameTaskInput.hidden = true;
      renameTaskInput.value = "";
    }
    if (currentTask) {
      setTaskHeader(currentTask, lastCaseFeed.status, {
        awaiting: awaitingHuman,
        task_name: currentTaskName,
      });
    }
  }

  async function saveRenameTask() {
    if (!currentTask || !renameTaskInput || !renamingTask) return;
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
      if (!res.ok) {
        showFlash(data.detail || `Не удалось переименовать задачу (${res.status}).`);
        return;
      }
      currentTaskName = String(data.task_name || "").trim();
      renamingTask = false;
      title.classList.remove("is-renaming");
      renameTaskInput.hidden = true;
      setTaskHeader(taskId, data.status, { task_name: currentTaskName });
      await refreshRail();
    } catch (err) {
      showFlash(`Не удалось переименовать задачу: ${err}`);
    } finally {
      if (renameTaskInput) renameTaskInput.disabled = false;
    }
  }

  function paintScheduleDownloadHead() {
    if (!scheduleDownloadHead) return;
    const art = scheduleArtifact;
    if (!art || !art.download_path) {
      scheduleDownloadHead.hidden = true;
      scheduleDownloadHead.removeAttribute("href");
      return;
    }
    const name = String(art.filename || "schedule.inc");
    const kb = art.byte_length ? ` · ${Math.max(1, Math.round(art.byte_length / 1024))} KB` : "";
    scheduleDownloadHead.hidden = false;
    scheduleDownloadHead.href = art.download_path;
    scheduleDownloadHead.setAttribute("download", name);
    scheduleDownloadHead.textContent = `Скачать результат${kb}`;
    scheduleDownloadHead.title = `Скачать результат SCHEDULE (${name})`;
  }

  function setScheduleArtifact(meta) {
    const art = meta && typeof meta === "object" && meta.available && meta.download_path ? meta : null;
    scheduleArtifact = art;
    paintScheduleDownloadHead();
  }

  function editLine(edit) {
    if (!edit || typeof edit !== "object") return "";
    if (edit.summary) return String(edit.summary);
    const parts = [edit.keyword, edit.well || edit.entity, edit.operation || edit.op, edit.message]
      .map((x) => (x == null ? "" : String(x).trim()))
      .filter(Boolean);
    return parts.join(" · ");
  }

  function renderSemanticDiff() {
    if (!diffExpander || !diffBody) return;
    const diff = semanticDiff;
    const keywords = Array.isArray(diff?.changed_keywords) ? diff.changed_keywords.filter(Boolean) : [];
    const wells = Array.isArray(diff?.commissioning_wells) ? diff.commissioning_wells.filter(Boolean) : [];
    const edits = Array.isArray(diff?.edits) ? diff.edits : [];
    const summary = String(diff?.summary || "").trim();
    const hasContent = Boolean(summary || keywords.length || wells.length || edits.length || diff?.include_graph_changed);
    if (!hasContent) {
      diffExpander.hidden = true;
      diffBody.replaceChildren();
      return;
    }
    diffExpander.hidden = false;
    diffBody.replaceChildren();

    if (summary) {
      const p = document.createElement("p");
      p.className = "diff-summary";
      p.textContent = summary;
      diffBody.append(p);
    }
    if (keywords.length) {
      const row = document.createElement("div");
      row.className = "diff-row";
      const label = document.createElement("span");
      label.className = "diff-label";
      label.textContent = "Keywords";
      const chips = document.createElement("div");
      chips.className = "diff-chips";
      for (const kw of keywords) {
        const chip = document.createElement("span");
        chip.className = "diff-chip";
        chip.textContent = kw;
        chips.append(chip);
      }
      row.append(label, chips);
      diffBody.append(row);
    }
    if (wells.length) {
      const row = document.createElement("div");
      row.className = "diff-row";
      const label = document.createElement("span");
      label.className = "diff-label";
      label.textContent = "Скважины";
      const chips = document.createElement("div");
      chips.className = "diff-chips";
      for (const w of wells) {
        const chip = document.createElement("span");
        chip.className = "diff-chip";
        chip.textContent = w;
        chips.append(chip);
      }
      row.append(label, chips);
      diffBody.append(row);
    }
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

  function setSemanticDiff(diff) {
    semanticDiff = diff && typeof diff === "object" ? diff : null;
    renderSemanticDiff();
  }

  function applyFeedMeta(data) {
    if (Object.prototype.hasOwnProperty.call(data, "task_name")) {
      currentTaskName = String(data.task_name || "").trim();
    } else if (data.state && typeof data.state === "object" && data.state.task_name) {
      currentTaskName = String(data.state.task_name || "").trim();
    }
    if (currentTask) {
      setTaskHeader(currentTask, data.status, { awaiting: data.awaiting_human, task_name: currentTaskName });
    }
    if (
      Object.prototype.hasOwnProperty.call(data, "objective")
      || Object.prototype.hasOwnProperty.call(data, "attached_files")
      || Array.isArray(data.activity)
    ) {
      const objective = Object.prototype.hasOwnProperty.call(data, "objective")
        ? data.objective
        : (requestText.textContent || null);
      renderRequest(objective, attachedFilesFromFeed(data));
    }
    renderStatusBanner(data.status, data.status_message || data.message);
    setRestartable(Boolean(data.restartable) || Boolean(data.human_gate?.restartable) || String(data.status || "").toLowerCase() === "retryable_error");
    if (data.version != null) taskVersion = data.version;
    else if (data.human_gate?.expected_version != null) taskVersion = data.human_gate.expected_version;
    renderGate(data.human_gate ?? data.gate ?? null, {
      status: data.status,
      version: data.version,
      awaiting: data.awaiting_human,
    });
    if (Object.prototype.hasOwnProperty.call(data, "schedule_artifact")) {
      setScheduleArtifact(data.schedule_artifact);
    }
    if (Object.prototype.hasOwnProperty.call(data, "semantic_diff")) {
      setSemanticDiff(data.semantic_diff);
    }
  }

  function showNotFound(taskId) {
    currentTask = null;
    currentTaskName = "";
    closeStream();
    setLive("idle");
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = true;
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setComposerArmed(false);
    setScheduleArtifact(null);
    setSemanticDiff(null);
    title.classList.remove("task-line");
    if (titleText) titleText.textContent = "Задача не найдена";
    else title.textContent = "Задача не найдена";
    setStatusDot("error");
    if (notFoundId) notFoundId.textContent = taskId ? `id: ${taskId}` : "";
    if (notFound) notFound.hidden = false;
    setWorkspaceView("chat", { persist: false });
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
      "Не удалось загрузить задачу. Возможно, сервер временно недоступен. Попробуйте обновить страницу.";
    if (taskId) setTaskHeader(taskId, "ошибка");
    renderRequest(null);
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setComposerArmed(false);
    setSemanticDiff(null);
    showFlash(message || "Не удалось загрузить задачу.");
  }

  async function openTask(taskId) {
    if (!taskId) return;
    startResumeTask = null;
    setStartOpen(false, { resume: false });
    hideNotFound();
    currentTask = taskId;
    currentTaskName = catalogTaskName(taskId);
    const gen = bumpFeedGeneration();
    history.replaceState({}, "", `/t/${encodeURIComponent(taskId)}`);
    rendered = new Set();
    thread.innerHTML = "";
    empty.hidden = false;
    empty.textContent = "Загружаем задачу…";
    setTaskHeader(taskId, "…", { task_name: currentTaskName });
    renderRequest(null);
    renderStatusBanner(null, null);
    closeStream();
    setLive("connecting");
    renderGate(null, { awaiting: false });
    setRestartable(false);
    setScheduleArtifact(null);
    setSemanticDiff(null);
    showFlash("");
    attachLive(taskId);

    try {
      const snap = await fetch(`/cases/${encodeURIComponent(taskId)}`);
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
      if (currentTask !== taskId || feedGeneration !== gen) return;
      hideNotFound();
      mergeFeed(data, { animateTurns: false });
      if (!Array.isArray(data.activity) || !data.activity.length) {
        empty.hidden = false;
        empty.textContent = emptyFeedMessage(data);
        renderStatusBanner(
          data.status,
          data.status_message
            || (["running", "new"].includes(String(data.status || "").toLowerCase())
              ? "Оркестратор ещё не прислал события — схема и лента обновятся сами."
              : null),
        );
      }
      try {
        const durableRes = await fetch(`/cases/${encodeURIComponent(taskId)}`);
        if (durableRes.ok) {
          const feed = await durableRes.json();
          if (feed && currentTask === taskId && feedGeneration === gen) {
            mergeFeed(feed, { animateTurns: true });
            if (feed.hydrate?.ok === false) {
              const msg = formatHydrateError(feed.hydrate.error || "hydrate_failed");
              if (msg) showFlash(msg, { sticky: true });
            } else if (feed.hydrate?.truncated) {
              showFlash("История обрезана: показаны только самые свежие сообщения.");
            }
          }
        }
      } catch (_) { /* memory snapshot already shown */ }
    } catch (_) {
      showLoadError(taskId, "Сеть недоступна при загрузке задачи.");
      await refreshRail();
      return;
    } finally {
      hideWait();
    }

    await refreshRail();
  }

  async function submitHitl() {
    if (!currentTask) {
      showFlash("Сначала выберите задачу в списке слева.");
      return;
    }
    const by = REQUESTED_BY;
    const responseText = humanResponse.value.trim();
    if (!responseText && !hitlFiles.length) {
      humanResponse.focus();
      showFlash("Нужен текст ответа или вложение.");
      return;
    }

    composer.classList.add("busy");
    composerHint.hidden = false;
    composerHint.textContent = "Отправляем ответ…";
    pollFeed({ durable: false });
    try {
      const form = new FormData();
      form.append("action", "reply");
      form.append("requested_by", by);
      if (responseText) form.append("human_response", responseText);
      if (gateState?.gate_id) form.append("gate_id", gateState.gate_id);
      form.append(
        "question_id",
        gateState?.gate_id || gateState?.questions?.[0]?.question_id || "Q-1",
      );
      form.append("answer", responseText || "(файл)");
      const ver = gateState?.expected_version ?? taskVersion;
      if (ver != null && ver !== "") form.append("expected_version", String(ver));
      for (const entry of hitlFiles) {
        if (entry.kind === "excel") form.append("file", entry.file, entry.file.name);
        else if (entry.kind === "surface") form.append("surface_file", entry.file, entry.file.name);
        else if (entry.kind === "trajectory") form.append("trajectory_files", entry.file, entry.file.name);
        else form.append("schedule_files", entry.file, entry.file.name);
      }
      const res = await fetch(`/cases/${encodeURIComponent(currentTask)}/answer`, {
        method: "POST",
        body: form,
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
        semantic_diff: data.semantic_diff,
        status_message: data.orchestrator?.message || data.status_message,
      });
      humanResponse.value = "";
      hitlFiles = [];
      renderHitlFiles();
      showFlash("Ответ отправлен.", { ok: true });
      const snap = await fetch(`/cases/${encodeURIComponent(currentTask)}`);
      if (snap.ok) {
        mergeFeed(await snap.json(), { animateTurns: true });
      }
      await refreshRail();
    } catch (_) {
      showFlash("Сеть недоступна при отправке ответа.");
      composerHint.hidden = false;
      composerHint.textContent = "Не удалось отправить. Проверьте сеть и соединение с сервером.";
    } finally {
      composer.classList.remove("busy");
    }
  }

  async function submitStart(e) {
    e.preventDefault();
    const by = REQUESTED_BY;
    const description = taskDescription.value.trim();
    if (!description) {
      taskDescription.focus();
      showFlash("Нужно описание задачи.");
      return;
    }

    const form = new FormData();
    form.append("task_description", description);
    form.append("requested_by", by);
    const givenName = taskNameInput ? taskNameInput.value.trim() : "";
    if (givenName) form.append("task_name", givenName);

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
    showWait("Записываем задачу…");
    try {
      const res = await fetch("/cases", {
        method: "POST",
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
        refreshRail();
        return;
      }
      pendingFiles = [];
      renderPendingFiles();
      taskDescription.value = "";
      if (taskNameInput) taskNameInput.value = "";
      scheduleRoot.value = "";
      startHint.hidden = true;
      startHint.textContent = "";
      setStartOpen(false, { resume: false });
      showFlash(
        data.task_id
          ? `Задача ${data.task_name || data.task_id} создана — оркестратор работает, лента обновляется сама.`
          : "Задача создана — оркестратор работает, лента обновляется сама.",
        { ok: true },
      );
      hideWait();
      refreshRail();
      await openTask(data.case_id || data.task_id);
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
      if (startHint.textContent === "Создаём задачу…") {
        startHint.hidden = true;
        startHint.textContent = "";
      }
    }
  }

  if (restartBtn) {
    restartBtn.addEventListener("click", async () => {
      if (!currentTask) {
        showFlash("Сначала выберите задачу с case_id.");
        return;
      }
      const by = REQUESTED_BY;
      if (!window.confirm(`Перезапустить case ${currentTask} с сохранёнными входными данными?`)) {
        return;
      }
      composer.classList.add("busy");
      showWait("Перезапускаем case…");
      try {
        const res = await fetch(`/cases/${encodeURIComponent(currentTask)}/run`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            action: "retry",
            requested_by: by,
            human_response: "restart",
            gate_id: gateState?.gate_id || null,
            expected_version: gateState?.expected_version ?? taskVersion ?? null,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`;
          showFlash(detail.includes(currentTask) ? detail : `${detail} (case_id=${currentTask})`);
          return;
        }
        if (data.ok === false || data.skipped || data.accepted === false) {
          const detail = data.reason || data.status || "skipped";
          showFlash(`Перезапуск не выполнен: ${detail} (case_id=${currentTask})`);
          return;
        }
        applyFeedMeta({
          status: data.orchestrator?.status || data.status,
          version: data.orchestrator?.version || data.version,
          awaiting_human: data.awaiting_human,
          restartable: data.restartable,
          human_gate: data.human_gate,
          status_message: data.orchestrator?.message,
          schedule_artifact: data.schedule_artifact,
          semantic_diff: data.semantic_diff,
        });
        if (data.turn) renderTurn(data.turn);
        showFlash(`Перезапуск case ${currentTask} принят.`);
        await pollFeed();
        await refreshRail();
      } catch (err) {
        showFlash(`Не удалось перезапустить case ${currentTask}: ${err}`);
      } finally {
        composer.classList.remove("busy");
        hideWait();
      }
    });
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    submitHitl();
  });

  newTaskBtn.addEventListener("click", () => setStartOpen(!startOpen));

  if (renameTaskBtn) renameTaskBtn.addEventListener("click", beginRenameTask);
  if (renameTaskInput) {
    renameTaskInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        saveRenameTask();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        cancelRenameTask();
      }
    });
    renameTaskInput.addEventListener("blur", () => {
      if (renamingTask && !renameTaskInput.disabled) saveRenameTask();
    });
  }

  function formatHydrateError(err) {
    const text = String(err || "");
    if (/webhook .* is not registered|not registered/i.test(text) || /HTTP 404/.test(text)) {
      return "Система пока не готова. Убедитесь, что все рабочие процессы активированы.";
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
      if (reloadFeed && currentTask) {
        const taskId = currentTask;
        const gen = feedGeneration;
        const snap = await fetch(`/cases/${encodeURIComponent(taskId)}`);
        if (snap.ok) {
          const body = await snap.json();
          if (currentTask !== taskId || feedGeneration !== gen || startOpen) return;
          lastCaseFeed = body;
          rendered = new Set();
          thread.innerHTML = "";
          setTaskHeader(currentTask, body.status, { awaiting: body.awaiting_human });
          applyFeedMeta(body);
          for (const turn of body.activity || []) renderTurn(turn, { animate: false });
          syncSchema(body);
          if (body.hydrate?.ok === false) {
            showFlash(formatHydrateError(body.hydrate.error || "hydrate_failed"));
          } else if (body.hydrate?.truncated) {
            showFlash("История обрезана: показаны только самые свежие сообщения.");
          }
        } else if (flash) {
          showFlash(`Не удалось обновить ленту (${snap.status}).`);
        }
        const data = await refreshRail({ durable: !snap.ok });
        return data;
      }
      const data = await refreshRail({ durable: true });
      if (data?.hydrate?.error) {
        if (flash) showFlash(formatHydrateError(data.hydrate.error));
      }
      return data;
    } catch (_) {
      if (flash) showFlash("Не удалось обновить ленту задач.");
      return null;
    }
  }

  if (brandHome) {
    brandHome.addEventListener("click", async (e) => {
      e.preventDefault();
      startResumeTask = null;
      setStartOpen(false, { resume: false });
      currentTask = null;
      history.replaceState({}, "", "/");
      clearWorkspaceView();
      brandHome.classList.add("busy");
      try {
        await hydrateFromDataTables({ flash: false, reloadFeed: false });
      } finally {
        brandHome.classList.remove("busy");
      }
    });
  }

  startCancelBtn.addEventListener("click", () => setStartOpen(false));
  startComposer.addEventListener("submit", submitStart);
  if (viewChatBtn) viewChatBtn.addEventListener("click", () => setWorkspaceView("chat"));
  if (viewSchemaBtn) viewSchemaBtn.addEventListener("click", () => setWorkspaceView("schema"));
  try {
    if (sessionStorage.getItem("masActivityView") === "schema") {
      setWorkspaceView("schema", { persist: false });
    }
  } catch (_) { /* ignore */ }

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

  function wireDropzone(zone, input, onFiles) {
    if (!zone || !input) return;
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", () => {
      onFiles(input.files);
      input.value = "";
    });
    ["dragenter", "dragover"].forEach((evt) => {
      zone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      zone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("dragover");
      });
    });
    zone.addEventListener("drop", (e) => {
      onFiles(e.dataTransfer?.files);
    });
  }
  wireDropzone(hitlDropzone, hitlFileInput, addHitlFiles);

  const initial = pathTaskId();
  if (initial) {
    openTask(initial);
  } else {
    hydrateFromDataTables({ flash: false, reloadFeed: false });
  }
})();
