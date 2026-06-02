const state = {
  tables: [],
  coreTables: [],
  schemas: new Map(),
  current: {
    core: { table: "", page: 1, search: "", field: "" },
    all: { table: "", page: 1, search: "", field: "" },
    file: "",
  },
  selection: {
    core: new Set(),
    all: new Set(),
  },
};

const tableLabels = {
  user_profile: "用户资料",
  infant_profile: "宝宝资料",
  feeding_log: "喂养记录",
  pumping_log: "泵奶记录",
  milk_plan: "泌乳计划",
  calendar: "日程任务",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const suppressAuthRedirect = Boolean(options.suppressAuthRedirect);
  const { suppressAuthRedirect: _ignored, ...fetchOptions } = options;
  const response = await fetch(path, {
    ...fetchOptions,
    headers: isForm ? options.headers || {} : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 401 && !suppressAuthRedirect) {
    showLogin();
    throw new Error("请先登录");
  }
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body.detail?.message || body.message || message;
    } catch {
      // Keep the HTTP status text.
    }
    throw new Error(message);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response;
}

function showLogin(message = "") {
  $("login-view").hidden = false;
  $("admin-view").hidden = true;
  $("login-error").textContent = message;
}

function showAdmin() {
  $("login-view").hidden = true;
  $("admin-view").hidden = false;
}

function bindEvents() {
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ username: $("username").value, password: $("password").value }),
      });
      showAdmin();
      await Promise.all([loadTables(), loadFiles()]);
    } catch (error) {
      showLogin(error.message);
    }
  });

  $("logout-button").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: "{}" });
    showLogin();
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
      document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active-view", view.id === tab.dataset.view));
    });
  });

  bindTableControls("core");
  bindTableControls("all");

  $("file-refresh").addEventListener("click", loadFiles);
  $("file-new").addEventListener("click", () => $("file-dialog").showModal());
  $("file-cancel").addEventListener("click", () => $("file-dialog").close());
  $("file-form").addEventListener("submit", createFile);
  $("file-save").addEventListener("click", saveFile);
  $("file-delete").addEventListener("click", deleteFile);
  $("file-download").addEventListener("click", downloadFile);
  $("file-upload").addEventListener("change", uploadFile);
  $("notification-form").addEventListener("submit", reportNotification);
  $("row-cancel").addEventListener("click", () => $("row-dialog").close());
}

function bindTableControls(kind) {
  const prefix = kind === "core" ? "core" : "all";
  $(`${prefix}-table`).addEventListener("change", (event) => {
    state.current[kind].table = event.target.value;
    state.current[kind].page = 1;
    state.current[kind].field = "";
    state.selection[kind].clear();
    loadRows(kind);
  });
  $(`${prefix}-field`).addEventListener("change", (event) => {
    state.current[kind].field = event.target.value;
    syncSearchFromInput(kind);
    state.current[kind].page = 1;
    loadRows(kind);
  });
  $(`${prefix}-search`).addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      syncSearchFromInput(kind);
      state.current[kind].page = 1;
      loadRows(kind);
    }
  });
  $(`${prefix}-refresh`).addEventListener("click", () => {
    syncSearchFromInput(kind);
    state.current[kind].page = 1;
    state.selection[kind].clear();
    loadRows(kind);
  });
  $(`${prefix}-add`).addEventListener("click", () => openRowDialog(kind, null));
  $(`${prefix}-bulk-delete`).addEventListener("click", () => deleteSelectedRows(kind));
}

function syncSearchFromInput(kind) {
  const prefix = kind === "core" ? "core" : "all";
  state.current[kind].search = $(`${prefix}-search`).value;
}

async function init() {
  bindEvents();
  try {
    await api("/api/session");
    showAdmin();
    await Promise.all([loadTables(), loadFiles()]);
  } catch {
    showLogin();
  }
}

