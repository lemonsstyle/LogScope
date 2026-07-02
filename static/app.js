const state = {
  connections: [],
  schemas: [],
  tables: [],
  columns: [],
  selectedConnectionId: "",
  selectedSchema: "",
  selectedTable: "",
  sqlMode: false,
  resultColumns: [],
  resultRows: [],
  resultSort: { column: "", order: "desc" },
  lastSelections: {}, // Store last selections per connection
  columnWidths: {}, // Store column widths
  sessionPasswords: {},
  activeQueryController: null,
  queryTimeout: 300000, // Default 300s in milliseconds
};

const LIMIT_MIN = 50;
const LIMIT_MAX = 1000;
const LIMIT_STEP = 10;
const LIMIT_SLIDER_MAX = 200;
const DATABASE_TYPE_LABELS = {
  auto: "自动识别",
  mysql: "MySQL",
  postgresql: "PostgreSQL",
  sqlserver: "SQL Server",
};
const DATABASE_TYPE_DEFAULT_PORTS = {
  mysql: "3306",
  postgresql: "5432",
  sqlserver: "1433",
};
const DEFAULT_PORT_VALUES = new Set(Object.values(DATABASE_TYPE_DEFAULT_PORTS));

const els = {
  connectionFormPanel: document.getElementById("connectionFormPanel"),
  connectionsState: document.getElementById("connectionsState"),
  connectionsList: document.getElementById("connectionsList"),
  connectionForm: document.getElementById("connectionForm"),
  connectionFeedback: document.getElementById("connectionFeedback"),
  charsetDisclosure: document.getElementById("charsetDisclosure"),
  databaseType: document.getElementById("databaseType"),
  port: document.getElementById("port"),
  database: document.getElementById("database"),
  charset: document.getElementById("charset"),
  activeConnection: document.getElementById("activeConnection"),
  schemaState: document.getElementById("schemaState"),
  schemaList: document.getElementById("schemaList"),
  tableList: document.getElementById("tableList"),
  schemaFilter: document.getElementById("schemaFilter"),
  tableFilter: document.getElementById("tableFilter"),
  searchForm: document.getElementById("searchForm"),
  resultsState: document.getElementById("resultsState"),
  resultsMeta: document.getElementById("resultsMeta"),
  resultsTable: document.getElementById("resultsTable"),
  timeColumn: document.getElementById("timeColumn"),
  timeMode: document.getElementById("timeMode"),
  limit: document.getElementById("limit"),
  limitValue: document.getElementById("limitValue"),
  timeFrom: document.getElementById("timeFrom"),
  timeTo: document.getElementById("timeTo"),
  timePoint: document.getElementById("timePoint"),
  visibleColumns: document.getElementById("visibleColumns"),
  selectAllVisibleColumns: document.getElementById("selectAllVisibleColumns"),
  keywordFields: document.getElementById("keywordFields"),
  keywordTerms: document.getElementById("keywordTerms"),
  toggleSqlButton: document.getElementById("toggleSqlButton"),
  sqlPanel: document.getElementById("sqlPanel"),
  sqlEditor: document.getElementById("sqlEditor"),
  cancelQueryButtons: document.querySelectorAll(".cancel-query-button"),
};

async function api(path, options = {}) {
  const { timeoutMs, headers, signal, abortMessage, ...fetchOptions } = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  const forwardAbort = () => controller?.abort();
  if (controller && signal) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener("abort", forwardAbort, { once: true });
    }
  }
  try {
    const response = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json", ...(headers || {}) },
      signal: controller ? controller.signal : signal,
      ...fetchOptions,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.details ? `${payload.error} ${payload.details}` : payload.error || "请求失败");
    }
    return payload;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      if (signal?.aborted) {
        throw new Error(abortMessage || "查询已终止。");
      }
      throw new Error("请求超时，已取消等待。数据库查询可能仍在服务端清理中。");
    }
    throw error;
  } finally {
    if (controller && signal) {
      signal.removeEventListener("abort", forwardAbort);
    }
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
  }
}

function setFeedback(node, text, tone = "") {
  node.textContent = text;
  node.dataset.tone = tone;
}

function handleError(error) {
  const message = error instanceof Error ? error.message : String(error);
  els.schemaState.classList.remove("hidden");
  els.resultsState.classList.remove("hidden");
  setFeedback(els.connectionFeedback, message, "error");
  setFeedback(els.schemaState, message, "error");
  setFeedback(els.resultsState, message, "error");
}

function openPanel(panel) {
  panel?.scrollIntoView?.({ block: "start", behavior: "smooth" });
}

function selectedConnection() {
  return state.connections.find((item) => item.id === state.selectedConnectionId) || null;
}

function rememberConnectionPassword(connectionId, password) {
  if (connectionId && password) {
    state.sessionPasswords[connectionId] = password;
  }
}

function updateConnectionSummary() {
  const item = selectedConnection();
  if (!item) {
    return;
  }
  // Summary removed from UI
}

function updateConnectionConfigSummary() {
  const profileId = document.getElementById("connectionId").value.trim();
  const item = state.connections.find((connection) => connection.id === profileId) || selectedConnection();
  if (!item) {
    return;
  }
  // Summary removed from UI
}

