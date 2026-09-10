/* Agents page: the agent_registry as the field engineer sees it.
 * Reads GET /agents, writes PUT /agents/{agent_id} (partial rows). No agent is hardcoded here —
 * the same rows drive the orchestrator's planner (Phase 2 registry). */
(() => {
  const list = document.getElementById("agentList");
  const listEmpty = document.getElementById("listEmpty");
  const hint = document.getElementById("agentsHint");
  const flashEl = document.getElementById("flash");
  const addBtn = document.getElementById("addAgentBtn");
  const createPanel = document.getElementById("createPanel");
  const createId = document.getElementById("createId");
  const createTitle = document.getElementById("createTitle");
  const createWhen = document.getElementById("createWhen");
  const createKind = document.getElementById("createKind");
  const createTarget = document.getElementById("createTarget");
  const createSave = document.getElementById("createSave");
  const createCancel = document.getElementById("createCancel");

  let agents = [];
  let openId = null;
  let flashTimer = null;

  function showFlash(message, { ok = false } = {}) {
    if (flashTimer) clearTimeout(flashTimer);
    const text = String(message || "").trim();
    flashEl.hidden = !text;
    flashEl.textContent = text;
    flashEl.classList.toggle("flash-ok", Boolean(ok));
    if (text) flashTimer = setTimeout(() => { flashEl.hidden = true; flashEl.textContent = ""; }, ok ? 4000 : 9000);
  }

  function detailMessage(data, fallback) {
    if (typeof data?.detail === "string" && data.detail.trim()) return data.detail;
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
    return fallback;
  }

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }

  const csv = (items) => (Array.isArray(items) ? items.join(", ") : "");
  const parseCsv = (text) => String(text || "").split(/[,\n;]+/).map((s) => s.trim()).filter(Boolean);
  const pretty = (obj) => JSON.stringify(obj && typeof obj === "object" ? obj : {}, null, 2);

  function invokeText(agent) {
    const inv = agent.invoke || {};
    if (inv.kind === "n8n_workflow") return inv.workflow_id ? `сценарий n8n ${inv.workflow_id}` : "сценарий n8n не привязан";
    if (inv.kind === "http") return inv.url ? `сервис ${inv.url}` : "адрес сервиса не задан";
    return "вызов не настроен";
  }

  function isUnbound(agent) {
    const inv = agent.invoke || {};
    if (inv.kind === "n8n_workflow") return !inv.workflow_id || /REPLACE/i.test(String(inv.workflow_id));
    if (inv.kind === "http") return !inv.url;
    return true;
  }

  function badges(agent) {
    const out = [];
    out.push(`<span class="ag-badge">${escapeHtml(agent.agent_id)}</span>`);
    out.push(`<span class="ag-badge ${agent.enabled ? "" : "is-off"}">${agent.enabled ? "включён" : "выключен"}</span>`);
    if (isUnbound(agent)) out.push('<span class="ag-badge is-unbound">вызов не привязан</span>');
    if (agent.hitl_policy === "never") out.push('<span class="ag-badge">без вопросов инженеру</span>');
    return out.join("");
  }

  function cardHtml(agent) {
    const open = agent.agent_id === openId;
    const inv = agent.invoke || {};
    return `
      <article class="kb-card ${open ? "is-open" : ""}" data-agent="${escapeHtml(agent.agent_id)}">
        <button type="button" class="kb-card-head" data-toggle>
          <div class="kb-card-title-row">
            <h3 class="kb-card-title">${escapeHtml(agent.title)}</h3>
            <span class="kb-type">${escapeHtml(inv.kind === "http" ? "HTTP-сервис" : "сценарий n8n")}</span>
          </div>
          <div class="ag-badges">${badges(agent)}</div>
          <p class="kb-preview">${escapeHtml(agent.when_to_use || "Без описания — планировщик не будет знать, когда звать этого агента.")}</p>
          <p class="kb-meta">${escapeHtml(invokeText(agent))} · нужно: ${escapeHtml(csv(agent.input_required) || "—")} · отдаёт: ${escapeHtml(csv(agent.output_provides) || "—")}</p>
        </button>
        ${open ? bodyHtml(agent) : ""}
      </article>`;
  }

  function bodyHtml(agent) {
    const inv = agent.invoke || {};
    const kind = inv.kind === "http" ? "http" : "n8n_workflow";
    return `
      <div class="kb-body">
        <label class="ag-toggle"><input type="checkbox" data-field="enabled" ${agent.enabled ? "checked" : ""} /> Агент включён (планировщик видит его)</label>
        <div class="ag-grid">
          <label class="field"><span>Название</span><input type="text" data-field="title" value="${escapeHtml(agent.title)}" /></label>
          <label class="field"><span>Вопросы инженеру</span>
            <select data-field="hitl_policy">
              <option value="agent_asks" ${agent.hitl_policy !== "never" ? "selected" : ""}>агент может спрашивать</option>
              <option value="never" ${agent.hitl_policy === "never" ? "selected" : ""}>никогда</option>
            </select></label>
          <label class="field ag-wide"><span>Когда звать <em class="optional">это читает планировщик</em></span><textarea data-field="when_to_use" rows="3">${escapeHtml(agent.when_to_use)}</textarea></label>
          <label class="field"><span>Вызов</span>
            <select data-field="invoke_kind">
              <option value="n8n_workflow" ${kind === "n8n_workflow" ? "selected" : ""}>Сценарий n8n</option>
              <option value="http" ${kind === "http" ? "selected" : ""}>HTTP-сервис</option>
            </select></label>
          <label class="field"><span>${kind === "http" ? "Адрес сервиса" : "Номер сценария в n8n"} <em class="optional">${kind === "http" ? "из Runtime Config, можно в фигурных скобках" : "из адресной строки после импорта"}</em></span>
            <input type="text" data-field="invoke_target" value="${escapeHtml(kind === "http" ? inv.url || "" : inv.workflow_id || "")}" /></label>
          <label class="field"><span>Нужно на входе <em class="optional">через запятую</em></span><input type="text" data-field="input_required" value="${escapeHtml(csv(agent.input_required))}" /></label>
          <label class="field"><span>Отдаёт <em class="optional">через запятую</em></span><input type="text" data-field="output_provides" value="${escapeHtml(csv(agent.output_provides))}" /></label>
          <label class="field"><span>Схема входа <em class="optional">описание для оркестратора</em></span><textarea class="ag-mono" data-field="input_schema">${escapeHtml(pretty(agent.input_schema))}</textarea></label>
          <label class="field"><span>Схема выхода <em class="optional">описание результата</em></span><textarea class="ag-mono" data-field="output_schema">${escapeHtml(pretty(agent.output_schema))}</textarea></label>
        </div>
        <div class="kb-actions">
          <span class="kb-meta">версия ${escapeHtml(agent.version || "1")}</span>
          <button type="button" class="btn btn-primary" data-save>Сохранить</button>
        </div>
      </div>`;
  }

  function render() {
    list.innerHTML = agents.map(cardHtml).join("");
    listEmpty.hidden = agents.length > 0;
    const enabled = agents.filter((a) => a.enabled).length;
    const unbound = agents.filter((a) => a.enabled && isUnbound(a)).length;
    hint.textContent = agents.length
      ? `${agents.length} агентов в реестре, ${enabled} включено${unbound ? `, ${unbound} без привязанного вызова` : ""}. Оркестратор читает эти строки на каждом шаге.`
      : "Реестр пуст.";
  }

  function readForm(card, agent) {
    const get = (name) => card.querySelector(`[data-field="${name}"]`);
    const kind = get("invoke_kind").value;
    const target = get("invoke_target").value.trim();
    const patch = {
      title: get("title").value.trim(),
      when_to_use: get("when_to_use").value.trim(),
      hitl_policy: get("hitl_policy").value,
      enabled: get("enabled").checked,
      input_required: parseCsv(get("input_required").value),
      output_provides: parseCsv(get("output_provides").value),
      invoke: kind === "http" ? { kind, url: target } : { kind, workflow_id: target, workflow_name: agent.invoke?.workflow_name || `Agent — ${get("title").value.trim()}` },
    };
    for (const key of ["input_schema", "output_schema"]) {
      const text = get(key).value.trim();
      if (!text) { patch[key] = {}; continue; }
      try { patch[key] = JSON.parse(text); } catch (_) { throw new Error(`Поле «${key === "input_schema" ? "Схема входа" : "Схема выхода"}» должно быть корректным описанием`); }
    }
    return patch;
  }

  async function putAgent(agentId, patch) {
    const res = await fetch(`/agents/${encodeURIComponent(agentId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailMessage(data, `Не удалось сохранить агента (${res.status})`));
    return data.agent;
  }

  async function load() {
    const res = await fetch("/agents");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(detailMessage(data, `Реестр недоступен (${res.status})`));
    agents = Array.isArray(data.agents) ? data.agents : [];
    render();
  }

  list.addEventListener("click", async (event) => {
    const card = event.target.closest(".kb-card");
    if (!card) return;
    const agentId = card.dataset.agent;
    if (event.target.closest("[data-toggle]")) {
      openId = openId === agentId ? null : agentId;
      render();
      return;
    }
    if (event.target.closest("[data-save]")) {
      const agent = agents.find((a) => a.agent_id === agentId);
      try {
        const patch = readForm(card, agent);
        const saved = await putAgent(agentId, patch);
        agents = agents.map((a) => (a.agent_id === agentId ? { ...a, ...saved } : a));
        render();
        showFlash(`Агент «${saved.title}» сохранён`, { ok: true });
      } catch (err) {
        showFlash(err.message || String(err));
      }
    }
  });

  list.addEventListener("change", (event) => {
    // Switching the invoke kind relabels the target field.
    if (event.target.matches('[data-field="invoke_kind"]')) {
      const card = event.target.closest(".kb-card");
      const label = card.querySelector('[data-field="invoke_target"]').closest(".field").querySelector("span");
      label.innerHTML = event.target.value === "http"
        ? 'URL сервиса <em class="optional">поле Runtime Config в фигурных скобках</em>'
        : 'Id workflow в n8n <em class="optional">из адресной строки после импорта</em>';
    }
  });

  addBtn.addEventListener("click", () => { createPanel.hidden = false; createId.focus(); });
  createCancel.addEventListener("click", () => { createPanel.hidden = true; });
  createSave.addEventListener("click", async () => {
    const agentId = createId.value.trim();
    if (!/^[a-z][a-z0-9_]{2,63}$/.test(agentId)) { showFlash("Идентификатор — латиница в нижнем регистре, цифры и подчёркивание"); return; }
    const kind = createKind.value;
    const target = createTarget.value.trim();
    const title = createTitle.value.trim();
    try {
      const saved = await putAgent(agentId, {
        title,
        when_to_use: createWhen.value.trim(),
        invoke: kind === "http" ? { kind, url: target } : { kind, workflow_id: target, workflow_name: `Agent — ${title}` },
        enabled: true,
      });
      createPanel.hidden = true;
      [createId, createTitle, createWhen, createTarget].forEach((el) => { el.value = ""; });
      openId = saved.agent_id;
      await load();
      showFlash(`Агент «${saved.title}» добавлен`, { ok: true });
    } catch (err) {
      showFlash(err.message || String(err));
    }
  });

  load().catch((err) => { hint.textContent = err.message || String(err); showFlash(err.message || String(err)); });
})();