async function loadTables() {
  const data = await api("/api/db/tables");
  state.tables = data.tables;
  state.coreTables = data.core_tables;
  fillTableSelect($("core-table"), state.coreTables);
  fillTableSelect($("all-table"), state.tables);
  state.current.core.table = state.coreTables[0] || "";
  state.current.all.table = state.tables[0] || "";
  if (state.current.core.table) await loadRows("core");
  if (state.current.all.table) await loadRows("all");
}

function fillTableSelect(select, tables) {
  select.innerHTML = tables.map((table) => `<option value="${escapeHtml(table)}">${escapeHtml(tableLabels[table] || table)}</option>`).join("");
}

async function getSchema(table) {
  if (!state.schemas.has(table)) {
    state.schemas.set(table, await api(`/api/db/tables/${encodeURIComponent(table)}/schema`));
  }
  return state.schemas.get(table);
}

async function loadRows(kind) {
  const current = state.current[kind];
  if (!current.table) return;
  const prefix = kind === "core" ? "core" : "all";
  const params = new URLSearchParams({ page: current.page, page_size: 20, q: current.search, field: current.field });
  const data = await api(`/api/db/tables/${encodeURIComponent(current.table)}/rows?${params}`);
  state.selection[kind].clear();
  renderFieldSelect(prefix, kind, data.schema);
  renderRows(prefix, kind, data);
  renderPager(prefix, kind, data);
  if (kind === "all") {
    $("schema-panel").textContent = `${data.schema.table} | 主键: ${data.schema.primary_key} | 字段: ${data.schema.columns.map((c) => c.name).join(", ")}`;
  }
}

function renderFieldSelect(prefix, kind, schema) {
  const select = $(`${prefix}-field`);
  const existing = state.current[kind].field;
  const fields = schema.columns.map((column) => column.name);
  select.innerHTML = '<option value="">全部字段</option>' + fields.map((field) => `<option value="${escapeHtml(field)}">${escapeHtml(field)}</option>`).join("");
  state.current[kind].field = fields.includes(existing) ? existing : "";
  select.value = state.current[kind].field;
}

function renderRows(prefix, kind, data) {
  const wrap = $(`${prefix}-table-wrap`);
  const columns = data.schema.columns.map((column) => column.name);
  const pk = data.schema.primary_key;
  if (!data.rows.length) {
    wrap.innerHTML = '<div class="schema-panel">暂无数据</div>';
    updateSelectionToolbar(prefix, kind);
    return;
  }
  const head = `<th class="select-cell"><input data-action="select-all" type="checkbox" aria-label="全选当前页"></th>` +
    columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("") +
    "<th>操作</th>";
  const body = data.rows.map((row, index) => {
    const key = valueText(row[pk]);
    const checked = state.selection[kind].has(key) ? "checked" : "";
    const selectCell = `<td class="select-cell"><input data-action="select-row" data-key="${escapeHtml(key)}" type="checkbox" aria-label="选择记录 ${escapeHtml(key)}" ${checked}></td>`;
    const cells = columns.map((column) => `<td title="${escapeHtml(valueText(row[column]))}">${escapeHtml(valueText(row[column]))}</td>`).join("");
    return `<tr data-index="${index}">${selectCell}${cells}<td><div class="row-actions"><button data-action="edit">编辑</button><button class="danger" data-action="delete">删除</button></div></td></tr>`;
  }).join("");
  wrap.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  const visibleKeys = data.rows.map((row) => valueText(row[pk]));
  const selectAll = wrap.querySelector("[data-action='select-all']");
  selectAll.addEventListener("change", () => {
    if (selectAll.checked) {
      visibleKeys.forEach((key) => state.selection[kind].add(key));
    } else {
      visibleKeys.forEach((key) => state.selection[kind].delete(key));
    }
    renderSelectionState(prefix, kind, wrap, visibleKeys);
  });
  wrap.querySelectorAll("tbody tr").forEach((tr) => {
    const row = data.rows[Number(tr.dataset.index)];
    tr.querySelector("[data-action='edit']").addEventListener("click", () => openRowDialog(kind, row));
    tr.querySelector("[data-action='delete']").addEventListener("click", () => deleteRow(kind, row, data.schema));
    tr.querySelector("[data-action='select-row']").addEventListener("change", (event) => {
      if (event.target.checked) {
        state.selection[kind].add(event.target.dataset.key);
      } else {
        state.selection[kind].delete(event.target.dataset.key);
      }
      renderSelectionState(prefix, kind, wrap, visibleKeys);
    });
  });
  renderSelectionState(prefix, kind, wrap, visibleKeys);
}