function clampLimit(value) {
  return Math.min(LIMIT_MAX, Math.max(LIMIT_MIN, value));
}

function snapLimit(value) {
  return clampLimit(Math.round(value / LIMIT_STEP) * LIMIT_STEP);
}

function limitFromSlider(value) {
  const progress = Math.min(1, Math.max(0, Number(value) / LIMIT_SLIDER_MAX));
  const scaled = LIMIT_MIN * Math.pow(LIMIT_MAX / LIMIT_MIN, progress);
  return snapLimit(scaled);
}

function sliderValueForLimit(value) {
  const clamped = clampLimit(value);
  const ratio = Math.log(clamped / LIMIT_MIN) / Math.log(LIMIT_MAX / LIMIT_MIN);
  return Math.round(ratio * LIMIT_SLIDER_MAX);
}

function currentLimitValue() {
  return limitFromSlider(els.limit.value);
}

function setLimitValue(value) {
  els.limit.value = String(sliderValueForLimit(value));
  updateLimitValue();
}

function updateLimitValue() {
  els.limitValue.textContent = String(currentLimitValue());
}

function databaseTypeLabel(value) {
  return DATABASE_TYPE_LABELS[value] || value || "MySQL";
}

function syncCharsetDisclosure(value) {
  const databaseType = els.databaseType.value || "auto";
  const canUseCharset = databaseType === "auto" || databaseType === "mysql";
  els.charsetDisclosure.classList.toggle("hidden", !canUseCharset);
  if (!canUseCharset) {
    els.charsetDisclosure.open = false;
    return;
  }
  els.charsetDisclosure.open = value !== "utf8mb4";
}

function maybeApplyDefaultPort(databaseType) {
  const defaultPort = DATABASE_TYPE_DEFAULT_PORTS[databaseType];
  if (!defaultPort) {
    return;
  }
  const currentPort = els.port.value.trim();
  const wasManuallyEdited = els.port.dataset.manual === "true";
  const canReplace = !currentPort || DEFAULT_PORT_VALUES.has(currentPort) || !wasManuallyEdited;
  if (canReplace) {
    els.port.value = defaultPort;
  }
}

function syncDatabaseTypeControls({ applyPort = false } = {}) {
  const databaseType = els.databaseType.value || "auto";
  if (applyPort) {
    maybeApplyDefaultPort(databaseType);
  }
  syncCharsetDisclosure(els.charset.value || "utf8mb4");
}

function selectedConnectionUsesDatabaseList() {
  const item = selectedConnection();
  if (!item) {
    return false;
  }
  const databaseType = item.database_type || "mysql";
  return databaseType === "mysql" || (databaseType === "auto" && Number(item.port) === 3306);
}

function optionNode(value, label = value, selected = false) {
  return new Option(String(label), String(value), false, selected);
}

function connectionPath(connectionId) {
  return `/connections/${encodeURIComponent(connectionId)}`;
}

async function saveSelectedConnectionPatch(patch) {
  const current = selectedConnection();
  if (!current) {
    return;
  }
  const payload = {
    ...current,
    ...patch,
    password: "",
  };
  await api(connectionPath(current.id), { method: "PUT", body: JSON.stringify(payload) });
}

function resetForm() {
  els.connectionForm.reset();
  els.databaseType.value = "auto";
  els.port.value = "3306";
  els.port.dataset.manual = "false";
  els.charset.value = "utf8mb4";
  syncCharsetDisclosure("utf8mb4");
  document.getElementById("connectionId").value = "";
  setFeedback(els.connectionFeedback, "");
  updateConnectionConfigSummary();
}

function getFormPayload() {
  return {
    id: document.getElementById("connectionId").value.trim(),
    name: document.getElementById("name").value.trim(),
    database_type: els.databaseType.value || "auto",
    host: document.getElementById("host").value.trim(),
    port: Number(els.port.value || 3306),
    username: document.getElementById("username").value.trim(),
    database: els.database.value.trim(),
    charset: els.charset.value.trim() || "utf8mb4",
    password: document.getElementById("password").value,
  };
}

function renderConnectionOptions() {
  const options = [optionNode("", "选择连接")];
  for (const item of state.connections) {
    options.push(optionNode(item.id, item.name, item.id === state.selectedConnectionId));
  }
  els.activeConnection.replaceChildren(...options);
}

function fillForm(item) {
  openPanel(els.connectionFormPanel);
  document.getElementById("connectionId").value = item.id;
  document.getElementById("name").value = item.name;
  els.databaseType.value = item.database_type || "mysql";
  document.getElementById("host").value = item.host;
  els.port.value = item.port;
  els.port.dataset.manual = DEFAULT_PORT_VALUES.has(String(item.port)) ? "false" : "true";
  document.getElementById("username").value = item.username;
  els.database.value = item.database || "";
  els.charset.value = item.charset || "utf8mb4";
  syncCharsetDisclosure(item.charset || "utf8mb4");
  document.getElementById("password").value = "";
  setFeedback(els.connectionFeedback, "已载入连接配置，密码需要重新输入。");
  updateConnectionConfigSummary();
}

