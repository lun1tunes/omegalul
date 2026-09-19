(() => {
  const agentSelect = document.getElementById("agentSelect");
  const agentHint = document.getElementById("agentHint");
  const cardList = document.getElementById("cardList");
  const listEmpty = document.getElementById("listEmpty");
  const flashEl = document.getElementById("flash");
  const addBtn = document.getElementById("addBtn");
  const ingestBtn = document.getElementById("ingestBtn");
  const purgeSuperseded = document.getElementById("purgeSuperseded");
  const createPanel = document.getElementById("createPanel");
  const createId = document.getElementById("createId");
  const createType = document.getElementById("createType");
  const createTitle = document.getElementById("createTitle");
  const createText = document.getElementById("createText");
  const createTagFields = document.getElementById("createTagFields");
  const createSave = document.getElementById("createSave");
  const createCancel = document.getElementById("createCancel");
  const agentTabs = document.getElementById("agentTabs");
  const kbSearch = document.getElementById("kbSearch");
  const kbCount = document.getElementById("kbCount");

  const TAG_GROUPS = [
    {
      key: "keywords",
      label: "Ключевые слова SCHEDULE",
      hint: "Имена ключевых слов из руководства tNavigator, по которым поиск находит карточку — например DATES, WCONPROD. Необязательно.",
    },
    {
      key: "topics",
      label: "Темы",
      hint: "О чём карточка простыми словами: ввод скважин, группы, перфорация. Необязательно.",
    },
    {
      key: "task_patterns",
      label: "Как инженер формулирует задачу",
      hint: "Типичные фразы из постановки («сдвинуть даты ввода», «перепривязать группу»), чтобы карточка находилась по запросу.",
    },
  ];
  const TYPE_LABELS = {
    keyword_instruction: "Ключевое слово SCHEDULE",
    worked_example: "Пример",
    protocol_instruction: "Правило работы",
    routing_card: "Как разбирать задачу",
    capability_instruction: "Что умеет агент",
    injection_template: "Служебный шаблон",
  };
  const STATUS_LABELS = {
    active: "актуальна",
    current: "актуальна",
    superseded: "заменена",
    draft: "черновик",
  };

  function typeLabel(value) {
    const key = String(value || "").trim();
    return TYPE_LABELS[key] || key.replace(/_/g, " ") || "карточка";
  }
  function statusLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    return STATUS_LABELS[key] || String(value || "");
  }

  let namespaces = [];
  let currentBase = "";
  let flashTimer = null;
  let openId = null;
  let editingId = null;
  let draft = null;
  let detailCache = new Map();
  let createDraft = { keywords: [], topics: [], task_patterns: [] };
  let activeTag = "";
  let searchTimer = null;

  function showFlash(message, { ok = false, ms } = {}) {
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
    if (ms === 0) {
      return;
    }
    flashTimer = setTimeout(() => {
      flashEl.hidden = true;
      flashEl.textContent = "";
      flashEl.classList.remove("flash-ok");
      flashTimer = null;
    }, typeof ms === "number" ? ms : (ok ? 4000 : 9000));
  }

  function detailMessage(data, fallback) {
    if (typeof data?.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
    if (Array.isArray(data?.detail) && data.detail.length) {
      const first = data.detail[0];
      if (typeof first === "string" && first.trim()) return first;
      if (first && typeof first.msg === "string" && first.msg.trim()) return first.msg;
    }
    if (typeof data?.message === "string" && data.message.trim()) {
      return data.message;
    }
    return fallback;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderMarkdown(src) {
    const raw = String(src || "");
    if (!(window.marked && typeof window.marked.parse === "function")) {
      return `<pre>${escapeHtml(raw)}</pre>`;
    }
    try {
      window.marked.setOptions({ breaks: true, gfm: true });
      const html = window.marked.parse(raw);
      const tpl = document.createElement("template");
      tpl.innerHTML = html;
      tpl.content.querySelectorAll("script,iframe,object,embed").forEach((el) => el.remove());
      tpl.content.querySelectorAll("*").forEach((el) => {
        [...el.attributes].forEach((attr) => {
          if (/^on/i.test(attr.name) || (attr.name === "href" && /^\s*javascript:/i.test(attr.value))) {
            el.removeAttribute(attr.name);
          }
        });
      });
      return tpl.innerHTML;
    } catch (_) {
      return `<pre>${escapeHtml(raw)}</pre>`;
    }
  }

  function currentNamespace() {
    return namespaces.find((item) => item.id === currentBase) || null;
  }

  /** Agent tabs mirror the (accessible) select: one click = one knowledge base. */
  function renderAgentTabs() {
    if (!agentTabs) return;
    agentTabs.innerHTML = "";
    for (const ns of namespaces) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "kb-tab" + (ns.id === currentBase ? " is-active" : "");
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", String(ns.id === currentBase));
      const label = document.createElement("span");
      label.className = "kb-tab-label";
      label.textContent = ns.label || ns.id;
      const count = document.createElement("span");
      count.className = "kb-tab-count";
      count.textContent = ns.document_count != null ? String(ns.document_count) : "";
      btn.dataset.base = ns.id;
      btn.append(label, count);
      btn.addEventListener("click", () => {
        if (agentSelect) agentSelect.value = ns.id;
        if (ns.id !== currentBase) activeTag = "";
        loadDocuments(ns.id);
      });
      agentTabs.append(btn);
    }
  }

  function syncAgentTabs() {
    if (!agentTabs) return;
    for (const btn of agentTabs.querySelectorAll(".kb-tab")) {
      const active = btn.dataset.base === currentBase;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", String(active));
    }
  }

  function documentsUrl(base, { q = "", tag = "" } = {}) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (tag) params.set("tag", tag);
    const qs = params.toString();
    return `/v1/knowledge/${encodeURIComponent(base)}${qs ? `?${qs}` : ""}`;
  }

  function setTagFilter(tag) {
    const next = String(tag || "").trim();
    activeTag = activeTag && activeTag.toLowerCase() === next.toLowerCase() ? "" : next;
    loadDocuments(currentBase);
  }

  function applySearch() {
    if (!kbSearch) return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTimer = null;
      loadDocuments(currentBase);
    }, 180);
  }

  function syncAddButton() {
    addBtn.disabled = !currentBase;
  }

  function fillCreateTypes() {
    const ns = currentNamespace();
    const types = (ns && ns.knowledge_types) || [];
    createType.innerHTML = "";
    for (const t of types) {
      const opt = document.createElement("option");
      opt.textContent = typeLabel(t);
      opt.value = t;
      createType.append(opt);
    }
    if (!types.length) {
      const opt = document.createElement("option");
      opt.value = "keyword_instruction";
      opt.textContent = typeLabel("keyword_instruction");
      createType.append(opt);
    }
  }

  function makeTagEditor(group, values, onChange) {
    const section = document.createElement("section");
    section.className = "kb-tag-group";

    const label = document.createElement("div");
    label.className = "kb-tag-label";
    label.textContent = group.label;

    const hint = document.createElement("p");
    hint.className = "kb-tag-hint";
    hint.textContent = group.hint;

    const row = document.createElement("div");
    row.className = "kb-chips kb-chips-edit";

    const renderChips = () => {
      row.querySelectorAll(".kb-chip").forEach((el) => el.remove());
      for (const tag of values) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `kb-chip ${group.key === "keywords" ? "kw" : ""}`;
        chip.title = "Удалить";
        chip.textContent = `${tag} ×`;
        chip.addEventListener("click", () => {
          const idx = values.indexOf(tag);
          if (idx >= 0) values.splice(idx, 1);
          onChange([...values]);
          renderChips();
        });
        row.insertBefore(chip, addWrap);
      }
    };

    const addWrap = document.createElement("span");
    addWrap.className = "kb-tag-add";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "добавить…";
    input.setAttribute("aria-label", `Добавить в ${group.label}`);
    const addBtnLocal = document.createElement("button");
    addBtnLocal.type = "button";
    addBtnLocal.className = "btn btn-quiet kb-tag-add-btn";
    addBtnLocal.textContent = "+";
    const commit = () => {
      const tag = input.value.trim();
      if (!tag) return;
      if (!values.some((v) => v.toLowerCase() === tag.toLowerCase())) {
        values.push(tag);
        onChange([...values]);
      }
      input.value = "";
      renderChips();
    };
    addBtnLocal.addEventListener("click", commit);
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        commit();
      }
    });
    addWrap.append(input, addBtnLocal);
    row.append(addWrap);

    renderChips();
    section.append(label, hint, row);
    return section;
  }

  function makeTagView(doc) {
    const wrap = document.createElement("div");
    wrap.className = "kb-tag-fields";
    for (const group of TAG_GROUPS) {
      const values = Array.isArray(doc[group.key]) ? doc[group.key] : [];
      const section = document.createElement("section");
      section.className = "kb-tag-group";
      const label = document.createElement("div");
      label.className = "kb-tag-label";
      label.textContent = group.label;
      const hint = document.createElement("p");
      hint.className = "kb-tag-hint";
      hint.textContent = group.hint;
      const row = document.createElement("div");
      row.className = "kb-chips";
      if (!values.length) {
        const empty = document.createElement("span");
        empty.className = "kb-tag-empty";
        empty.textContent = "—";
        row.append(empty);
      } else {
        for (const tag of values) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = `kb-chip ${group.key === "keywords" ? "kw" : ""} is-filter` + (activeTag && activeTag.toLowerCase() === String(tag).toLowerCase() ? " is-active" : "");
        el.textContent = tag;
        el.addEventListener("click", (ev) => {
          ev.stopPropagation();
          setTagFilter(tag);
        });
          row.append(el);
        }
      }
      section.append(label, row);
      wrap.append(section);
    }
    return wrap;
  }

  function chipRowCompact(doc) {
    const wrap = document.createElement("div");
    wrap.className = "kb-tag-fields kb-tag-fields-compact";
    for (const group of TAG_GROUPS) {
      const values = Array.isArray(doc[group.key]) ? doc[group.key] : [];
      if (!values.length) continue;
      const section = document.createElement("section");
      section.className = "kb-tag-group";
      const label = document.createElement("div");
      label.className = "kb-tag-label";
      label.textContent = group.label;
      const row = document.createElement("div");
      row.className = "kb-chips";
      for (const tag of values.slice(0, group.key === "keywords" ? 10 : 6)) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = `kb-chip ${group.key === "keywords" ? "kw" : ""} is-filter` + (activeTag && activeTag.toLowerCase() === String(tag).toLowerCase() ? " is-active" : "");
        el.textContent = tag;
        el.addEventListener("click", (ev) => {
          ev.stopPropagation();
          setTagFilter(tag);
        });
        row.append(el);
      }
      const more = values.length - (group.key === "keywords" ? 10 : 6);
      if (more > 0) {
        const el = document.createElement("span");
        el.className = "kb-chip";
        el.textContent = `+${more}`;
        row.append(el);
      }
      section.append(label, row);
      wrap.append(section);
    }
    return wrap;
  }

  function renderCreateTagFields() {
    createTagFields.innerHTML = "";
    for (const group of TAG_GROUPS) {
      createTagFields.append(
        makeTagEditor(group, createDraft[group.key], (next) => {
          createDraft[group.key] = next;
        }),
      );
    }
  }

  function openCreatePanel() {
    if (!currentBase) {
      showFlash("Сначала выберите агента.");
      return;
    }
    createDraft = { keywords: [], topics: [], task_patterns: [] };
    createId.value = "";
    createTitle.value = "";
    createText.value = "";
    fillCreateTypes();
    renderCreateTagFields();
    createPanel.hidden = false;
    createId.focus();
  }

  function closeCreatePanel() {
    createPanel.hidden = true;
  }

  async function loadNamespaces() {
    const res = await fetch("/v1/knowledge/namespaces");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showFlash(typeof data.detail === "string" ? data.detail : "Не удалось загрузить список агентов.");
      agentSelect.innerHTML = '<option value="">Нет данных</option>';
      return;
    }
    namespaces = Array.isArray(data.namespaces) ? data.namespaces : [];
    agentSelect.innerHTML = "";
    if (!namespaces.length) {
      agentSelect.innerHTML = '<option value="">Нет данных</option>';
      agentHint.textContent = "Нет баз знаний агентов.";
      syncAddButton();
      return;
    }
    for (const ns of namespaces) {
      const opt = document.createElement("option");
      opt.value = ns.id;
      opt.textContent = ns.label || ns.id;
      agentSelect.append(opt);
    }
    const first = namespaces[0].id;
    agentSelect.value = first;
    currentBase = first;
    renderAgentTabs();
    await loadDocuments(first);
  }

  async function fetchDetail(base, id) {
    const key = `${base}/${id}`;
    if (detailCache.has(key)) return detailCache.get(key);
    const res = await fetch(`/v1/knowledge/documents/${encodeURIComponent(base)}/${encodeURIComponent(id)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : `Ошибка ${res.status}`);
    }
    detailCache.set(key, data.document);
    return data.document;
  }

  function sameList(left, right) {
    const a = Array.isArray(left) ? left : [];
    const b = Array.isArray(right) ? right : [];
    return a.length === b.length && a.every((item, idx) => item === b[idx]);
  }

  function createDraftDirty() {
    if (!createPanel || createPanel.hidden) return false;
    return Boolean(
      createId.value.trim()
        || createTitle.value.trim()
        || createText.value.trim()
        || (createDraft.keywords || []).length
        || (createDraft.topics || []).length
        || (createDraft.task_patterns || []).length,
    );
  }

  function editingCardEl() {
    if (!editingId) return null;
    return [...cardList.querySelectorAll(".kb-card")].find((el) => el.dataset.id === editingId) || null;
  }

  function readEditingFields() {
    if (!editingId || !currentBase) return null;
    const card = editingCardEl();
    const titleInput = card && card.querySelector(".kb-body input[type='text']");
    const editor = card && card.querySelector(".kb-body textarea.kb-editor");
    return {
      knowledge_id: editingId,
      title: titleInput ? titleInput.value.trim() : String((draft && draft.title) || "").trim(),
      text: editor ? editor.value : String((draft && draft.text) || ""),
      keywords: (draft && draft.keywords) || [],
      topics: (draft && draft.topics) || [],
      task_patterns: (draft && draft.task_patterns) || [],
      knowledge_type: (draft && draft.knowledge_type) || "",
      status: (draft && draft.status) || "",
    };
  }

  function editingDirty() {
    const fields = readEditingFields();
    if (!fields) return false;
    const saved = detailCache.get(`${currentBase}/${fields.knowledge_id}`) || {};
    return (
      fields.title !== String(saved.title || "").trim()
      || fields.text !== String(saved.text || "")
      || !sameList(fields.keywords, saved.keywords)
      || !sameList(fields.topics, saved.topics)
      || !sameList(fields.task_patterns, saved.task_patterns)
      || (fields.knowledge_type && fields.knowledge_type !== String(saved.knowledge_type || ""))
      || (fields.status && fields.status !== String(saved.status || ""))
    );
  }

  async function persistOpenEdit({ force = false } = {}) {
    const fields = readEditingFields();
    if (!fields) return { ok: true, saved: false };
    if (!fields.title) {
      showFlash("Заголовок не может быть пустым.");
      return { ok: false, saved: false };
    }
    if (!fields.text.trim()) {
      showFlash("Текст не может быть пустым.");
      return { ok: false, saved: false };
    }
    if (!editingDirty() && !force) return { ok: true, saved: false };
    try {
      const res = await fetch(
        `/v1/knowledge/documents/${encodeURIComponent(currentBase)}/${encodeURIComponent(fields.knowledge_id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: fields.text,
            title: fields.title,
            keywords: fields.keywords,
            topics: fields.topics,
            task_patterns: fields.task_patterns,
            knowledge_type: fields.knowledge_type || undefined,
            status: fields.status || undefined,
          }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFlash(detailMessage(data, `Сохранение не удалось (${res.status})`));
        return { ok: false, saved: false };
      }
      const updated = data.document;
      detailCache.set(`${updated.target_base}/${updated.knowledge_id}`, updated);
      editingId = null;
      draft = null;
      return { ok: true, saved: true, document: updated };
    } catch (_) {
      showFlash("Сеть недоступна при сохранении.");
      return { ok: false, saved: false };
    }
  }

  async function persistCreateDraft({ incompleteMessage } = {}) {
    if (!createDraftDirty()) return { ok: true, saved: false };
    if (!currentBase) {
      showFlash("Сначала выберите агента.");
      return { ok: false, saved: false };
    }
    const payload = {
      target_base: currentBase,
      knowledge_id: createId.value.trim(),
      knowledge_type: createType.value.trim(),
      title: createTitle.value.trim(),
      text: createText.value,
      keywords: createDraft.keywords,
      topics: createDraft.topics,
      task_patterns: createDraft.task_patterns,
    };
    if (!payload.knowledge_id || !payload.title || !payload.text.trim()) {
      showFlash(incompleteMessage || "Нужны код карточки, заголовок и текст.");
      return { ok: false, saved: false };
    }
    try {
      const res = await fetch("/v1/knowledge/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFlash(detailMessage(data, `Создание не удалось (${res.status})`));
        return { ok: false, saved: false };
      }
      closeCreatePanel();
      return { ok: true, saved: true, document: data.document };
    } catch (_) {
      showFlash("Сеть недоступна при создании.");
      return { ok: false, saved: false };
    }
  }

  async function persistPendingBeforeIngest() {
    if (editingDirty()) {
      const edited = await persistOpenEdit();
      if (!edited.ok) return false;
    }
    if (createDraftDirty()) {
      const created = await persistCreateDraft({
        incompleteMessage: "Сначала сохраните или отмените новую карточку (нужны код, заголовок и текст).",
      });
      if (!created.ok) return false;
    }
    return true;
  }

  function renderBody(cardEl, doc, { editing = false } = {}) {
    let body = cardEl.querySelector(".kb-body");
    if (!body) {
      body = document.createElement("div");
      body.className = "kb-body";
      cardEl.append(body);
    }
    body.innerHTML = "";

    const actions = document.createElement("div");
    actions.className = "kb-actions";

    if (editing) {
      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn btn-approve";
      saveBtn.textContent = "Сохранить";
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btn-cancel";
      cancelBtn.textContent = "Отмена";
      actions.append(saveBtn, cancelBtn);

      const titleField = document.createElement("label");
      titleField.className = "who-field";
      titleField.innerHTML = "<span>Заголовок</span>";
      const titleInput = document.createElement("input");
      titleInput.type = "text";
      titleInput.value = draft.title || "";
      titleInput.maxLength = 300;
      titleField.append(titleInput);

      const typeField = document.createElement("label");
      typeField.className = "who-field";
      typeField.innerHTML = "<span>Тип карточки</span>";
      const typeSelect = document.createElement("select");
      const ns = currentNamespace();
      const types = (ns && ns.knowledge_types) || [draft.knowledge_type || "keyword_instruction"];
      for (const t of types) {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = typeLabel(t);
        if (t === draft.knowledge_type) opt.selected = true;
        typeSelect.append(opt);
      }
      typeSelect.addEventListener("change", () => { draft.knowledge_type = typeSelect.value; });
      typeField.append(typeSelect);

      const statusField = document.createElement("label");
      statusField.className = "who-field";
      statusField.innerHTML = "<span>Статус</span>";
      const statusSelect = document.createElement("select");
      for (const st of ["active", "draft", "superseded"]) {
        const opt = document.createElement("option");
        opt.value = st;
        opt.textContent = statusLabel(st);
        if (st === (draft.status || "active")) opt.selected = true;
        statusSelect.append(opt);
      }
      statusSelect.addEventListener("change", () => { draft.status = statusSelect.value; });
      statusField.append(statusSelect);

      const tagWrap = document.createElement("div");
      tagWrap.className = "kb-tag-fields";
      for (const group of TAG_GROUPS) {
        tagWrap.append(
          makeTagEditor(group, draft[group.key], (next) => {
            draft[group.key] = next;
          }),
        );
      }

      const textLabel = document.createElement("label");
      textLabel.className = "who-field";
      textLabel.innerHTML = "<span>Текст карточки</span>";
      const editor = document.createElement("textarea");
      editor.className = "kb-editor";
      editor.value = draft.text || "";
      editor.setAttribute("aria-label", "Текст карточки");
      textLabel.append(editor);

      const hint = document.createElement("p");
      hint.className = "kb-ingest";
      hint.textContent = "Сохранение обновляет карточку. Чтобы агент начал её учитывать в поиске, нажмите «Загрузить в RAG».";

      body.append(actions, titleField, typeField, statusField, tagWrap, textLabel, hint);

      cancelBtn.addEventListener("click", () => {
        editingId = null;
        draft = null;
        renderBody(cardEl, doc, { editing: false });
      });
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        try {
          const result = await persistOpenEdit({ force: true });
          if (!result.ok || !result.saved) return;
          showFlash(`Карточка сохранена, версия ${result.document.revision}.`, { ok: true });
          await loadDocuments(currentBase, { keepOpen: result.document.knowledge_id });
        } finally {
          saveBtn.disabled = false;
          cancelBtn.disabled = false;
        }
      });
      return;
    }

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn";
    editBtn.textContent = "Редактировать";
    const ingestOne = document.createElement("button");
    ingestOne.type = "button";
    ingestOne.className = "btn btn-quiet";
    ingestOne.textContent = "Загрузить эту карточку";
    actions.append(editBtn, ingestOne);

    const tags = makeTagView(doc);

    const md = document.createElement("div");
    md.className = "kb-md";
    md.innerHTML = renderMarkdown(doc.text || "");

    const meta = document.createElement("p");
    meta.className = "kb-meta";
    meta.textContent = [
      doc.page || null,
      doc.heading || null,
      doc.author ? `автор ${doc.author}` : null,
      doc.has_schema_catalogue ? "есть схема записей SCHEDULE" : null,
    ].filter(Boolean).join(" · ");

    body.append(actions, tags, md);
    if (meta.textContent) body.append(meta);

    const schema = doc.schema_catalogue;
    if (schema && Array.isArray(schema.schemas) && schema.schemas.length) {
      const titleEl = document.createElement("div");
      titleEl.className = "kb-section-title";
      titleEl.textContent = "Схема записей SCHEDULE";
      const list = document.createElement("ul");
      list.className = "kb-schema";
      for (const entry of schema.schemas) {
        const li = document.createElement("li");
        const fields = Array.isArray(entry.field_names) && entry.field_names.length
          ? ` · ${entry.field_names.join(", ")}`
          : "";
        li.innerHTML = `<span>${escapeHtml(entry.keyword || entry.schema_id || "")}</span><span class="kb-schema-fields">${escapeHtml(fields)}</span>`;
        list.append(li);
      }
      body.append(titleEl, list);
    }

    const examples = Array.isArray(doc.examples) ? doc.examples.filter((item) => item && (item.title || item.task)) : [];
    if (examples.length) {
      const titleEl = document.createElement("div");
      titleEl.className = "kb-section-title";
      titleEl.textContent = "Примеры";
      const list = document.createElement("ul");
      list.className = "kb-examples";
      for (const ex of examples) {
        const li = document.createElement("li");
        li.textContent = [ex.title, ex.task].filter(Boolean).join(" — ");
        list.append(li);
      }
      body.append(titleEl, list);
    }

    const revTitle = document.createElement("div");
    revTitle.className = "kb-section-title";
    revTitle.textContent = "Ревизии";
    const revList = document.createElement("ul");
    revList.className = "kb-rev-list";
    revList.textContent = "Загрузка…";
    body.append(revTitle, revList);
    fetch(`/v1/knowledge/${encodeURIComponent(doc.target_base)}/${encodeURIComponent(doc.knowledge_id)}/revisions`)
      .then((res) => res.json().then((payload) => ({ ok: res.ok, payload })))
      .then(({ ok, payload }) => {
        revList.textContent = "";
        const rows = ok && Array.isArray(payload.revisions) ? payload.revisions : [];
        if (!rows.length) {
          const li = document.createElement("li");
          li.textContent = `версия ${doc.revision || "1"} · ${statusLabel(doc.status)}`;
          revList.append(li);
          return;
        }
        for (const row of rows) {
          const li = document.createElement("li");
          li.textContent = [
            `версия ${row.revision}`,
            statusLabel(row.status),
            row.stored_at ? String(row.stored_at).replace("T", " ").slice(0, 16) : null,
            row.source === "corpus" ? "в файле" : null,
          ].filter(Boolean).join(" · ");
          revList.append(li);
        }
      })
      .catch(() => {
        revList.textContent = "";
        const li = document.createElement("li");
        li.textContent = `версия ${doc.revision || "1"}`;
        revList.append(li);
      });

    ingestOne.addEventListener("click", () => ingestCorpus({
      target_base: doc.target_base,
      knowledge_id: doc.knowledge_id,
    }));

    editBtn.addEventListener("click", () => {
      editingId = doc.knowledge_id;
      draft = {
        text: doc.text || "",
        title: doc.title || "",
        keywords: [...(doc.keywords || [])],
        topics: [...(doc.topics || [])],
        task_patterns: [...(doc.task_patterns || [])],
        knowledge_type: doc.knowledge_type || "",
        status: doc.status || "active",
      };
      renderBody(cardEl, doc, { editing: true });
    });
  }

  async function toggleCard(cardEl, summary) {
    const id = summary.knowledge_id;
    if (openId === id && editingId !== id) {
      openId = null;
      editingId = null;
      draft = null;
      const body = cardEl.querySelector(".kb-body");
      if (body) body.remove();
      cardEl.classList.remove("is-open");
      return;
    }
    for (const other of cardList.querySelectorAll(".kb-card")) {
      if (other !== cardEl) {
        const body = other.querySelector(".kb-body");
        if (body) body.remove();
        other.classList.remove("is-open");
      }
    }
    openId = id;
    cardEl.classList.add("is-open");
    try {
      const detail = await fetchDetail(summary.target_base, id);
      renderBody(cardEl, detail, { editing: editingId === id });
    } catch (err) {
      showFlash(err.message || "Не удалось загрузить карточку.");
    }
  }

  function buildCard(summary) {
    const card = document.createElement("article");
    card.className = "kb-card";
    card.dataset.id = summary.knowledge_id;
    card.dataset.search = [
      summary.title, summary.knowledge_id, summary.knowledge_type, summary.text_preview,
      ...(summary.keywords || []), ...(summary.topics || []), ...(summary.task_patterns || []),
    ].filter(Boolean).join(" ").toLowerCase();

    const head = document.createElement("button");
    head.type = "button";
    head.className = "kb-card-head";

    const titleRow = document.createElement("div");
    titleRow.className = "kb-card-title-row";
    const title = document.createElement("h2");
    title.className = "kb-card-title";
    title.textContent = summary.title || summary.knowledge_id;
    const type = document.createElement("span");
    type.className = "kb-type";
    type.textContent = typeLabel(summary.knowledge_type);
    titleRow.append(title, type);

    const meta = document.createElement("div");
    meta.className = "kb-meta";
    meta.textContent = [
      summary.knowledge_id || null,
      summary.revision ? `версия ${summary.revision}` : null,
      statusLabel(summary.status),
    ].filter(Boolean).join(" · ");

    const preview = document.createElement("p");
    preview.className = "kb-preview";
    preview.textContent = summary.text_preview || "";

    head.append(titleRow, meta, chipRowCompact(summary), preview);
    head.addEventListener("click", () => toggleCard(card, summary));
    card.append(head);
    return card;
  }

  async function loadDocuments(base, { keepOpen = null } = {}) {
    currentBase = base;
    detailCache.clear();
    cardList.innerHTML = "";
    listEmpty.hidden = true;
    openId = null;
    editingId = null;
    draft = null;
    syncAddButton();
    if (createPanel && !createPanel.hidden && base) {
      fillCreateTypes();
    } else {
      closeCreatePanel();
    }

    if (!base) {
      agentHint.textContent = "Нет выбранного агента.";
      return;
    }

    const q = kbSearch ? kbSearch.value.trim() : "";
    const res = await fetch(documentsUrl(base, { q, tag: activeTag }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showFlash(typeof data.detail === "string" ? data.detail : "Не удалось загрузить список.");
      return;
    }
    const docs = Array.isArray(data.documents) ? data.documents : [];
    const ns = namespaces.find((item) => item.id === base);
    syncAgentTabs();
    const total = ns && ns.document_count != null ? ns.document_count : docs.length;
    const filterBits = [q ? `поиск «${q}»` : "", activeTag ? `тег «${activeTag}»` : ""].filter(Boolean);
    agentHint.textContent = ns
      ? `${ns.label} · ${pluralCards(q || activeTag ? docs.length : total)} · ${[...new Set((ns.knowledge_types || []).map(typeLabel))].join(", ") || "—"}`
      : pluralCards(docs.length);
    if (kbCount) kbCount.textContent = filterBits.length ? `${docs.length}` : "";

    if (!docs.length) {
      listEmpty.hidden = false;
      return;
    }
    for (const doc of docs) {
      const card = buildCard(doc);
      cardList.append(card);
      if (keepOpen && doc.knowledge_id === keepOpen) {
        openId = keepOpen;
        try {
          const detail = await fetchDetail(base, keepOpen);
          renderBody(card, detail, { editing: false });
        } catch (err) {
          showFlash(err.message || "Не удалось открыть карточку.");
        }
      }
    }
  }

  function pluralCards(n) {
    const m10 = n % 10; const m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return `${n} карточка`;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${n} карточки`;
    return `${n} карточек`;
  }

  agentSelect.addEventListener("change", () => {
    const base = agentSelect.value.trim();
    activeTag = "";
    loadDocuments(base);
  });
  if (kbSearch) kbSearch.addEventListener("input", applySearch);

  addBtn.addEventListener("click", () => openCreatePanel());

  async function ingestCorpus(extra = {}) {
    ingestBtn.disabled = true;
    try {
      const ready = await persistPendingBeforeIngest();
      if (!ready) return;
      showFlash("Загрузка в RAG… это может занять несколько минут.", { ok: true, ms: 0 });
      const body = {
        purge_superseded: Boolean(purgeSuperseded && purgeSuperseded.checked),
        ...extra,
      };
      const res = await fetch("/v1/knowledge/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFlash(detailMessage(data, `Загрузка в RAG не удалась (${res.status})`));
        return;
      }
      const message = String(data.message || "").trim()
        || `Добавлено ${data.added ?? 0}, пропущено ${data.skipped ?? 0}, всего в RAG ${data.total_in_rag ?? "—"}.`;
      showFlash(message, { ok: Boolean(data.ok), ms: 12000 });
      await refreshNamespaceCounts();
      if (currentBase) await loadDocuments(currentBase, extra.knowledge_id ? { keepOpen: extra.knowledge_id } : {});
    } catch (_) {
      showFlash("Сеть недоступна при загрузке в RAG.");
    } finally {
      ingestBtn.disabled = false;
    }
  }

  async function refreshNamespaceCounts() {
    const res = await fetch("/v1/knowledge/namespaces");
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !Array.isArray(data.namespaces)) return;
    namespaces = data.namespaces;
    renderAgentTabs();
  }

  ingestBtn.addEventListener("click", () => ingestCorpus());
  createCancel.addEventListener("click", () => closeCreatePanel());
  createSave.addEventListener("click", async () => {
    createSave.disabled = true;
    try {
      const result = await persistCreateDraft();
      if (!result.ok || !result.saved) {
        if (result.ok && !result.saved && !createDraftDirty()) {
          showFlash("Нужны код карточки, заголовок и текст.");
        }
        return;
      }
      showFlash("Карточка создана.", { ok: true });
      await loadDocuments(currentBase, { keepOpen: result.document.knowledge_id });
    } finally {
      createSave.disabled = false;
    }
  });

  loadNamespaces().catch(() => showFlash("Сеть недоступна при загрузке базы знаний."));
})();