function renderSelectionState(prefix, kind, wrap, visibleKeys) {
  wrap.querySelectorAll("[data-action='select-row']").forEach((checkbox) => {
    checkbox.checked = state.selection[kind].has(checkbox.dataset.key);
  });
  const selectedVisibleCount = visibleKeys.filter((key) => state.selection[kind].has(key)).length;
  const selectAll = wrap.querySelector("[data-action='select-all']");
  selectAll.checked = selectedVisibleCount > 0 && selectedVisibleCount === visibleKeys.length;
  selectAll.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visibleKeys.length;
  updateSelectionToolbar(prefix, kind);
}

function updateSelectionToolbar(prefix, kind) {
  const selectedCount = state.selection[kind].size;
  $(`${prefix}-selected-count`).textContent = `已选 ${selectedCount} 条`;
  $(`${prefix}-bulk-delete`).disabled = selectedCount === 0;
}

function renderPager(prefix, kind, data) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  const pager = $(`${prefix}-pager`);
  pager.innerHTML = `<button data-page="prev" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${data.page} / ${totalPages} 页，共 ${data.total} 条</span><button data-page="next" ${data.page >= totalPages ? "disabled" : ""}>下一页</button>`;
  pager.querySelector("[data-page='prev']").addEventListener("click", () => {
    state.current[kind].page -= 1;
    loadRows(kind);
  });
  pager.querySelector("[data-page='next']").addEventListener("click", () => {
    state.current[kind].page += 1;
    loadRows(kind);
  });
}