function renderConnections() {
  renderConnectionOptions();
  updateConnectionSummary();
  updateConnectionConfigSummary();
  if (!state.connections.length) {
    els.connectionsList.innerHTML = "";
    els.connectionsState.classList.add("hidden");
    setFeedback(els.connectionsState, "");
    return;
  }
  els.connectionsState.classList.add("hidden");
  setFeedback(els.connectionsState, "");
  const template = document.getElementById("connectionCardTemplate");
  const fragment = document.createDocumentFragment();
  for (const item of state.connections) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.classList.toggle("active", item.id === state.selectedConnectionId);
    node.querySelector("h3").textContent = item.name;
    node.querySelector(".connection-meta").textContent = `${databaseTypeLabel(item.database_type)} · ${item.username}@${item.host}:${item.port}${item.database ? ` / ${item.database}` : ""}`;
    node.querySelector('[data-action="edit"]').addEventListener("click", () => fillForm(item));
    node.querySelector('[data-action="use"]').addEventListener("click", () => {
      state.selectedConnectionId = item.id;
      renderConnections();
      fillForm(item);
    });
    node.querySelector('[data-action="delete"]').addEventListener("click", () => deleteConnection(item));
    fragment.appendChild(node);
  }
  els.connectionsList.replaceChildren(fragment);
}

async function loadConnections() {
  els.connectionsState.classList.remove("hidden");
  setFeedback(els.connectionsState, "读取连接列表中…");
  const payload = await api("/connections");
  state.connections = payload.items;
  if (!state.selectedConnectionId && state.connections[0]) {
    state.selectedConnectionId = state.connections[0].id;
  }
  renderConnections();
}

async function saveConnection(event) {
  event.preventDefault();
  const payload = getFormPayload();
  const path = payload.id ? connectionPath(payload.id) : "/connections";
  const method = payload.id ? "PUT" : "POST";
  const result = await api(path, { method, body: JSON.stringify(payload) });
  if (result.item?.id) {
    rememberConnectionPassword(result.item.id, payload.password);
    state.selectedConnectionId = result.item.id;
  }
  resetForm();
  setFeedback(els.connectionFeedback, "连接已保存。", "ok");
  await loadConnections();
}

async function testConnection() {
  const payload = getFormPayload();
  openPanel(els.connectionFormPanel);
  setFeedback(els.connectionFeedback, "正在测试连接…");
  const result = await api("/connections/test", { method: "POST", body: JSON.stringify(payload) });
  const resolvedType = databaseTypeLabel(result.resolved_database_type || result.database_type || payload.database_type);
  if (payload.id) {
    rememberConnectionPassword(payload.id, payload.password);
    state.selectedConnectionId = payload.id;
    renderConnections();
  }
  setFeedback(
    els.connectionFeedback,
    `连接成功。类型: ${resolvedType}；数据库: ${result.database || "未选"}；版本: ${result.version || "未知"}`,
    "ok",
  );
}

async function deleteConnection(item) {
  if (!window.confirm(`删除本地保存的连接“${item.name}”？此操作不会影响数据库。`)) {
    return;
  }
  await api(connectionPath(item.id), { method: "DELETE" });
  delete state.sessionPasswords[item.id];
  if (state.selectedConnectionId === item.id) {
    state.selectedConnectionId = "";
  }
  await loadConnections();
}

function selectedPassword() {
  return state.sessionPasswords[state.selectedConnectionId] || "";
}

function requireSession() {
  if (!state.selectedConnectionId) {
    throw new Error("先选择一个连接。");
  }
  if (!selectedPassword()) {
    throw new Error("先在连接配置里输入密码并测试或保存连接。");
  }
}

function renderList(select, items, key) {
  const options = items.map((item) => {
    const value = key ? item[key] : item;
    return optionNode(value);
  });
  select.replaceChildren(...options);
}

function filterValues(source, keyword, key) {
  const q = keyword.trim().toLowerCase();
  if (!q) {
    return source;
  }
  return source.filter((item) => {
    const value = key ? item[key] : item;
    return String(value).toLowerCase().includes(q);
  });
}

async function loadSchemas() {
  requireSession();
  els.schemaState.classList.remove("hidden");
  setFeedback(els.schemaState, "读取数据库/Schema 列表中…");
  const payload = await api("/schemas", {
    method: "POST",
    timeoutMs: state.queryTimeout,
    body: JSON.stringify({
      connection_id: state.selectedConnectionId,
      password: selectedPassword(),
    }),
  });
  state.schemas = payload.items;
  state.tables = [];
  state.columns = [];
  state.selectedSchema = "";
  state.selectedTable = "";
  renderList(els.schemaList, state.schemas, null);
  els.tableList.innerHTML = "";
  setFeedback(els.schemaState, `已读取 ${state.schemas.length} 个数据库/Schema。`, "ok");

  // Restore last selected schema if available
  restoreLastSelection("schema");
}

