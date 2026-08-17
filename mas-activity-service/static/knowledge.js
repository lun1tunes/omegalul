(() => {
  const agentSelect = document.getElementById("agentSelect");
  const agentHint = document.getElementById("agentHint");
  const cardList = document.getElementById("cardList");
  const listEmpty = document.getElementById("listEmpty");
  const flashEl = document.getElementById("flash");
  const addBtn = document.getElementById("addBtn");
  const createPanel = document.getElementById("createPanel");
  const createId = document.getElementById("createId");
  const createType = document.getElementById("createType");
  const createTitle = document.getElementById("createTitle");
  const createText = document.getElementById("createText");
  const createTagFields = document.getElementById("createTagFields");
  const createSave = document.getElementById("createSave");
  const createCancel = document.getElementById("createCancel");

  const TAG_GROUPS = [
    {
      key: "keywords",
      label: "Keywords (теги)",
      hint: "Поле keywords в corpus. Для Schedule — обычно allowlisted SCHEDULE keywords (DATES, WCONPROD…). В RAG — keyword/tag-фильтр.",
    },
    {
      key: "topics",
      label: "Topics",
      hint: "Поле topics. Тематические ярлыки для tag-ветки retrieval (необязательно).",
    },
    {
      key: "task_patterns",
      label: "Task patterns",
      hint: "Поле task_patterns. Как инженер формулирует задачу — помогает матчить запрос к карточке.",
    },
  ];

  let namespaces = [];
  let currentBase = "";
  let flashTimer = null;
  let openId = null;
  let editingId = null;
  let draft = null;
  let detailCache = new Map();
  let createDraft = { keywords: [], topics: [], task_patterns: [] };

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
    }, ok ? 4000 : 9000);
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

  function syncAddButton() {
    addBtn.disabled = !currentBase;
  }

  function fillCreateTypes() {
    const ns = currentNamespace();
    const types = (ns && ns.knowledge_types) || [];
    createType.innerHTML = "";
    for (const t of types) {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      createType.append(opt);
    }
    if (!types.length) {
      const opt = document.createElement("option");
      opt.value = "keyword_instruction";
      opt.textContent = "keyword_instruction";
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
          const el = document.createElement("span");
          el.className = `kb-chip ${group.key === "keywords" ? "kw" : ""}`;
          el.textContent = tag;
          row.append(el);
        }
      }
      section.append(label, hint, row);
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
        const el = document.createElement("span");
        el.className = `kb-chip ${group.key === "keywords" ? "kw" : ""}`;
        el.textContent = tag;
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
      showFlash(typeof data.detail === "string" ? data.detail : "Не удалось загрузить namespaces.");
      agentSelect.innerHTML = '<option value="">Нет данных</option>';
      return;
    }
    namespaces = Array.isArray(data.namespaces) ? data.namespaces : [];
    agentSelect.innerHTML = "";
    if (!namespaces.length) {
      agentSelect.innerHTML = '<option value="">Нет данных</option>';
      agentHint.textContent = "В corpus нет namespaces.";
      syncAddButton();
      return;
    }
    for (const ns of namespaces) {
      const opt = document.createElement("option");
      opt.value = ns.id;
      opt.textContent = `${ns.label} (${ns.id})`;
      agentSelect.append(opt);
    }
    const first = namespaces[0].id;
    agentSelect.value = first;
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
      titleField.innerHTML = "<span>title</span>";
      const titleInput = document.createElement("input");
      titleInput.type = "text";
      titleInput.value = draft.title || "";
      titleInput.maxLength = 300;
      titleField.append(titleInput);

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
      textLabel.innerHTML = "<span>text (markdown)</span>";
      const editor = document.createElement("textarea");
      editor.className = "kb-editor";
      editor.value = draft.text || "";
      editor.setAttribute("aria-label", "Сырой markdown карточки");
      textLabel.append(editor);

      const hint = document.createElement("p");
      hint.className = "kb-ingest";
      hint.textContent = "Сохранение пишет в JSON corpus и поднимает revision.";

      body.append(actions, titleField, tagWrap, textLabel, hint);

      cancelBtn.addEventListener("click", () => {
        editingId = null;
        draft = null;
        renderBody(cardEl, doc, { editing: false });
      });
      saveBtn.addEventListener("click", async () => {
        const text = editor.value;
        const title = titleInput.value.trim();
        if (!title) {
          showFlash("Заголовок не может быть пустым.");
          return;
        }
        if (!text.trim()) {
          showFlash("Текст не может быть пустым.");
          return;
        }
        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        try {
          const res = await fetch(
            `/v1/knowledge/documents/${encodeURIComponent(doc.target_base)}/${encodeURIComponent(doc.knowledge_id)}`,
            {
              method: "PATCH",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                text,
                title,
                keywords: draft.keywords,
                topics: draft.topics,
                task_patterns: draft.task_patterns,
              }),
            },
          );
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            showFlash(typeof data.detail === "string" ? data.detail : `Сохранение не удалось (${res.status})`);
            return;
          }
          const updated = data.document;
          detailCache.set(`${updated.target_base}/${updated.knowledge_id}`, updated);
          editingId = null;
          draft = null;
          showFlash(`Сохранено, revision ${updated.revision}.`, { ok: true });
          await loadDocuments(currentBase, { keepOpen: updated.knowledge_id });
        } catch (_) {
          showFlash("Сеть недоступна при сохранении.");
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
    actions.append(editBtn);

    const tags = makeTagView(doc);

    const md = document.createElement("div");
    md.className = "kb-md";
    md.innerHTML = renderMarkdown(doc.text || "");

    const meta = document.createElement("p");
    meta.className = "kb-meta";
    meta.textContent = [
      doc.page || null,
      doc.heading || null,
      doc.author ? `author ${doc.author}` : null,
      doc.has_schema_catalogue ? "schema_catalogue" : null,
    ].filter(Boolean).join(" · ");

    body.append(actions, tags, md);
    if (meta.textContent) body.append(meta);

    editBtn.addEventListener("click", () => {
      editingId = doc.knowledge_id;
      draft = {
        text: doc.text || "",
        title: doc.title || "",
        keywords: [...(doc.keywords || [])],
        topics: [...(doc.topics || [])],
        task_patterns: [...(doc.task_patterns || [])],
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
      return;
    }
    for (const other of cardList.querySelectorAll(".kb-card")) {
      if (other !== cardEl) {
        const body = other.querySelector(".kb-body");
        if (body) body.remove();
      }
    }
    openId = id;
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
    type.textContent = summary.knowledge_type || "card";
    titleRow.append(title, type);

    const meta = document.createElement("div");
    meta.className = "kb-meta";
    meta.textContent = `${summary.knowledge_id} · rev ${summary.revision} · ${summary.status}`;

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

    const res = await fetch(`/v1/knowledge/documents?target_base=${encodeURIComponent(base)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showFlash(typeof data.detail === "string" ? data.detail : "Не удалось загрузить список.");
      return;
    }
    const docs = Array.isArray(data.documents) ? data.documents : [];
    const ns = namespaces.find((item) => item.id === base);
    agentHint.textContent = ns
      ? `${ns.label}: ${docs.length} карточек · типы: ${(ns.knowledge_types || []).join(", ") || "—"}`
      : `${docs.length} карточек`;

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

  agentSelect.addEventListener("change", () => {
    const base = agentSelect.value.trim();
    loadDocuments(base);
  });

  addBtn.addEventListener("click", () => openCreatePanel());
  createCancel.addEventListener("click", () => closeCreatePanel());
  createSave.addEventListener("click", async () => {
    if (!currentBase) {
      showFlash("Сначала выберите агента.");
      return;
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
      showFlash("Нужны knowledge_id, title и text.");
      return;
    }
    createSave.disabled = true;
    try {
      const res = await fetch("/v1/knowledge/documents", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFlash(typeof data.detail === "string" ? data.detail : `Создание не удалось (${res.status})`);
        return;
      }
      closeCreatePanel();
      showFlash("Карточка создана.", { ok: true });
      await loadDocuments(currentBase, { keepOpen: data.document.knowledge_id });
    } catch (_) {
      showFlash("Сеть недоступна при создании.");
    } finally {
      createSave.disabled = false;
    }
  });

  loadNamespaces().catch(() => showFlash("Сеть недоступна при загрузке namespaces."));
})();