async function openRowDialog(kind, row) {
  const current = state.current[kind];
  const schema = await getSchema(current.table);
  const pk = schema.primary_key;
  $("row-dialog-title").textContent = row ? `编辑 ${tableLabels[current.table] || current.table}` : `新增 ${tableLabels[current.table] || current.table}`;
  $("row-error").textContent = "";
  $("row-fields").innerHTML = schema.columns.map((column) => {
    const disabled = row && column.name === pk ? "disabled" : "";
    return `<label>${escapeHtml(column.name)}<input data-column="${escapeHtml(column.name)}" value="${escapeHtml(row ? valueText(row[column.name]) : "")}" ${disabled}></label>`;
  }).join("");
  $("row-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const payload = {};
      $("row-fields").querySelectorAll("input").forEach((input) => {
        if (!input.disabled) payload[input.dataset.column] = parseInput(input.value);
      });
      if (row) {
        await api(`/api/db/tables/${encodeURIComponent(current.table)}/rows/${encodeURIComponent(row[pk])}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await api(`/api/db/tables/${encodeURIComponent(current.table)}/rows`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      $("row-dialog").close();
      await loadRows(kind);
    } catch (error) {
      $("row-error").textContent = error.message;
    }
  };
  $("row-dialog").showModal();
}

async function deleteRow(kind, row, schema) {
  const pk = schema.primary_key;
  if (!confirm(`确认删除 ${schema.table}/${row[pk]} ?`)) return;
  await api(`/api/db/tables/${encodeURIComponent(schema.table)}/rows/${encodeURIComponent(row[pk])}`, { method: "DELETE" });
  await loadRows(kind);
}

async function deleteSelectedRows(kind) {
  const current = state.current[kind];
  const selectedKeys = Array.from(state.selection[kind]);
  if (!selectedKeys.length) return;
  const schema = await getSchema(current.table);
  if (!confirm(`确认删除 ${selectedKeys.length} 条记录？`)) return;
  await Promise.all(selectedKeys.map((key) => (
    api(`/api/db/tables/${encodeURIComponent(schema.table)}/rows/${encodeURIComponent(key)}`, { method: "DELETE" })
  )));
  state.selection[kind].clear();
  await loadRows(kind);
}

async function loadFiles() {
  const data = await api("/api/files");
  $("file-list").innerHTML = data.files.map((file) => (
    `<button class="file-item" data-path="${escapeHtml(file.path)}" type="button">${escapeHtml(file.path)}<br><small>${file.size} bytes</small></button>`
  )).join("");
  $("file-list").querySelectorAll(".file-item").forEach((button) => {
    button.addEventListener("click", () => openFile(button.dataset.path));
  });
}

async function openFile(path) {
  state.current.file = path;
  document.querySelectorAll(".file-item").forEach((button) => button.classList.toggle("active", button.dataset.path === path));
  const data = await api(`/api/files/content?path=${encodeURIComponent(path)}`);
  $("file-title").textContent = path;
  $("file-status").textContent = data.editable ? "可编辑" : "二进制/不可编辑";
  $("file-editor").value = data.content || "";
  $("file-editor").disabled = !data.editable;
  $("file-save").disabled = !data.editable;
  $("file-delete").disabled = false;
  $("file-download").disabled = false;
}

async function createFile(event) {
  event.preventDefault();
  try {
    await api("/api/files", {
      method: "POST",
      body: JSON.stringify({ path: $("new-file-path").value, content: $("new-file-content").value }),
    });
    $("file-dialog").close();
    $("new-file-path").value = "";
    $("new-file-content").value = "";
    await loadFiles();
  } catch (error) {
    $("file-error").textContent = error.message;
  }
}

async function saveFile() {
  await api("/api/files/content", {
    method: "PUT",
    body: JSON.stringify({ path: state.current.file, content: $("file-editor").value }),
  });
  $("file-status").textContent = "已保存";
}

async function deleteFile() {
  if (!state.current.file || !confirm(`确认删除 ${state.current.file} ?`)) return;
  await api(`/api/files?path=${encodeURIComponent(state.current.file)}`, { method: "DELETE" });
  state.current.file = "";
  $("file-title").textContent = "未选择文件";
  $("file-editor").value = "";
  $("file-editor").disabled = true;
  $("file-save").disabled = true;
  $("file-delete").disabled = true;
  $("file-download").disabled = true;
  await loadFiles();
}

function downloadFile() {
  if (state.current.file) window.location.href = `/api/files/download?path=${encodeURIComponent(state.current.file)}`;
}

async function uploadFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("upload", file);
  await api("/api/files/upload", { method: "POST", body: form });
  event.target.value = "";
  await loadFiles();
}

async function reportNotification(event) {
  event.preventDefault();
  $("notification-error").textContent = "";
  $("notification-status").textContent = "";
  $("notification-submit").disabled = true;
  try {
    const dataText = $("notification-data").value.trim();
    const data = dataText ? JSON.parse(dataText) : {};
    const token = $("notification-token").value.trim();
    const headers = token ? { "X-Web-Data-Ws-Token": token } : {};
    const response = await api("/api/notifications/report", {
      method: "POST",
      headers,
      suppressAuthRedirect: true,
      body: JSON.stringify({
        user_id: $("notification-user-id").value.trim(),
        reminder_type: $("notification-type").value,
        title: $("notification-title").value,
        message: $("notification-message").value,
        data,
      }),
    });
    $("notification-status").textContent = `已上报：${response.notification.reminder_type}，已投递 ${response.delivered} 个连接`;
  } catch (error) {
    $("notification-error").textContent = error instanceof SyntaxError ? "扩展数据必须是合法 JSON" : error.message;
  } finally {
    $("notification-submit").disabled = false;
  }
}

function valueText(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function parseInput(value) {
  const text = value.trim();
  if (text === "") return null;
  if (/^-?\d+$/.test(text)) return Number(text);
  if (/^-?\d+\.\d+$/.test(text)) return Number(text);
  return value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

init();