async function loadTables(schema) {
  requireSession();
  els.schemaState.classList.remove("hidden");
  state.selectedSchema = schema;
  if (selectedConnectionUsesDatabaseList()) {
    els.database.value = schema;
    saveSelectedConnectionPatch({ database: schema })
      .then(() => loadConnections())
      .catch(handleError);
  }
  setFeedback(els.schemaState, `读取 ${schema} 的表列表中…`);
  const payload = await api("/tables", {
    method: "POST",
    timeoutMs: state.queryTimeout,
    body: JSON.stringify({
      connection_id: state.selectedConnectionId,
      password: selectedPassword(),
      schema,
    }),
  });
  state.tables = payload.items;
  state.columns = [];
  state.selectedTable = "";
  renderFilteredTables();
  setFeedback(els.schemaState, "");
  els.schemaState.classList.add("hidden");

  // Restore last selected table if available
  restoreLastSelection("table");
}

async function loadColumns(table) {
  requireSession();
  state.selectedTable = table;
  setFeedback(els.resultsState, `读取 ${table} 的字段定义中…`);
  const payload = await api("/table-columns", {
    method: "POST",
    timeoutMs: state.queryTimeout,
    body: JSON.stringify({
      connection_id: state.selectedConnectionId,
      password: selectedPassword(),
      schema: state.selectedSchema,
      table,
    }),
  });
  state.columns = payload.items;
  populateColumnControls();
  setFeedback(els.resultsState, `已选中 ${state.selectedSchema}.${table}，可以开始查询。`, "ok");

  // Save this selection
  saveLastSelection();
}

