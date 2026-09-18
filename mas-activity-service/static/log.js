/* «Лог» — developer view of one case (Режим разработчика).
 *
 * Source of truth is GET /cases/{id}/log (app/case_log.py): every event with level, source, step,
 * n8n execution link and the raw payload. This module only renders and filters; it re-fetches the
 * log (debounced) whenever the SSE stream reports a new event, so the view never diverges from the
 * server. Exposed as window.MasLog; wired from app.js.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const LEVELS = ["error", "warn", "info", "debug"];
  const LEVEL_LABEL = { error: "Ошибки", warn: "Внимание", info: "События", debug: "Отладка" };
  const SOURCE_LABEL = { engineer: "инженер", orchestrator: "оркестратор", n8n: "n8n" };
  const REFRESH_DEBOUNCE_MS = 350;

  const root = $("logView");
  if (!root) return;
  const stepsList = $("logSteps");
  const body = $("logBody");
  const summaryEl = $("logSummary");
  const emptyEl = $("logEmpty");
  const sourceSelect = $("logSource");
  const searchInput = $("logSearch");
  const followInput = $("logFollow");
  const downloadLink = $("logDownload");
  const chips = Array.from(root.querySelectorAll(".log-chip[data-level]"));

  const state = {
    caseId: null,
    log: null,
    levels: new Set(LEVELS),
    source: "",
    query: "",
    expanded: new Set(),
    pendingFocus: null,
    refreshTimer: null,
    loading: null,
    visible: false,
  };

  // ------------------------------------------------------------------ helpers
  const text = (v) => (v == null ? "" : String(v));
  const esc = (v) => text(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function clock(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return text(iso).slice(11, 23) || text(iso);
    const p = (n, w = 2) => String(n).padStart(w, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
  }

  function duration(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return "";
    const n = Number(ms);
    if (n < 1000) return `${n} мс`;
    if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} с`;
    const m = Math.floor(n / 60000);
    const s = Math.round((n % 60000) / 1000);
    return m < 60 ? `${m} мин ${s} с` : `${Math.floor(m / 60)} ч ${m % 60} мин`;
  }

  function sourceLabel(source) {
    const s = text(source);
    if (SOURCE_LABEL[s]) return SOURCE_LABEL[s];
    return s.startsWith("agent:") ? s.slice(6) : s || "—";
  }

  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  function pretty(value) {
    try { return JSON.stringify(value, null, 2); } catch (_) { return text(value); }
  }

  const PROMPT_ROLE = { system: "система", user: "запрос", assistant: "модель", tool: "инструмент", omitted: "…" };

  function promptMessages(preview) {
    if (Array.isArray(preview)) return preview;
    if (preview && typeof preview === "object" && Array.isArray(preview.messages)) return preview.messages;
    return null;
  }

  function promptHtml(preview) {
    const msgs = promptMessages(preview);
    if (msgs && msgs.length) {
      return `<div class="log-prompt">${msgs.map((m) => {
        const role = String((m && m.role) || "");
        const label = PROMPT_ROLE[role] || role || "сообщение";
        if (role === "omitted") {
          return `<div class="log-prompt-msg" data-role="omitted"><span class="log-prompt-role">${esc(label)}</span><p class="log-prompt-text">пропущено ${esc(String((m && m.count) || 0))} сообщ.</p></div>`;
        }
        const extra = m && m.tool_calls ? ` · ${m.tool_calls} вызов.` : (m && m.tool_call_id ? ` · ${m.tool_call_id}` : "");
        return `<div class="log-prompt-msg" data-role="${esc(role)}"><span class="log-prompt-role">${esc(label)}${esc(extra)}</span><pre class="log-prompt-text">${esc(text(m && m.content))}</pre></div>`;
      }).join("")}</div>`;
    }
    if (typeof preview === "string" && preview.trim()) {
      return `<pre class="log-json">${esc(preview)}</pre>`;
    }
    return "";
  }

  function detailWithoutPrompt(detail) {
    if (!detail || typeof detail !== "object") return detail;
    if (detail.prompt_preview == null) return detail;
    const out = { ...detail };
    delete out.prompt_preview;
    return out;
  }

  // ------------------------------------------------------------------ filtering
  function matches(record) {
    if (!state.levels.has(record.level)) return false;
    if (state.source && record.source !== state.source) return false;
    if (state.query) {
      const hay = `${record.title} ${record.message} ${record.kind} ${record.task_id || ""} ${record.execution_id || ""} ${pretty(record.detail)}`.toLowerCase();
      if (!hay.includes(state.query)) return false;
    }
    return true;
  }

  // ------------------------------------------------------------------ rendering
  function renderSummary(log) {
    const s = log.summary || {};
    const items = [
      { label: "шагов", value: s.steps || 0 },
      { label: "передач агентам", value: s.handoffs || 0 },
      { label: "вызовов инструментов", value: s.tool_calls || 0 },
      { label: "запросов в базу", value: s.kb_calls || 0 },
      { label: "пустой RAG", value: s.rag_empty || 0, tone: s.rag_empty ? "warn" : "" },
      { label: "усечений LLM", value: s.llm_truncated || 0, tone: s.llm_truncated ? "warn" : "" },
      { label: "вопросов инженеру", value: s.hitl_rounds || 0 },
      { label: "предупреждений", value: s.warnings || 0, tone: s.warnings ? "warn" : "" },
      { label: "ошибок", value: s.errors || 0, tone: s.errors ? "error" : "" },
    ];
    const agents = Array.isArray(s.agents) && s.agents.length ? `<span class="log-sum-item"><span class="log-sum-label">агенты</span><span class="log-sum-agents">${s.agents.map((a) => `<code>${esc(a)}</code>`).join(" ")}</span></span>` : "";
    const time = s.duration_ms != null ? `<span class="log-sum-item"><span class="log-sum-label">длительность</span><b>${esc(duration(s.duration_ms))}</b></span>` : "";
    summaryEl.innerHTML = items.map((it) => `<span class="log-sum-item" data-tone="${it.tone || ""}"><b>${it.value}</b><span class="log-sum-label">${it.label}</span></span>`).join("") + agents + time;
  }

  function renderCounts(records) {
    const counts = { error: 0, warn: 0, info: 0, debug: 0 };
    for (const r of records) if (counts[r.level] != null) counts[r.level] += 1;
    for (const chip of chips) {
      const level = chip.dataset.level;
      const el = chip.querySelector(".log-chip-count");
      if (el) el.textContent = String(counts[level] || 0);
      chip.classList.toggle("is-on", state.levels.has(level));
      chip.setAttribute("aria-pressed", String(state.levels.has(level)));
    }
  }

  function renderSources(records) {
    const current = state.source;
    const sources = Array.from(new Set(records.map((r) => r.source))).sort();
    sourceSelect.innerHTML = `<option value="">Все источники</option>` + sources.map((s) => `<option value="${esc(s)}">${esc(sourceLabel(s))}</option>`).join("");
    sourceSelect.value = sources.includes(current) ? current : "";
    state.source = sourceSelect.value;
  }

  function rowHtml(record) {
    const seq = text(record.seq);
    const open = state.expanded.has(seq);
    const link = record.execution_url
      ? `<a class="log-exec" href="${esc(record.execution_url)}" target="_blank" rel="noopener" title="Открыть выполнение в n8n">n8n ↗</a>`
      : record.execution_id ? `<span class="log-exec is-plain" title="Идентификатор выполнения n8n">#${esc(record.execution_id)}</span>` : "";
    const dur = record.duration_ms != null ? `<span class="log-dur">${esc(duration(record.duration_ms))}</span>` : "";
    const gap = record.gap_ms != null && Number(record.gap_ms) > 0 ? `<span class="log-gap">+${esc(duration(record.gap_ms))}</span>` : "";
    const message = record.message && record.message !== record.title ? `<p class="log-msg">${esc(record.message)}</p>` : "";
    const task = record.task_id ? `<span class="log-task" title="Задача агента">${esc(record.task_id)}</span>` : "";
    const cards = record.detail && Array.isArray(record.detail.cards) ? record.detail.cards : [];
    const cardChips = cards.length
      ? `<div class="log-cards">${cards.map((c) => {
          const id = c && c.knowledge_id ? String(c.knowledge_id) : "";
          const score = c && c.rrf_score != null && c.rrf_score !== "" ? ` · ${Number(c.rrf_score).toFixed(3)}` : "";
          const branches = Array.isArray(c && c.branches) && c.branches.length ? ` · ${c.branches.join(",")}` : "";
          return `<span class="log-card">${esc(id)}${esc(score)}${esc(branches)}</span>`;
        }).join("")}</div>`
      : "";
    const prompt = promptHtml(record.detail && record.detail.prompt_preview);
    const jsonDetail = detailWithoutPrompt(record.detail);
    const jsonBlock = jsonDetail && Object.keys(jsonDetail).length ? `<pre class="log-json">${esc(pretty(jsonDetail))}</pre>` : "";
    const detailBlock = prompt + jsonBlock || `<p class="log-msg is-muted">Без деталей.</p>`;
    return `
      <li class="log-row" data-seq="${esc(seq)}" data-level="${esc(record.level)}" data-kind="${esc(record.kind)}">
        <button type="button" class="log-row-head" aria-expanded="${open}">
          <time class="log-time" datetime="${esc(record.at || "")}">${esc(clock(record.at))}</time>
          <span class="log-dot" aria-label="${esc(LEVEL_LABEL[record.level] || record.level)}"></span>
          <span class="log-source" data-source="${esc(record.source)}">${esc(sourceLabel(record.source))}</span>
          <span class="log-title">${esc(record.title)}</span>
          ${task}${gap}${dur}${link}
          <span class="log-kind">${esc(record.kind)}</span>
        </button>
        <div class="log-row-detail" ${open ? "" : "hidden"}>
          ${message}
          ${cardChips}
          ${detailBlock}
          <div class="log-row-actions">
            <button type="button" class="btn btn-quiet log-copy" data-copy="${esc(seq)}">Копировать JSON</button>
          </div>
        </div>
      </li>`;
  }

  function stepHtml(group, rows) {
    const decision = group.decision ? esc(group.decision) : group.step === 0 ? "постановка задачи" : "без решения";
    const meta = [
      group.tool_calls ? `${group.tool_calls} ${plural(group.tool_calls, "вызов", "вызова", "вызовов")}` : "",
      group.errors ? `${group.errors} ${plural(group.errors, "ошибка", "ошибки", "ошибок")}` : "",
      group.duration_ms != null ? duration(group.duration_ms) : "",
    ].filter(Boolean).join(" · ");
    return `
      <li class="log-step" data-step="${group.step}" data-level="${esc(group.level)}">
        <details open>
          <summary class="log-step-head">
            <span class="log-step-no">${esc(group.title)}</span>
            <span class="log-step-decision">${decision}</span>
            <span class="log-step-meta">${esc(meta)}</span>
          </summary>
          <ol class="log-rows">${rows}</ol>
        </details>
      </li>`;
  }

  function render() {
    const log = state.log;
    if (!log) {
      stepsList.innerHTML = "";
      summaryEl.innerHTML = "";
      emptyEl.hidden = false;
      emptyEl.textContent = state.caseId ? "Загружаю лог…" : "Откройте задачу — здесь появится её лог.";
      return;
    }
    const records = Array.isArray(log.records) ? log.records : [];
    renderSummary(log);
    renderCounts(records);
    renderSources(records);
    const wasAtBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 48;
    const groups = new Map((log.steps || []).map((g) => [g.step, g]));
    const byStep = new Map();
    for (const r of records) {
      if (!matches(r)) continue;
      const key = typeof r.step === "number" ? r.step : 0;
      if (!byStep.has(key)) byStep.set(key, []);
      byStep.get(key).push(r);
    }
    const html = Array.from(byStep.keys()).sort((a, b) => a - b).map((step) => {
      const group = groups.get(step) || { step, title: step === 0 ? "Старт" : `Шаг ${step}`, level: "info" };
      return stepHtml(group, byStep.get(step).map(rowHtml).join(""));
    }).join("");
    stepsList.innerHTML = html;
    emptyEl.hidden = Boolean(html);
    if (!html) emptyEl.textContent = records.length ? "Под фильтр ничего не попало." : "Событий пока нет.";
    if (state.pendingFocus != null) {
      const target = stepsList.querySelector(`.log-row[data-seq="${CSS.escape(String(state.pendingFocus))}"]`);
      state.pendingFocus = null;
      if (target) {
        target.classList.add("is-focus");
        target.scrollIntoView({ block: "center" });
        return;
      }
    }
    if (followInput.checked && (wasAtBottom || state.justLoaded)) body.scrollTop = body.scrollHeight;
    state.justLoaded = false;
  }

  // ------------------------------------------------------------------ data
  async function load(caseId) {
    if (!caseId) return null;
    state.caseId = caseId;
    downloadLink.href = `/cases/${encodeURIComponent(caseId)}/log?format=ndjson`;
    downloadLink.setAttribute("download", `${caseId}.log.ndjson`);
    const promise = fetch(`/cases/${encodeURIComponent(caseId)}/log`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null);
    state.loading = promise;
    const log = await promise;
    if (state.loading !== promise || state.caseId !== caseId) return null;
    state.loading = null;
    if (log) {
      state.justLoaded = !state.log;
      state.log = log;
      render();
    } else if (!state.log) {
      emptyEl.hidden = false;
      emptyEl.textContent = "Не удалось загрузить лог задачи.";
    }
    return log;
  }

  function scheduleRefresh() {
    if (!state.caseId || !state.visible) return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => { state.refreshTimer = null; load(state.caseId); }, REFRESH_DEBOUNCE_MS);
  }

  // ------------------------------------------------------------------ public API
  const api = {
    /** Case changed: forget the old log; load lazily when the tab becomes visible. */
    setCase(caseId) {
      if (state.caseId === caseId) return;
      state.caseId = caseId || null;
      state.log = null;
      state.expanded.clear();
      state.pendingFocus = null;
      render();
      if (state.visible && caseId) load(caseId);
    },
    /** The «Лог» tab is shown/hidden (drives lazy loading and live refresh). */
    setVisible(visible) {
      state.visible = Boolean(visible);
      if (state.visible && state.caseId && !state.log) load(state.caseId);
      else if (state.visible && state.caseId) scheduleRefresh();
    },
    /** SSE reported new events (turn / trace / meta) — re-read the log soon. */
    notify() { scheduleRefresh(); },
    /** Chat → «в логе»: expand and scroll to the record of an event. */
    focus(seq) {
      if (seq == null) return;
      state.expanded.add(String(seq));
      state.pendingFocus = String(seq);
      if (state.log) render();
      else if (state.caseId) load(state.caseId);
    },
    clear() { api.setCase(null); },
  };
  window.MasLog = api;

  // ------------------------------------------------------------------ events
  for (const chip of chips) {
    chip.addEventListener("click", () => {
      const level = chip.dataset.level;
      if (state.levels.has(level)) state.levels.delete(level); else state.levels.add(level);
      if (!state.levels.size) LEVELS.forEach((l) => state.levels.add(l));
      render();
    });
  }
  sourceSelect.addEventListener("change", () => { state.source = sourceSelect.value; render(); });
  searchInput.addEventListener("input", () => { state.query = searchInput.value.trim().toLowerCase(); render(); });
  stepsList.addEventListener("click", (e) => {
    const copy = e.target.closest(".log-copy");
    if (copy) {
      const record = (state.log?.records || []).find((r) => text(r.seq) === copy.dataset.copy);
      if (record && navigator.clipboard) {
        navigator.clipboard.writeText(pretty(record)).then(() => { copy.textContent = "Скопировано"; setTimeout(() => { copy.textContent = "Копировать JSON"; }, 1200); }).catch(() => {});
      }
      return;
    }
    if (e.target.closest("a.log-exec")) return;
    const head = e.target.closest(".log-row-head");
    if (!head) return;
    const row = head.closest(".log-row");
    const seq = row.dataset.seq;
    const detail = row.querySelector(".log-row-detail");
    const open = detail.hidden;
    detail.hidden = !open;
    head.setAttribute("aria-expanded", String(open));
    row.classList.remove("is-focus");
    if (open) state.expanded.add(seq); else state.expanded.delete(seq);
  });
  render();
})();