function populateColumnControls() {
  const timeOptions = [optionNode("", "不限制时间")];
  const visibleOptions = [];
  const keywordOptions = [];

  // Get last selections if available
  const lastSelection = getLastSelection();
  const lastTimeColumn = lastSelection?.timeColumn;
  const lastKeywordFields = lastSelection?.keywordFields || [];
  let selectedDefaultTimeColumn = false;

  for (const item of state.columns) {
    const isLastTimeColumn = item.column_name === lastTimeColumn;
    const isLastKeywordField = lastKeywordFields.includes(item.column_name);

    if (item.is_time_like) {
      const selected = lastTimeColumn ? isLastTimeColumn : !selectedDefaultTimeColumn;
      selectedDefaultTimeColumn = selectedDefaultTimeColumn || selected;
      timeOptions.push(optionNode(item.column_name, item.column_name, selected));
    }
    visibleOptions.push(optionNode(item.column_name, item.column_name, true));
    const suffix = item.is_text_searchable ? "" : "（非文本）";
    keywordOptions.push(optionNode(item.column_name, `${item.column_name}${suffix}`, isLastKeywordField));
  }

  els.timeColumn.replaceChildren(...timeOptions);
  els.visibleColumns.replaceChildren(...visibleOptions);
  els.keywordFields.replaceChildren(...keywordOptions);
  syncVisibleColumnsButton();
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

function allOptionsSelected(select) {
  return select.options.length > 0 && Array.from(select.options).every((option) => option.selected);
}

function selectAllVisibleColumns() {
  for (const option of els.visibleColumns.options) {
    option.selected = true;
  }
  syncVisibleColumnsButton();
}

function syncVisibleColumnsButton() {
  const allSelected = allOptionsSelected(els.visibleColumns);
  els.selectAllVisibleColumns.classList.toggle("is-active", allSelected);
}

function parseKeywordTerms(value) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function compactDate(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return [date.getFullYear(), pad(date.getMonth() + 1), pad(date.getDate())].join("");
}

function setRangePreset(days) {
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  els.timeMode.value = "range";
  syncTimeMode();
  els.timeFrom.value = compactDate(start);
  els.timeTo.value = compactDate(end);
  setActivePresetButton(".preset-button", `${days}d`);
}

function setPointPreset(type) {
  const now = new Date();
  els.timeMode.value = "point";
  syncTimeMode();
  setActivePresetButton(".point-button", type);
  if (type === "yesterday") {
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    els.timePoint.value = compactDate(yesterday);
    return;
  }
  els.timePoint.value = compactDate(now);
}

function setActivePresetButton(selector, activeValue) {
  for (const button of document.querySelectorAll(selector)) {
    const value = button.dataset.preset || button.dataset.point;
    button.classList.toggle("is-active", value === activeValue);
  }
}

function clearPresetSelection(selector) {
  for (const button of document.querySelectorAll(selector)) {
    button.classList.remove("is-active");
  }
}

function syncTimeMode() {
  const isPoint = els.timeMode.value === "point";
  for (const node of document.querySelectorAll(".time-range-group")) {
    node.classList.toggle("hidden", isPoint);
  }
  for (const node of document.querySelectorAll(".time-point-group")) {
    node.classList.toggle("hidden", !isPoint);
  }
  if (isPoint) {
    clearPresetSelection(".preset-button");
  } else {
    clearPresetSelection(".point-button");
  }
}

function setTimeMode(mode) {
  els.timeMode.value = mode;
  syncTimeMode();
  for (const button of document.querySelectorAll(".mode-button")) {
    button.classList.toggle("is-active", button.dataset.mode === mode);
  }
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function compareResultValues(left, right, order) {
  const leftText = left == null ? "" : String(left);
  const rightText = right == null ? "" : String(right);
  const leftNumber = Number(leftText);
  const rightNumber = Number(rightText);
  const bothNumeric = leftText !== "" && rightText !== "" && Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
  let result = 0;
  if (bothNumeric) {
    result = leftNumber - rightNumber;
  } else {
    result = leftText.localeCompare(rightText, "zh-CN", { numeric: true, sensitivity: "base" });
  }
  return order === "asc" ? result : -result;
}

function sortedResultRows() {
  const { column, order } = state.resultSort;
  if (!column || !state.resultColumns.includes(column)) {
    return state.resultRows;
  }
  return [...state.resultRows].sort((left, right) => compareResultValues(left[column], right[column], order));
}

function formatElapsedTime(seconds) {
  const roundedSeconds = Math.round(seconds);
  if (roundedSeconds < 60) {
    return `${roundedSeconds} 秒`;
  }
  const minutes = Math.floor(roundedSeconds / 60);
  const remainingSeconds = roundedSeconds % 60;
  return `${minutes} 分 ${remainingSeconds} 秒`;
}

function renderResults(columns, rows, options = {}) {
  const thead = els.resultsTable.querySelector("thead");
  const tbody = els.resultsTable.querySelector("tbody");
  state.resultColumns = columns;
  state.resultRows = rows;
  if (options.resetSort) {
    const nextOrder = options.appliedSort?.order === "asc" ? "asc" : "desc";
    state.resultSort = options.appliedSort && columns.includes(options.appliedSort.column)
      ? { column: options.appliedSort.column, order: nextOrder }
      : { column: "", order: nextOrder };
  }
  if (!rows.length) {
    thead.innerHTML = "";
    tbody.innerHTML = "";
    setFeedback(els.resultsState, "查询完成，但没有匹配结果。");
    els.resultsMeta.textContent = "";
    return;
  }
  const currentRows = sortedResultRows();
  thead.innerHTML = `<tr>${columns.map((name) => {
    const active = state.resultSort.column === name;
    const classes = active ? "sortable is-sorted" : "sortable";
    const width = state.columnWidths[name] || '';
    const widthStyle = width ? ` style="width: ${width}px; min-width: ${width}px; max-width: ${width}px;"` : '';
    return `<th class="${classes}" data-column="${escapeHtml(name)}" tabindex="0"${widthStyle}>${escapeHtml(name)}<div class="resize-handle"></div></th>`;
  }).join("")}</tr>`;
  tbody.innerHTML = currentRows
    .map((row) => `<tr>${columns.map((name) => {
      const text = String(row[name] ?? "");
      const safeText = escapeHtml(text);
      const width = state.columnWidths[name] || '';
      const widthStyle = width ? ` style="width: ${width}px; min-width: ${width}px; max-width: ${width}px;"` : '';
      return `<td title="${safeText}"${widthStyle}><span class="cell-clip">${safeText}</span></td>`;
    }).join("")}</tr>`)
    .join("");

  // Display status message
  if (options.statusMessage) {
    setFeedback(els.resultsState, options.statusMessage, "ok");
  } else {
    const timeInfo = options.elapsed_time ? `，耗时 ${formatElapsedTime(options.elapsed_time)}` : "";
    setFeedback(els.resultsState, `返回 ${rows.length} 行${timeInfo}。`, "ok");
  }

  // Add column sorting
  for (const cell of thead.querySelectorAll("th[data-column]")) {
    const activate = () => {
      const column = cell.dataset.column;
      if (!column) {
        return;
      }
      if (state.resultSort.column === column) {
        state.resultSort.order = state.resultSort.order === "asc" ? "desc" : "asc";
      } else {
        state.resultSort = { column, order: "desc" };
      }
      renderResults(state.resultColumns, state.resultRows);
    };

    // Click handler that checks if resize is happening
    cell.addEventListener("click", (event) => {
      // Don't sort if clicking on resize handle or if recently resized
      const isResizeHandle = event.target.classList.contains('resize-handle');
      const isRecentlyResized = cell.dataset.resizing === 'true';

      if (isResizeHandle || isRecentlyResized) {
        return;
      }
      activate();
    });

    cell.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  }

  // Add column resizing
  initColumnResizing(thead);
}

function validateSearchPayload(payload) {
  if (!state.selectedSchema || !state.selectedTable) {
    throw new Error("先选择数据库/Schema 和表。");
  }
  if (payload.keyword_terms.length && payload.keyword_columns.length === 0) {
    throw new Error("输入关键词时，请至少选择一个关键词字段。");
  }
  if (payload.time_column) {
    if (payload.time_mode === "point" && !payload.time_point) {
      throw new Error("时间点模式下，请填写时间点。");
    }
    if (payload.time_mode === "range" && !payload.time_from && !payload.time_to) {
      throw new Error("时间段模式下，至少填写开始或结束时间。");
    }
  }
}

function setCancelQueryVisible(visible) {
  for (const button of els.cancelQueryButtons) {
    button.classList.toggle("hidden", !visible);
  }
}

function clearResultsView() {
  state.resultColumns = [];
  state.resultRows = [];
  state.resultSort = { column: "", order: "desc" };
  els.resultsMeta.textContent = "";
  els.resultsTable.querySelector("thead").innerHTML = "";
  els.resultsTable.querySelector("tbody").innerHTML = "";
  const cards = document.getElementById("resultsCards");
  if (cards) {
    cards.innerHTML = "";
  }
}

function beginQuery(message) {
  const controller = new AbortController();
  state.activeQueryController = controller;
  setCancelQueryVisible(true);
  clearResultsView();
  els.resultsState.classList.remove("is-success");
  els.resultsState.classList.add("is-loading");
  setFeedback(els.resultsState, message);
  return controller;
}

function finishQuery(controller) {
  if (state.activeQueryController === controller) {
    state.activeQueryController = null;
    setCancelQueryVisible(false);
  }
  els.resultsState.classList.remove("is-loading");
}

function abortActiveQuery() {
  if (!state.activeQueryController) {
    return;
  }
  state.activeQueryController.abort();
  setFeedback(els.resultsState, "正在终止查询…");
}

async function runSearch(event) {
  event.preventDefault();
  requireSession();
  const controller = beginQuery("查询执行中…");

  const payload = {
    connection_id: state.selectedConnectionId,
    password: selectedPassword(),
    schema: state.selectedSchema,
    table: state.selectedTable,
    keyword_terms: parseKeywordTerms(els.keywordTerms.value),
    keyword_columns: selectedValues(els.keywordFields),
    time_column: els.timeColumn.value,
    time_mode: els.timeMode.value,
    time_point: els.timePoint.value.trim(),
    time_from: els.timeFrom.value.trim(),
    time_to: els.timeTo.value.trim(),
    limit: currentLimitValue(),
    sort_by: els.timeColumn.value,
    sort_order: "desc",
    visible_columns: selectedValues(els.visibleColumns),
  };

  try {
    validateSearchPayload(payload);
    const result = await api("/query/search", {
      method: "POST",
      timeoutMs: state.queryTimeout,
      signal: controller.signal,
      abortMessage: "查询已终止。",
      body: JSON.stringify(payload),
    });
    const keywordInfo = payload.keyword_terms.length
      ? ` · 关键词: ${payload.keyword_terms.join(" AND ")} · 字段: ${payload.keyword_columns.join(", ")}`
      : "";
    const timeInfo = result.elapsed_time ? ` · 耗时: ${formatElapsedTime(result.elapsed_time)}` : "";
    const statusMessage = `排序: ${result.applied_sort.column} ${result.applied_sort.order.toUpperCase()} · 上限: ${result.limit}${keywordInfo}${timeInfo}`;

    // Clear the meta line (second line)
    els.resultsMeta.textContent = "";

    renderResultsResponsive(result.columns, result.rows, {
      appliedSort: result.applied_sort,
      resetSort: true,
      statusMessage: statusMessage
    });

    // Show success state briefly
    els.resultsState.classList.add('is-success');
    setTimeout(() => {
      els.resultsState.classList.remove('is-success');
    }, 1200);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("查询已终止。");
    }
    throw error;
  } finally {
    finishQuery(controller);
  }
}

async function runSql() {
  requireSession();
  const controller = beginQuery("SQL 执行中…");

  const payload = {
    connection_id: state.selectedConnectionId,
    password: selectedPassword(),
    sql: els.sqlEditor.value,
  };

  try {
    const result = await api("/query/sql-readonly", {
      method: "POST",
      timeoutMs: state.queryTimeout,
      signal: controller.signal,
      abortMessage: "查询已终止。",
      body: JSON.stringify(payload),
    });
    const timeInfo = result.elapsed_time ? ` · 耗时: ${formatElapsedTime(result.elapsed_time)}` : "";
    const truncationInfo = result.truncated ? ` · 已截断到 ${result.limit} 行` : "";
    const statusMessage = `高级 SQL 结果${timeInfo}${truncationInfo}`;

    // Clear the meta line (second line)
    els.resultsMeta.textContent = "";

    renderResultsResponsive(result.columns, result.rows, {
      resetSort: true,
      statusMessage: statusMessage
    });

    // Show success state briefly
    els.resultsState.classList.add('is-success');
    setTimeout(() => {
      els.resultsState.classList.remove('is-success');
    }, 1200);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("查询已终止。");
    }
    throw error;
  } finally {
    finishQuery(controller);
  }
}

function renderFilteredSchemas() {
  renderList(els.schemaList, filterValues(state.schemas, els.schemaFilter.value, null), null);
}

function renderFilteredTables() {
  renderList(els.tableList, filterValues(state.tables, els.tableFilter.value, "table_name"), "table_name");
}

function toggleSqlMode() {
  state.sqlMode = !state.sqlMode;
  els.sqlPanel.classList.toggle("hidden", !state.sqlMode);
  els.searchForm.classList.toggle("hidden", state.sqlMode);
  els.toggleSqlButton.textContent = state.sqlMode ? "返回表单" : "高级 SQL";
  els.toggleSqlButton.classList.toggle("active-toggle", state.sqlMode);
  if (state.sqlMode) {
    els.sqlEditor.focus();
  }
}

// Save and restore last selections
function saveLastSelection() {
  if (!state.selectedConnectionId) {
    return;
  }
  const selection = {
    schema: state.selectedSchema,
    table: state.selectedTable,
    timeColumn: els.timeColumn.value,
    keywordFields: selectedValues(els.keywordFields),
  };
  state.lastSelections[state.selectedConnectionId] = selection;
  try {
    localStorage.setItem("logscope_last_selections", JSON.stringify(state.lastSelections));
  } catch (e) {
    // Ignore localStorage errors
  }
}

function getLastSelection() {
  if (!state.selectedConnectionId) {
    return null;
  }
  return state.lastSelections[state.selectedConnectionId] || null;
}

function loadLastSelections() {
  try {
    const stored = localStorage.getItem("logscope_last_selections");
    if (stored) {
      state.lastSelections = JSON.parse(stored);
    }
  } catch (e) {
    // Ignore localStorage errors
    state.lastSelections = {};
  }
}

async function restoreLastSelection(type) {
  const lastSelection = getLastSelection();
  if (!lastSelection) {
    return;
  }

  if (type === "schema" && lastSelection.schema) {
    if (state.schemas.includes(lastSelection.schema)) {
      els.schemaList.value = lastSelection.schema;
      await loadTables(lastSelection.schema).catch(handleError);
    }
  } else if (type === "table" && lastSelection.table) {
    const tableExists = state.tables.some((t) => t.table_name === lastSelection.table);
    if (tableExists) {
      els.tableList.value = lastSelection.table;
      await loadColumns(lastSelection.table).catch(handleError);
    }
  }
}

async function bootstrap() {
  try {
    await api("/health");
    loadLastSelections();
    await loadConnections();
    setLimitValue(100);
    syncDatabaseTypeControls();
    syncTimeMode();
  } catch (error) {
    setFeedback(els.connectionsState, error.message, "error");
  }
}

function bindEvents() {
  els.connectionForm.addEventListener("submit", (event) => saveConnection(event).catch(handleError));
  document.getElementById("testConnectionButton").addEventListener("click", () => testConnection().catch(handleError));
  document.getElementById("resetFormButton").addEventListener("click", resetForm);
  document.getElementById("newConnectionButton").addEventListener("click", () => {
    resetForm();
    openPanel(els.connectionFormPanel);
  });
  document.getElementById("loadSchemasButton").addEventListener("click", () => loadSchemas().catch(handleError));
  els.toggleSqlButton.addEventListener("click", toggleSqlMode);
  document.getElementById("runSqlButton").addEventListener("click", () => runSql().catch(handleError));
  els.searchForm.addEventListener("submit", (event) => runSearch(event).catch(handleError));
  for (const button of els.cancelQueryButtons) {
    button.addEventListener("click", abortActiveQuery);
  }
  els.activeConnection.addEventListener("change", (event) => {
    state.selectedConnectionId = event.target.value;
    renderConnections();
  });
  els.limit.addEventListener("input", updateLimitValue);
  els.visibleColumns.addEventListener("change", syncVisibleColumnsButton);
  els.selectAllVisibleColumns.addEventListener("click", selectAllVisibleColumns);
  els.databaseType.addEventListener("change", () => syncDatabaseTypeControls({ applyPort: true }));
  els.port.addEventListener("input", () => {
    els.port.dataset.manual = "true";
  });
  els.charset.addEventListener("change", () => syncCharsetDisclosure(els.charset.value));
  els.schemaFilter.addEventListener("input", renderFilteredSchemas);
  els.tableFilter.addEventListener("input", renderFilteredTables);
  els.schemaList.addEventListener("change", (event) => loadTables(event.target.value).catch(handleError));
  els.tableList.addEventListener("change", (event) => loadColumns(event.target.value).catch(handleError));
  els.timeColumn.addEventListener("change", saveLastSelection);
  els.keywordFields.addEventListener("change", saveLastSelection);

  // Close disclosures when clicking outside
  document.addEventListener("click", (event) => {
    const charsetDisclosure = document.getElementById("charsetDisclosure");
    if (charsetDisclosure && charsetDisclosure.open) {
      if (!charsetDisclosure.contains(event.target)) {
        charsetDisclosure.open = false;
      }
    }

    const timeoutDisclosure = document.getElementById("timeoutDisclosure");
    if (timeoutDisclosure && timeoutDisclosure.open) {
      if (!timeoutDisclosure.contains(event.target)) {
        timeoutDisclosure.open = false;
      }
    }
  });

  // Timeout option buttons
  for (const button of document.querySelectorAll(".timeout-option")) {
    button.addEventListener("click", () => {
      const timeout = parseInt(button.dataset.timeout, 10);
      state.queryTimeout = timeout;

      // Update active state
      document.querySelectorAll(".timeout-option").forEach(btn => {
        btn.classList.remove("is-active");
      });
      button.classList.add("is-active");

      // Close the disclosure
      const timeoutDisclosure = document.getElementById("timeoutDisclosure");
      if (timeoutDisclosure) {
        timeoutDisclosure.open = false;
      }
    });
  }

  for (const button of document.querySelectorAll(".mode-button")) {
    button.addEventListener("click", () => setTimeMode(button.dataset.mode));
  }
  for (const button of document.querySelectorAll(".preset-button")) {
    button.addEventListener("click", () => {
      const value = button.dataset.preset;
      if (value === "1d") {
        setRangePreset(1);
      } else if (value === "3d") {
        setRangePreset(3);
      } else if (value === "7d") {
        setRangePreset(7);
      }
    });
  }
  for (const button of document.querySelectorAll(".point-button")) {
    button.addEventListener("click", () => setPointPreset(button.dataset.point));
  }
}

// Responsive: detect mobile and render cards instead of table
function isMobile() {
  return window.innerWidth <= 720;
}

function renderResultCards(columns, rows) {
  const container = document.getElementById("resultsCards");
  if (!container) return;

  if (!rows.length) {
    container.innerHTML = "";
    return;
  }

  const fragment = document.createDocumentFragment();

  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "result-card";

    // Header: show first column (usually ID or timestamp)
    const header = document.createElement("header");
    const firstCol = columns[0];
    header.innerHTML = `
      <strong>${escapeHtml(firstCol)}</strong>
      <span>${escapeHtml(String(row[firstCol] ?? ""))}</span>
    `;
    card.appendChild(header);

    // Body: remaining columns as dl
    const dl = document.createElement("dl");
    for (let i = 1; i < columns.length; i++) {
      const col = columns[i];
      const dt = document.createElement("dt");
      dt.textContent = col;
      const dd = document.createElement("dd");
      dd.textContent = String(row[col] ?? "");
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
    card.appendChild(dl);

    fragment.appendChild(card);
  }

  container.innerHTML = "";
  container.appendChild(fragment);
}

// Enhanced render that supports both table and cards
function renderResultsResponsive(columns, rows, options = {}) {
  if (isMobile()) {
    // Mobile: render cards
    renderResultCards(columns, rows);
    els.resultsTable.querySelector("thead").innerHTML = "";
    els.resultsTable.querySelector("tbody").innerHTML = "";
  } else {
    // Desktop: render table
    renderResults(columns, rows, options);
    const container = document.getElementById("resultsCards");
    if (container) container.innerHTML = "";
  }
}

// Performance hint calculator
function calculatePerformanceHint(queryParams) {
  let score = 100;
  let hints = [];

  // Check time filter
  if (!queryParams.timeColumn || (!queryParams.timeFrom && !queryParams.timeTo && !queryParams.timePoint)) {
    score -= 40;
    hints.push({ type: "warning", text: "未设置时间范围会全表扫描，可能较慢" });
  } else {
    hints.push({ type: "ok", text: "✓ 已优化，预计 2-5 秒" });
  }

  // Check keyword fields count
  const keywordFieldsCount = queryParams.keywordColumns?.length || 0;
  if (keywordFieldsCount > 5) {
    score -= 20;
    hints.push({ type: "warning", text: `搜索 ${keywordFieldsCount} 个字段可能较慢` });
  }

  // Check limit
  if (queryParams.limit > 200) {
    score -= 10;
    hints.push({ type: "warning", text: "大结果集可能较慢" });
  }

  return { score, hints };
}

// Column resizing functionality
function initColumnResizing(thead) {
  const handles = thead.querySelectorAll('.resize-handle');

  handles.forEach((handle) => {
    let startX = 0;
    let startWidth = 0;
    let th = null;
    let colIndex = 0;
    let columnName = '';
    let hasMoved = false;

    const onMouseDown = (e) => {
      e.preventDefault();
      e.stopPropagation();

      th = handle.parentElement;
      columnName = th.dataset.column;
      colIndex = Array.from(th.parentElement.children).indexOf(th);
      startX = e.pageX;
      startWidth = th.offsetWidth;
      hasMoved = false;

      th.classList.add('resizing');
      th.dataset.resizing = 'true';
      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    };

    const onMouseMove = (e) => {
      if (!th) return;

      hasMoved = true;
      const diff = e.pageX - startX;
      const newWidth = Math.max(50, startWidth + diff);

      // Set width on header
      th.style.width = `${newWidth}px`;
      th.style.minWidth = `${newWidth}px`;
      th.style.maxWidth = `${newWidth}px`;

      // Set width on all corresponding cells in tbody
      const tbody = els.resultsTable.querySelector('tbody');
      const rows = tbody.querySelectorAll('tr');
      rows.forEach(row => {
        const cell = row.children[colIndex];
        if (cell) {
          cell.style.width = `${newWidth}px`;
          cell.style.minWidth = `${newWidth}px`;
          cell.style.maxWidth = `${newWidth}px`;
        }
      });
    };

    const onMouseUp = () => {
      if (th) {
        th.classList.remove('resizing');
        // Save the final width to state
        const finalWidth = th.offsetWidth;
        if (columnName) {
          state.columnWidths[columnName] = finalWidth;
        }
        // Keep the resizing flag briefly to prevent sort trigger
        if (hasMoved) {
          setTimeout(() => {
            th.dataset.resizing = 'false';
          }, 100);
        } else {
          th.dataset.resizing = 'false';
        }
      }
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      th = null;
    };

    handle.addEventListener('mousedown', onMouseDown);
  });
}

bindEvents();
bootstrap();
