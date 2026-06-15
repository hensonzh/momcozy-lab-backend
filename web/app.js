const messages = document.querySelector("#messages");
window.MOMCOZY_APP_BUILD_ID = "form-id-default-guard-20260531";
const form = document.querySelector("#composer");
const input = document.querySelector("#message");
const send = document.querySelector("#send");
const reset = document.querySelector("#reset");
const attachImage = document.querySelector("#attach-image");
const imageInput = document.querySelector("#image-input");
const imagePreviewStrip = document.querySelector("#image-preview-strip");
const conversationLabel = document.querySelector("#conversation");
const skillsLabel = document.querySelector("#skills");

const THINKING_EVENT_NAME = "momcozy.agent.thinking";
const MAX_IMAGE_ATTACHMENTS = 4;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const CLIENT_USER_ID_KEY = "momcozy_user_id";
const IMAGE_VIEWER_MIN_ZOOM = 1;
const IMAGE_VIEWER_MAX_ZOOM = 3;
const IMAGE_VIEWER_ZOOM_STEP = 0.25;
const IBCLC_CONSULT_COMPLETED_KEY = "momcozy_ibclc_consult_completed";
const MOMCOZY_LOGO_SRC = "/momcozy_logo.png";
const HOSPITAL_BAG_REASON_SUPPRESSED_ITEM_LABELS = new Set([
  "检查报告/化验单",
  "医院预登记信息",
  "紧急联系人信息",
  "医生/医院联系电话",
  "手机充电线和充电器",
  "医院路线和停车信息",
  "夜间入口信息",
]);
const HOSPITAL_BAG_REASON_SUPPRESSED_GROUP_IDS = new Set(["support_person_bag"]);
const NEW_CONVERSATION_GREETING =
  "你好呀，我在。\n\n这次想先聊哪件事？你可以直接说现在最困扰你的情况，不管是孕期准备、产后恢复、喂养奶量，还是设备使用，我都会陪你一步步理清楚。";
const DOWNLOAD_ICON_SVG = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 3v11" />
    <path d="m7 10 5 5 5-5" />
    <path d="M5 21h14" />
  </svg>
`;

let conversationId = localStorage.getItem("momcozy_conversation_id") || "";
let clientUserId = getOrCreateClientUserId();
let runCount = Number(localStorage.getItem("momcozy_run_count") || "0");
let pendingImages = [];
let imageViewer = null;

function getOrCreateClientUserId() {
  const existing = localStorage.getItem(CLIENT_USER_ID_KEY);
  if (existing) return existing;
  const generated = `user_${crypto.randomUUID()}`;
  localStorage.setItem(CLIENT_USER_ID_KEY, generated);
  return generated;
}

function addMessage(role, text, options = {}) {
  const row = addMessageRow(role);
  const node = document.createElement("div");
  node.className = `message ${role}`;
  if (role === "user" && options.images?.length) {
    appendMessageImages(node, options.images);
  }
  if (role === "assistant") {
    setAssistantMarkdown(node, text);
  } else if (text) {
    const textNode = document.createElement("div");
    textNode.textContent = text;
    node.appendChild(textNode);
  }
  row.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function addNewConversationGreeting() {
  addMessage("assistant", NEW_CONVERSATION_GREETING);
}

function appendMessageImages(node, images) {
  const strip = document.createElement("div");
  strip.className = "message-images";
  for (const image of images) {
    const img = document.createElement("img");
    img.className = "message-image";
    img.src = image.image_url;
    img.alt = image.name || "Attached image";
    enableImageViewer(img);
    strip.appendChild(img);
  }
  node.appendChild(strip);
}

function setAssistantMarkdown(node, text) {
  node._rawMarkdown = text || "";
  renderAssistantMarkdown(node);
}

function appendAssistantMarkdown(node, delta) {
  node._rawMarkdown = `${node._rawMarkdown || ""}${delta || ""}`;
  renderAssistantMarkdown(node);
}

function getAssistantMarkdown(node) {
  return node?._rawMarkdown || node?.textContent || "";
}

function renderAssistantMarkdown(node) {
  const markdown = node._rawMarkdown || "";
  if (!window.marked || !window.DOMPurify) {
    node.classList.remove("has-markdown-image");
    node.textContent = markdown;
    return;
  }
  const rawHtml = window.marked.parse(markdown, {
    async: false,
    breaks: true,
    gfm: true,
  });
  node.innerHTML = window.DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true },
  });
  const markdownImages = node.querySelectorAll("img");
  node.classList.toggle("has-markdown-image", markdownImages.length > 0);
  const markdownTables = node.querySelectorAll("table");
  node.classList.toggle("has-markdown-table", markdownTables.length > 0);
  markdownImages.forEach((img) => {
    img.decoding = "async";
    enableImageViewer(img);
    img.addEventListener("load", () => {
      messages.scrollTop = messages.scrollHeight;
    }, { once: true });
  });
}

function enableImageViewer(img) {
  if (!img || img.dataset.viewerEnabled === "true") return;
  img.dataset.viewerEnabled = "true";
  img.tabIndex = 0;
  img.setAttribute("role", "button");
  img.setAttribute("aria-label", img.alt ? `查看图片：${img.alt}` : "查看图片");
  img.addEventListener("click", () => openImageViewer(img.src, img.alt || "Image"));
  img.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openImageViewer(img.src, img.alt || "Image");
    }
  });
}

function createImageViewer() {
  if (imageViewer) return imageViewer;
  const overlay = document.createElement("div");
  overlay.className = "image-viewer";
  overlay.hidden = true;
  overlay.innerHTML = `
    <div class="image-viewer-toolbar" role="toolbar" aria-label="图片查看工具">
      <button class="image-viewer-button" type="button" data-action="zoom-out" aria-label="缩小">-</button>
      <button class="image-viewer-button" type="button" data-action="reset" aria-label="重置缩放">1x</button>
      <button class="image-viewer-button" type="button" data-action="zoom-in" aria-label="放大">+</button>
      <button class="image-viewer-button" type="button" data-action="close" aria-label="关闭">Close</button>
    </div>
    <div class="image-viewer-stage">
      <img class="image-viewer-img" alt="" />
    </div>
  `;
  const img = overlay.querySelector(".image-viewer-img");
  const stage = overlay.querySelector(".image-viewer-stage");
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeImageViewer();
  });
  overlay.querySelector('[data-action="close"]').addEventListener("click", closeImageViewer);
  overlay.querySelector('[data-action="zoom-in"]').addEventListener("click", () => setImageViewerZoom(imageViewer.zoom + IMAGE_VIEWER_ZOOM_STEP));
  overlay.querySelector('[data-action="zoom-out"]').addEventListener("click", () => setImageViewerZoom(imageViewer.zoom - IMAGE_VIEWER_ZOOM_STEP));
  overlay.querySelector('[data-action="reset"]').addEventListener("click", () => setImageViewerZoom(1));
  stage.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    setImageViewerZoom(imageViewer.zoom + direction * IMAGE_VIEWER_ZOOM_STEP);
  }, { passive: false });
  document.body.appendChild(overlay);
  imageViewer = { overlay, img, stage, zoom: 1 };
  return imageViewer;
}

function openImageViewer(src, alt) {
  const viewer = createImageViewer();
  viewer.img.src = src;
  viewer.img.alt = alt || "Image";
  viewer.overlay.hidden = false;
  document.body.classList.add("image-viewer-open");
  setImageViewerZoom(1);
  viewer.overlay.querySelector('[data-action="close"]').focus();
}

function closeImageViewer() {
  if (!imageViewer) return;
  imageViewer.overlay.hidden = true;
  imageViewer.img.removeAttribute("src");
  document.body.classList.remove("image-viewer-open");
}

function setImageViewerZoom(value) {
  if (!imageViewer) return;
  const zoom = Math.min(IMAGE_VIEWER_MAX_ZOOM, Math.max(IMAGE_VIEWER_MIN_ZOOM, value));
  imageViewer.zoom = zoom;
  imageViewer.img.style.width = `${zoom * 100}%`;
  imageViewer.overlay.querySelector('[data-action="reset"]').textContent = `${Math.round(zoom * 100)}%`;
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && imageViewer && !imageViewer.overlay.hidden) {
    closeImageViewer();
  }
});

function addMessageRow(role) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;
  messages.appendChild(row);
  return row;
}

function addToolNote(toolResults) {
  if (!toolResults || toolResults.length === 0) return;
  const count = new Set(toolResults.map((tool) => tool.name).filter(Boolean)).size;
  if (!count) return;
  const node = document.createElement("div");
  node.className = "tool-note";
  node.textContent = `已完成 ${count} 个处理步骤`;
  messages.appendChild(node);
}

function addStatusNode() {
  const row = addMessageRow("status");
  const node = document.createElement("div");
  node.className = "status-note";
  node.innerHTML = `
    <span class="status-dot" aria-hidden="true"></span>
    <span class="status-text">正在处理...</span>
    <span class="status-tools"></span>
  `;
  row.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function addWorkPanel(startedAt = Date.now()) {
  const row = addMessageRow("work");
  const node = document.createElement("div");
  node.className = "work-panel";
  node._startedAt = startedAt;
  node._finished = false;
  node.innerHTML = `
    <button class="work-header" type="button" aria-expanded="true">
      <span class="work-caret" aria-hidden="true"></span>
      <span class="work-title">Working for 0s</span>
    </button>
    <div class="work-body">
      <ol class="work-list"></ol>
    </div>
  `;
  const header = node.querySelector(".work-header");
  header?.addEventListener("click", () => {
    const collapsed = node.classList.toggle("collapsed");
    header.setAttribute("aria-expanded", String(!collapsed));
  });
  node._timer = window.setInterval(() => updateWorkDuration(node), 1000);
  updateWorkDuration(node);
  row.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function createWorkRun() {
  return {
    startedAt: Date.now(),
    panel: null,
    finished: false,
    hasOutput: false,
    hasAction: false,
    thinkingVisible: false,
    thinkingNode: null,
  };
}

function ensureWorkPanel(run) {
  if (!run) return null;
  if (!run.panel) {
    run.panel = addWorkPanel(run.startedAt);
  }
  return run.panel;
}

function updateWorkDuration(workPanel, final = false) {
  const title = workPanel?.querySelector(".work-title");
  if (!title) return;
  const elapsed = Date.now() - (workPanel._startedAt || Date.now());
  title.textContent = `${final ? "Worked" : "Working"} for ${formatDuration(elapsed)}`;
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (!minutes) return `${seconds}s`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

function showThinking(run, options = {}) {
  if (!run || run.finished || run.thinkingVisible) return;
  const panel = options.afterOutput ? null : run.hasAction ? ensureWorkPanel(run) : run.panel;
  const row = document.createElement("div");
  row.className = "message-row thinking";
  const node = document.createElement("div");
  node.className = "thinking-note";
  node.innerHTML = `<span class="thinking-title">${options.title || "正在思考"}</span>`;
  row.appendChild(node);
  const panelRow = panel?.closest(".message-row");
  if (panelRow) {
    panelRow.insertAdjacentElement("afterend", row);
  } else {
    messages.appendChild(row);
  }
  run.thinkingNode = node;
  run.thinkingVisible = true;
  messages.scrollTop = messages.scrollHeight;
}

function completeThinking(run) {
  if (!run) return;
  removeMessageElement(run.thinkingNode);
  run.thinkingNode = null;
  run.thinkingVisible = false;
}

function finishWorkPanel(run, options = {}) {
  if (!run || run.finished) return;
  run.finished = true;
  completeThinking(run);
  const workPanel = options.failed ? ensureWorkPanel(run) : run.panel;
  if (!workPanel || workPanel._finished) return;
  if (!options.failed && !run.hasAction) {
    if (workPanel._timer) {
      window.clearInterval(workPanel._timer);
      workPanel._timer = null;
    }
    removeMessageElement(workPanel);
    run.panel = null;
    return;
  }
  workPanel._finished = true;
  if (workPanel._timer) {
    window.clearInterval(workPanel._timer);
    workPanel._timer = null;
  }
  updateWorkDuration(workPanel, true);
  if (options.failed) {
    upsertWorkItem(workPanel, {
      key: "run-error",
      status: "failed",
      title: "执行中断",
      detail: options.message || "请求处理失败，请稍后重试。",
    });
  } else {
    settleRunningWorkItems(workPanel);
    workPanel.classList.add("collapsed");
    workPanel.querySelector(".work-header")?.setAttribute("aria-expanded", "false");
  }
}

function addWorkToolStart(run, event) {
  if (!run || !event?.tool_call_id) return;
  run.hasAction = true;
  completeThinking(run);
  const workPanel = ensureWorkPanel(run);
  const toolName = normalizeToolName(event.tool_call_name || "tool");
  const copy = toolStartCopy(toolName);
  upsertWorkItem(workPanel, {
    key: workItemKeyForToolCall(event),
    aliases: workItemKeysForToolCall(event),
    toolName,
    status: "running",
    title: copy.title,
  });
}

function addWorkToolArgs(run, event, toolName) {
  if (!run || !event?.tool_call_id) return;
  run.hasAction = true;
  completeThinking(run);
  const workPanel = ensureWorkPanel(run);
  const normalizedToolName = normalizeToolName(toolName || event.tool_call_name || "tool");
  const list = workPanel?.querySelector(".work-list");
  const aliases = new Set(workItemKeysForToolCall(event));
  if (
    list &&
    (findWorkItemByAliases(list, aliases) || findRunningWorkItemByToolName(list, normalizedToolName))
  ) {
    return;
  }
  const copy = toolStartCopy(normalizedToolName);
  upsertWorkItem(workPanel, {
    key: workItemKeyForToolCall(event),
    aliases: workItemKeysForToolCall(event),
    toolName: normalizedToolName,
    status: "running",
    title: copy.title,
    detail: copy.detail,
    mergeRunning: true,
  });
}

function addWorkConfirmationRequired(run, event) {
  if (!run) return;
  run.hasAction = true;
  completeThinking(run);
  const workPanel = ensureWorkPanel(run);
  const title = "请确认后继续";
  const detail = "相关内容已准备好，等待你确认。";
  upsertWorkItem(workPanel, {
    key: event?.confirmation_id || `confirmation:${event?.tool_call_id || Date.now()}`,
    aliases: [event?.tool_call_id ? `tool:${event.tool_call_id}` : ""].filter(Boolean),
    toolName: normalizeToolName(event?.tool_call_name || ""),
    status: "completed",
    title,
    detail,
  });
}

function addArtifactFromEvent(event) {
  const artifact = event?.artifact;
  const artifactType = normalizeArtifactType(event?.artifact_type || "");
  const toolName = normalizeToolName(event?.tool_call_name || "");
  if (!artifact || typeof artifact !== "object") return "";
  if (artifactType === "form") {
    addFormCard(artifact);
    return "form";
  }
  if (artifactType === "support_ticket") {
    addSupportTicketDraft({
      ticket: artifact,
      submit_label: event?.submit_label || "确认并提交",
    });
    return "form";
  }
  if (toolName === "ibclc_consult_card_create" || artifactType === "ibclc_consult") {
    addIbclcConsultCard(artifact);
    return "card";
  }
  addCard(artifact);
  return "card";
}

function normalizeArtifactType(value) {
  const token = String(value || "").trim();
  if (!token) return "";
  if (token === "support-ticket") return "support_ticket";
  return token;
}

function addWorkToolEnd(run, event, toolName) {
  if (!run || !event?.tool_call_id) return;
  run.hasAction = true;
  completeThinking(run);
  const workPanel = ensureWorkPanel(run);
  const normalizedToolName = normalizeToolName(toolName || event.tool_call_name || "tool");
  const copy = toolEndCopy(normalizedToolName);
  upsertWorkItem(workPanel, {
    key: workItemKeyForToolCall(event),
    aliases: workItemKeysForToolCall(event),
    toolName: normalizedToolName,
    status: "running",
    title: copy.title,
    detail: copy.detail,
    mergeRunning: true,
  });
}

function addWorkToolResult(run, event, result, toolName) {
  if (!run || !event?.tool_call_id) return;
  run.hasAction = true;
  completeThinking(run);
  const workPanel = ensureWorkPanel(run);
  const normalizedToolName = normalizeToolName(toolName || result?.tool_name || event.tool_call_name || "tool");
  const copy = toolResultCopy(normalizedToolName, result || {});
  upsertWorkItem(workPanel, {
    key: workItemKeyForToolCall(event),
    aliases: workItemKeysForToolCall(event),
    toolName: normalizedToolName,
    status: result?.ok === false ? "failed" : "completed",
    title: copy.title,
    detail: copy.detail,
    mergeRunning: true,
  });
}

function workItemKeyForToolCall(event) {
  return workItemKeysForToolCall(event)[0] || "tool:unknown";
}

function workItemKeysForToolCall(event) {
  const keys = [];
  if (event?.response_id && event.output_index !== undefined && event.output_index !== null) {
    keys.push(`tool:${event.response_id}:${event.output_index}`);
  }
  if (event?.tool_call_id) keys.push(`tool:${event.tool_call_id}`);
  if (event?.item_id) keys.push(`tool:${event.item_id}`);
  return [...new Set(keys)];
}

function upsertWorkItem(workPanel, itemData) {
  const list = workPanel?.querySelector(".work-list");
  if (!list) return null;

  const key = itemData.key || `item:${Date.now()}:${Math.random()}`;
  const aliases = new Set([key, ...(itemData.aliases || [])].filter(Boolean));
  const escapedKey = cssEscape(key);
  let existingItem = null;
  if (itemData.mergeRunning) {
    existingItem = list.querySelector(`.work-item.running[data-work-key="${escapedKey}"]`);
  }
  if (!existingItem) {
    existingItem = list.querySelector(`.work-item[data-work-key="${escapedKey}"]`);
  }
  if (!existingItem && aliases.size) {
    existingItem = findWorkItemByAliases(list, aliases, itemData.mergeRunning ? "running" : "");
  }
  if (!existingItem && itemData.mergeRunning && itemData.toolName) {
    existingItem = findRunningWorkItemByToolName(list, itemData.toolName);
  }
  const item = existingItem || document.createElement("li");

  if (!existingItem) {
    const marker = document.createElement("span");
    marker.className = "work-marker";
    marker.setAttribute("aria-hidden", "true");
    item.appendChild(marker);
  }
  item.className = `work-item ${itemData.status || "running"}`;
  item.dataset.workKey = key;
  item.dataset.workAliases = mergeWorkAliases(item.dataset.workAliases, aliases);
  if (itemData.toolName) item.dataset.toolName = itemData.toolName;

  let body = item.querySelector(".work-item-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "work-item-body";
    item.appendChild(body);
  }
  body.innerHTML = "";

  const title = document.createElement("div");
  title.className = "work-item-title";
  title.textContent = itemData.title || "处理中";
  body.appendChild(title);

  if (itemData.detail) {
    const detail = document.createElement("div");
    detail.className = "work-detail";
    detail.textContent = itemData.detail;
    body.appendChild(detail);
  }

  if (!existingItem) {
    list.appendChild(item);
  }

  messages.scrollTop = messages.scrollHeight;
  return item;
}

function findWorkItemByAliases(list, aliases, requiredStatus = "") {
  for (const item of list.querySelectorAll(".work-item")) {
    if (requiredStatus && !item.classList.contains(requiredStatus)) continue;
    const itemAliases = parseWorkAliases(item.dataset.workAliases);
    if ([...aliases].some((alias) => itemAliases.has(alias))) {
      return item;
    }
  }
  return null;
}

function findRunningWorkItemByToolName(list, toolName) {
  for (const item of list.querySelectorAll(".work-item.running")) {
    if (item.dataset.toolName === toolName) return item;
  }
  return null;
}

function mergeWorkAliases(currentValue, aliases) {
  const merged = parseWorkAliases(currentValue);
  for (const alias of aliases) {
    merged.add(alias);
  }
  return [...merged].join(" ");
}

function parseWorkAliases(value) {
  return new Set(String(value || "").split(/\s+/).filter(Boolean));
}

function settleRunningWorkItems(workPanel) {
  for (const item of workPanel?.querySelectorAll(".work-item.running") || []) {
    item.classList.remove("running");
    item.classList.add("completed");
  }
}

function addStructuredArtifact(kind, label) {
  const row = addMessageRow("artifact");
  const node = document.createElement("section");
  node.className = `structured-artifact structured-artifact-${kind}`;

  if (label) {
    const header = document.createElement("div");
    header.className = "artifact-header";

    const artifactLabel = document.createElement("div");
    artifactLabel.className = "artifact-label";
    artifactLabel.textContent = label;
    header.appendChild(artifactLabel);
    node.appendChild(header);
  }

  const body = document.createElement("div");
  body.className = "artifact-body";
  node.appendChild(body);

  row.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return body;
}

function addFormCard(formSpec) {
  const body = addStructuredArtifact("form", "信息采集表");
  const node = document.createElement("form");
  node.className = "agent-form";
  node.dataset.formId = formSpec.id || "form";
  const normalizedFormSpec = normalizeFormSpec(formSpec);

  const title = document.createElement("h2");
  title.textContent = normalizedFormSpec.title || "Confirm details";
  node.appendChild(title);

  if (normalizedFormSpec.description) {
    const description = document.createElement("p");
    description.className = "agent-form-description";
    description.textContent = normalizedFormSpec.description;
    node.appendChild(description);
  }

  const fieldGroups = groupFormFields(normalizedFormSpec.fields || []);
  fieldGroups.forEach((group, groupIndex) => {
    if (group.title) {
      node.appendChild(createFormSection(group, normalizedFormSpec.id, groupIndex));
      return;
    }
    for (const field of group.fields) {
      node.appendChild(createFormField(field));
    }
  });

  const actions = document.createElement("div");
  actions.className = "agent-form-actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = formSpec.submit_label || "Confirm";
  actions.appendChild(submit);
  node.appendChild(actions);

  node.addEventListener("submit", (event) => {
    event.preventDefault();
    const validationMessage = validateFormSelection(node, normalizedFormSpec);
    if (validationMessage) {
      addMessage("error", validationMessage);
      return;
    }
    const values = collectFormValues(node, normalizedFormSpec);
    node.classList.add("submitted");
    for (const element of node.elements) {
      element.disabled = true;
    }
    const summary = buildFormConfirmationMessage(normalizedFormSpec, values);
    sendUserText(summary, { displayText: `已提交：${normalizedFormSpec.title || "表单"}` });
  });

  body.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function addSupportTicketDraft(ticketPayload) {
  const ticket = ticketPayload.ticket || {};
  const body = addStructuredArtifact("support-ticket", "");
  const node = document.createElement("form");
  node.className = "agent-form support-ticket-form";
  node.dataset.ticketDraftId = ticket.draft_id || "";

  const title = document.createElement("h2");
  title.textContent = "售后工单";
  node.appendChild(title);

  const fields = supportTicketFields(ticket);
  for (const field of fields) {
    node.appendChild(createFormField(field));
  }

  const actions = document.createElement("div");
  actions.className = "agent-form-actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = ticketPayload.submit_label || "确认并提交";
  actions.appendChild(submit);
  node.appendChild(actions);

  node.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = normalizeSupportTicketValues(collectFormValues(node, { fields }));
    submit.disabled = true;
    submit.textContent = "发送中...";
    try {
      const result = await submitSupportTicket(values);
      node.classList.add("submitted");
      for (const element of node.elements) {
        element.disabled = true;
      }
      markSupportTicketSubmitted(node, submit, result);
      await sendUserText(buildSupportTicketSubmittedMessage(values, result), { displayText: "已提交售后工单" });
    } catch (error) {
      addMessage("error", error.message || "工单提交失败，请稍后重试。");
      submit.disabled = false;
      submit.textContent = ticketPayload.submit_label || "确认并提交";
    }
  });

  body.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function supportTicketFields(ticket) {
  return [
    {
      id: "issue_type",
      label: "问题类型",
      type: "select",
      required: true,
      default_value: supportTicketIssueLabel(ticket.issue_type),
      options: ["设备故障", "缺少配件", "疑似质量问题", "保修", "退换货/退款", "订单/物流", "使用帮助", "安全问题", "其他"],
    },
    {
      id: "issue_summary",
      label: "问题描述",
      type: "textarea",
      required: true,
      default_value: ticket.issue_summary || "",
      placeholder: "简单描述你遇到的问题",
    },
    {
      id: "product_model",
      label: "产品型号",
      type: "text",
      required: false,
      default_value: ticket.product_model || "",
      placeholder: "例如：M5、S12 Pro，或暂不确定",
    },
    {
      id: "urgency",
      label: "紧急程度",
      type: "select",
      required: true,
      default_value: supportTicketUrgencyLabel(ticket.urgency),
      options: ["普通", "较急", "安全相关"],
    },
  ];
}

function supportTicketIssueLabel(value) {
  const labels = {
    malfunction: "设备故障",
    missing_parts: "缺少配件",
    defect: "疑似质量问题",
    warranty: "保修",
    return_or_refund: "退换货/退款",
    order_or_shipping: "订单/物流",
    usage_help: "使用帮助",
    safety_concern: "安全问题",
    other: "其他",
  };
  return labels[value] || "其他";
}

function supportTicketUrgencyLabel(value) {
  const labels = {
    normal: "普通",
    high: "较急",
    safety: "安全相关",
  };
  return labels[value] || "普通";
}

function normalizeSupportTicketValues(values) {
  return {
    ...values,
    troubleshooting_done: String(values.troubleshooting_done || "")
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

async function submitSupportTicket(ticket) {
  const timezone = clientTimezone();
  const messageSentAt = clientMessageSentAt();
  const response = await fetch("/api/support-ticket-submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ticket,
      thread_id: conversationId,
      user_id: clientUserId,
      locale: clientLocale(),
      timezone,
      message_sent_at: messageSentAt,
      idempotency_key: crypto.randomUUID(),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed with ${response.status}`);
  }
  return data;
}

function markSupportTicketSubmitted(form, submit, result) {
  submit.textContent = "已提交";
  messages.scrollTop = messages.scrollHeight;
}

function buildSupportTicketSubmittedMessage(ticket, result) {
  const submittedTicket = result.ticket || ticket || {};
  const lines = [
    "客服工单已模拟提交成功。请基于以下提交信息，给用户一段简短的情绪支持。",
    result.ticket_id ? `工单编号：${result.ticket_id}` : "",
    submittedTicket.issue_type ? `问题类型：${submittedTicket.issue_type}` : "",
    submittedTicket.issue_summary ? `问题描述：${submittedTicket.issue_summary}` : "",
    submittedTicket.product_model ? `产品型号：${submittedTicket.product_model}` : "",
    submittedTicket.urgency ? `紧急程度：${submittedTicket.urgency}` : "",
    "回复要求：不要重复工单字段，不要继续排查；根据用户的主要售后情绪做 1-3 句贴合场景的承接，并告诉用户人工客服会在 24 小时内联系你解决问题。",
  ];
  return lines.filter(Boolean).join("\n");
}

function addIbclcConsultCard(card) {
  const body = addStructuredArtifact("ibclc", "");
  const node = document.createElement("article");
  node.className = "ibclc-card";
  const consultId = `ibclc_${crypto.randomUUID()}`;
  node.dataset.consultId = consultId;

  const consultant = card.consultant || {};
  const chat = card.chat || {};

  const heading = document.createElement("div");
  heading.className = "ibclc-card-heading";
  const title = document.createElement("h2");
  title.textContent = "IBCLC咨询";
  heading.appendChild(title);
  node.appendChild(heading);

  const consultantBlock = document.createElement("section");
  consultantBlock.className = "ibclc-consultant";
  const avatar = document.createElement("div");
  avatar.className = "ibclc-avatar";
  avatar.textContent = initialsForName(consultant.name || "IBCLC");
  consultantBlock.appendChild(avatar);

  const details = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = consultant.name || "IBCLC 顾问";
  details.appendChild(name);
  if (consultant.bio) {
    const bio = document.createElement("p");
    bio.textContent = consultant.bio;
    details.appendChild(bio);
  }
  consultantBlock.appendChild(details);
  node.appendChild(consultantBlock);

  const button = document.createElement("a");
  button.className = "ibclc-chat-button";
  button.href = ibclcChatUrl(chat.url || "/ibclc-chat.html", consultId);
  button.target = "_blank";
  button.rel = "noopener";
  button.textContent = "在线咨询";
  node.appendChild(button);
  applyIbclcConsultCompletionToCard(node, readStoredIbclcConsultCompletion());

  body.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function ibclcChatUrl(url, consultId) {
  const threadId = conversationId || localStorage.getItem("momcozy_conversation_id") || "";
  try {
    const nextUrl = new URL(url, window.location.origin);
    if (threadId) nextUrl.searchParams.set("thread_id", threadId);
    if (consultId) nextUrl.searchParams.set("consult_id", consultId);
    return nextUrl.origin === window.location.origin
      ? `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`
      : nextUrl.toString();
  } catch (_) {
    return url;
  }
}

function readStoredIbclcConsultCompletion() {
  return parseJson(localStorage.getItem(IBCLC_CONSULT_COMPLETED_KEY));
}

function handleIbclcConsultCompleted(payload) {
  if (!payload || payload.type !== "momcozy.ibclc_consult_completed") return;
  const eventThreadId = payload.conversation_id || payload.thread_id || payload.threadId || "";
  if (eventThreadId && conversationId && eventThreadId !== conversationId) return;
  if (eventThreadId && !conversationId) {
    conversationId = eventThreadId;
    localStorage.setItem("momcozy_conversation_id", conversationId);
  }
  updateMeta({
    conversation_id: eventThreadId || conversationId,
    loaded_skill_ids: payload.session_state?.loaded_skill_ids || [],
  });
  for (const card of document.querySelectorAll(".ibclc-card")) {
    applyIbclcConsultCompletionToCard(card, payload);
  }
}

function applyIbclcConsultCompletionToCard(card, payload) {
  const eventThreadId = payload?.conversation_id || payload?.thread_id || payload?.threadId || "";
  if (eventThreadId && conversationId && eventThreadId !== conversationId) return;
  const eventConsultId = payload?.consult_id || payload?.consultId || "";
  if (!eventConsultId || eventConsultId !== card?.dataset?.consultId) return;
  const button = card?.querySelector(".ibclc-chat-button");
  if (!button || payload?.type !== "momcozy.ibclc_consult_completed") return;
  card.classList.add("consult-completed");
  button.textContent = "咨询结束";
  button.classList.add("is-completed");
  button.setAttribute("aria-disabled", "true");
  button.removeAttribute("href");
  button.removeAttribute("target");
  button.removeAttribute("rel");
}

function initialsForName(name) {
  const text = String(name || "").trim();
  if (!text) return "IB";
  const asciiParts = text.match(/[A-Za-z]+/g);
  if (asciiParts?.length) {
    return asciiParts.slice(0, 2).map((part) => part[0].toUpperCase()).join("");
  }
  return text.slice(0, 2);
}

function addCard(card) {
  const body = addStructuredArtifact("card", "生成卡片");
  const node = document.createElement("article");
  node.className = `agent-card ${card.card_type ? `agent-card-${card.card_type}` : ""}`.trim();
  node.dataset.cardType = card.card_type || "";
  node.dataset.schemaVersion = card.schema_version || "";

  const cardJson = card.card_json || {};
  if (card.card_type === "birth_plan_card" && card.schema_version === "1.0") {
    renderBirthPlanCardV1(node, cardJson);
  } else if (card.card_type === "birth_journey_plan_card" && card.schema_version === "1.0") {
    renderBirthJourneyPlanCardV1(node, cardJson);
  } else if (card.card_type === "hospital_bag_card" && card.schema_version === "1.0") {
    renderHospitalBagCardV1(node, cardJson);
  } else if (card.card_type === "mom_baby_status_card" && card.schema_version === "1.0") {
    renderMomBabyStatusCardV1(node, cardJson);
  } else {
    renderUnsupportedCard(node, card);
  }

  body.appendChild(node);
  attachCardDownload(body, node, cardDownloadFilename(card));
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function attachCardDownload(body, cardNode, filename) {
  const artifact = body?.closest(".structured-artifact");
  const header = artifact?.querySelector(".artifact-header");
  if (!header || !cardNode) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "artifact-download";
  button.title = "下载卡片 PNG";
  button.setAttribute("aria-label", "下载卡片 PNG");
  button.innerHTML = DOWNLOAD_ICON_SVG;
  button.addEventListener("click", async () => {
    await downloadCardAsPng(button, cardNode, filename);
  });
  header.appendChild(button);
}

async function downloadCardAsPng(button, cardNode, filename) {
  if (!window.htmlToImage?.toPng) {
    addMessage("error", "图片导出组件未加载，请检查网络后重试。");
    return;
  }

  button.disabled = true;
  button.classList.add("is-loading");
  try {
    await document.fonts?.ready;
    const dataUrl = await window.htmlToImage.toPng(cardNode, {
      backgroundColor: "#ffffff",
      cacheBust: true,
      pixelRatio: Math.min(Math.max(window.devicePixelRatio || 2, 2), 3),
    });
    const blob = await dataUrlToBlob(dataUrl);
    const shared = await shareImageOnMobile(blob, filename);
    if (!shared) {
      triggerImageDownload(dataUrl, filename);
    }
  } catch (error) {
    addMessage("error", error?.message || "卡片下载失败，请稍后重试。");
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
  }
}

function cardDownloadFilename(card) {
  const type = card?.card_type || "card";
  const date = new Date().toISOString().slice(0, 10);
  return `comate-${type}-${date}.png`.replace(/[^a-z0-9._-]+/gi, "-");
}

async function dataUrlToBlob(dataUrl) {
  const response = await fetch(dataUrl);
  return response.blob();
}

async function shareImageOnMobile(blob, filename) {
  const canShareFiles = navigator.share && navigator.canShare && typeof File !== "undefined";
  const mobileViewport = window.matchMedia?.("(max-width: 700px)")?.matches;
  if (!canShareFiles || !mobileViewport) return false;

  const file = new File([blob], filename, { type: "image/png" });
  if (!navigator.canShare({ files: [file] })) return false;

  try {
    await navigator.share({
      files: [file],
      title: "CoMate card",
    });
    return true;
  } catch (error) {
    return error?.name === "AbortError";
  }
}

function triggerImageDownload(dataUrl, filename) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function renderBirthPlanCardV1(node, cardJson) {
  const card = normalizeBirthPlanCard(cardJson);
  addCardHeader(node, card.title || "Birth Plan Card", "");
  addBirthPlanPreferenceGroups(node, [
    ["沟通方式", card.communication],
    ["生产过程", card.labor_preferences],
    ["需要先沟通的操作", card.intervention_preferences],
    ["疼痛缓解", card.pain_relief],
    ["宝宝出生后", card.baby_after_birth],
    ["计划变化时", card.if_plans_change],
    ["来不及慢慢沟通时", card.emergency_authorization],
    ["提前问医院", card.questions_for_hospital],
  ]);
  addListSection(node, "医疗或安全信息", card.medical_notes);
  addDisclaimer(node, card.disclaimer);
}

function renderHospitalBagCardV1(node, cardJson) {
  addCardHeader(node, hospitalBagTitle(cardJson.title), hospitalBagSubtitle(cardJson) || cardJson.subtitle || "");
  addPackingGroups(node, compactPackingGroups(cardJson.packing_groups));
  addListSection(node, "Timeline", limitList(cardJson.timeline, 2));
}

function renderBirthJourneyPlanCardV1(node, cardJson) {
  addCardHeader(node, cardJson.title || "孕期计划", cardJson.subtitle || "");
  addBirthJourneyOwnerStrip(node, cardJson.owner || {});
  const phases = normalizeBirthJourneyPhases(cardJson.phases);
  if (phases.length) {
    const timeline = document.createElement("section");
    timeline.className = "birth-journey-timeline";
    for (const [index, phase] of phases.entries()) {
      const item = document.createElement("article");
      item.className = `birth-journey-phase ${phase.status === "current" ? "is-current" : ""}`.trim();
      const marker = document.createElement("span");
      marker.className = "birth-journey-phase-marker";
      marker.textContent = String(index + 1);
      const body = document.createElement("div");
      body.className = "birth-journey-phase-body";
      const heading = document.createElement("div");
      heading.className = "birth-journey-phase-heading";
      const titleWrap = document.createElement("div");
      const dateRange = document.createElement("p");
      dateRange.textContent = phase.date_range;
      const title = document.createElement("h3");
      title.textContent = phase.title;
      titleWrap.append(dateRange, title);
      const status = document.createElement("span");
      status.textContent = phase.status === "current" ? "当前阶段" : "下一阶段";
      heading.append(titleWrap, status);
      body.appendChild(heading);
      if (phase.goal) {
        const goal = document.createElement("p");
        goal.className = "birth-journey-goal";
        goal.textContent = phase.goal;
        body.appendChild(goal);
      }
      const grid = document.createElement("div");
      grid.className = "birth-journey-section-grid";
      appendBirthJourneyMiniSection(grid, "注意", phase.watchouts);
      appendBirthJourneyMiniSection(grid, "准备", phase.actions);
      appendBirthJourneyMiniSection(grid, "CoMate", phase.comate_help);
      if (grid.childElementCount) body.appendChild(grid);
      item.append(marker, body);
      timeline.appendChild(item);
    }
    node.appendChild(timeline);
  }
}

function addBirthJourneyOwnerStrip(node, owner) {
  if (!owner || typeof owner !== "object") return;
  const entries = [
    ["孕期", owner.current_week || owner.due_date_or_week],
    ["预产期预计", owner.estimated_due_date],
    ["方式", owner.birth_path],
    ["支持", owner.support_person],
  ].filter(([, value]) => hasDisplayValue(value) && !isConfirmPlaceholder(value));
  if (!entries.length) return;
  const list = document.createElement("dl");
  list.className = "birth-journey-owner-strip";
  for (const [label, value] of entries.slice(0, 4)) {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const desc = document.createElement("dd");
    desc.textContent = formatPlainValue(value);
    item.append(term, desc);
    list.appendChild(item);
  }
  node.appendChild(list);
}

function normalizeBirthJourneyPhases(phases) {
  if (!Array.isArray(phases)) return [];
  return phases
    .filter((phase) => phase && typeof phase === "object")
    .map((phase, index) => ({
      id: phase.id || `phase-${index}`,
      title: phase.title || `阶段 ${index + 1}`,
      date_range: phase.date_range || "",
      status: phase.status === "current" ? "current" : "upcoming",
      goal: phase.goal || "",
      watchouts: limitList(phase.watchouts, 4),
      actions: limitList(phase.actions, 4),
      comate_help: limitList(phase.comate_help, 3),
    }));
}

function appendBirthJourneyMiniSection(parent, titleText, values) {
  const items = limitList(values, 4);
  if (!items.length) return;
  const section = document.createElement("section");
  const title = document.createElement("h4");
  title.textContent = titleText;
  const list = document.createElement("ul");
  for (const value of items) {
    const item = document.createElement("li");
    item.textContent = formatPlainValue(value);
    list.appendChild(item);
  }
  section.append(title, list);
  parent.appendChild(section);
}

function renderMomBabyStatusCardV1(node, cardJson) {
  addCardHeader(node, cardJson.title || "母婴状态", cardJson.subtitle || "");
  const tabs = normalizeMomBabyStatusTabs(cardJson.tabs);
  if (!tabs.length) {
    const pre = document.createElement("pre");
    pre.className = "agent-card-json";
    pre.textContent = JSON.stringify(cardJson || {}, null, 2);
    node.appendChild(pre);
    return;
  }

  const baseId = `mom-baby-status-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const tabList = document.createElement("div");
  tabList.className = "mom-baby-tablist";
  tabList.setAttribute("role", "tablist");
  tabList.setAttribute("aria-label", "母婴数字分身");

  const panels = document.createElement("div");
  panels.className = "mom-baby-tabpanels";

  tabs.forEach((tab, index) => {
    const tabId = `${baseId}-tab-${tab.id}`;
    const panelId = `${baseId}-panel-${tab.id}`;
    const button = document.createElement("button");
    button.type = "button";
    button.id = tabId;
    button.className = "mom-baby-tab";
    button.dataset.tabId = tab.id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", panelId);
    button.setAttribute("aria-selected", index === 0 ? "true" : "false");
    button.textContent = tab.title;
    button.addEventListener("click", () => activateMomBabyStatusTab(node, tab.id));
    tabList.appendChild(button);

    const panel = document.createElement("section");
    panel.id = panelId;
    panel.className = "mom-baby-tabpanel";
    panel.dataset.tabId = tab.id;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tabId);
    panel.hidden = index !== 0;
    if (tab.summary) {
      const summary = document.createElement("p");
      summary.className = "mom-baby-summary";
      summary.textContent = tab.summary;
      panel.appendChild(summary);
    }
    for (const section of tab.sections) {
      addMomBabyStatusSection(panel, section);
    }
    panels.appendChild(panel);
  });

  node.append(tabList, panels);
}

function normalizeMomBabyStatusTabs(tabs) {
  if (!Array.isArray(tabs)) return [];
  return tabs
    .filter((tab) => tab && typeof tab === "object")
    .map((tab, index) => ({
      id: slugifyClassName(tab.id || tab.title || `tab-${index}`) || `tab-${index}`,
      title: String(tab.title || `Tab ${index + 1}`),
      summary: String(tab.summary || "").trim(),
      sections: Array.isArray(tab.sections) ? tab.sections.filter((section) => section && typeof section === "object") : [],
    }))
    .slice(0, 2);
}

function activateMomBabyStatusTab(cardNode, tabId) {
  cardNode.querySelectorAll(".mom-baby-tab").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.tabId === tabId));
  });
  cardNode.querySelectorAll(".mom-baby-tabpanel").forEach((panel) => {
    panel.hidden = panel.dataset.tabId !== tabId;
  });
}

function addMomBabyStatusSection(parent, section) {
  const sectionNode = document.createElement("section");
  sectionNode.className = `mom-baby-status-section mom-baby-status-section-${slugifyClassName(section.tone || "default")}`;
  const title = document.createElement("h3");
  title.textContent = section.title || "状态";
  sectionNode.appendChild(title);

  const metrics = Array.isArray(section.metrics) ? section.metrics.filter((metric) => metric && typeof metric === "object") : [];
  if (metrics.length) {
    const list = document.createElement("dl");
    list.className = "mom-baby-metrics";
    for (const metric of metrics.slice(0, 4)) {
      const item = document.createElement("div");
      item.className = "mom-baby-metric";
      const label = document.createElement("dt");
      label.textContent = metric.label || "";
      const value = document.createElement("dd");
      value.textContent = metric.value || "—";
      item.append(label, value);
      if (metric.detail) {
        const detail = document.createElement("small");
        detail.textContent = metric.detail;
        item.appendChild(detail);
      }
      list.appendChild(item);
    }
    sectionNode.appendChild(list);
  }

  const items = normalizeList(section.items);
  if (items.length) {
    const list = document.createElement("ul");
    list.className = "agent-card-list mom-baby-status-list";
    for (const item of items.slice(0, 4)) {
      const li = document.createElement("li");
      appendValue(li, item);
      list.appendChild(li);
    }
    sectionNode.appendChild(list);
  }

  parent.appendChild(sectionNode);
}

function hospitalBagTitle(value) {
  const title = String(value || "").trim();
  if (!title || title === "Hospital Bag Card" || (title.includes("待产包") && title.includes("卡片"))) return "待产包";
  return title;
}

function hospitalBagSubtitle(cardJson) {
  return "待产包是什么 | 什么时间准备好";
}

function hospitalBagProfile(cardJson) {
  const owner = cardJson.owner || {};
  return {
    孕期: hospitalBagMetaValue(owner.due_date_or_week),
    生产方式: hospitalBagMetaValue(owner.birth_path),
    喂养意向: hospitalBagMetaValue(owner.feeding_intention),
  };
}

function hospitalBagMetaValue(value) {
  const text = String(value || "").trim();
  const labels = {
    breastfeeding: "母乳喂养",
    "母乳": "母乳喂养",
    formula: "配方喂养",
    "配方": "配方喂养",
    formula_feeding: "配方喂养",
    mixed: "混合喂养",
    "混合": "混合喂养",
    vaginal: "顺产",
    planned_c_section: "剖宫产",
    c_section: "剖宫产",
    "计划剖宫产": "剖宫产",
    "剖宫产": "剖宫产",
    "剖腹产": "剖宫产",
    "刨腹产": "剖宫产",
  };
  return labels[text.toLowerCase()] || value;
}

function normalizeBirthPlanCard(cardJson) {
  const owner = cardJson.owner || {};
  const overview = cardJson.overview || {};
  const birthPreferences = cardJson.birth_preferences || {};
  return {
    title: cardJson.title || "Birth Plan Card",
    subtitle: cardJson.subtitle || "Labor room communication priority card",
    overview: {
      due_date_or_week: overview.due_date_or_week || owner.due_date_or_week,
      birth_path: overview.birth_path || birthPreferences.birth_path,
      birth_setting: overview.birth_setting || owner.birth_setting,
      support_people: overview.support_people || owner.support_people,
    },
    top_priorities: compactBirthPlanList(cardJson.top_priorities || cardJson.if_plans_change?.what_matters_most, 3),
    communication: compactBirthPlanList(cardJson.communication || cardJson.communication_preferences, 3),
    labor_preferences: compactBirthPlanList(cardJson.labor_preferences, 3),
    intervention_preferences: compactBirthPlanList(cardJson.intervention_preferences, 3),
    pain_relief: compactBirthPlanList(cardJson.pain_relief || cardJson.pain_relief_preferences, 3),
    baby_after_birth: compactBirthPlanList(cardJson.baby_after_birth || cardJson.baby_after_birth_preferences, 3),
    if_plans_change: compactBirthPlanList(cardJson.if_plans_change, 3),
    emergency_authorization: compactBirthPlanList(cardJson.emergency_authorization, 3),
    questions_for_hospital: compactBirthPlanList(cardJson.questions_for_hospital, 3),
    medical_notes: compactBirthPlanList(cardJson.medical_notes, 3),
    personalized_notes: compactBirthPlanList(cardJson.personalized_notes, 3),
    disclaimer:
      cardJson.disclaimer ||
      "This card is for communication only. Please follow your clinician and hospital guidance, especially if plans change for safety reasons.",
  };
}

function compactBirthPlanList(values, maxItems) {
  return normalizeList(flattenDisplayValues(values))
    .filter((value) => !isConfirmPlaceholder(value))
    .slice(0, maxItems);
}

function flattenDisplayValues(value) {
  if (Array.isArray(value)) {
    return value.flatMap(flattenDisplayValues);
  }
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(flattenDisplayValues);
  }
  return hasDisplayValue(value) ? [formatPlainValue(value)] : [];
}

function addBirthPlanPreferenceGroups(node, groups) {
  const visibleGroups = groups
    .map(([title, values]) => [title, compactBirthPlanList(values, 3)])
    .filter(([, values]) => values.length);
  if (!visibleGroups.length) return;

  const section = createCardSection("沟通卡片内容");
  section.classList.add("birth-plan-preferences");
  const grid = document.createElement("div");
  grid.className = "birth-plan-group-grid";
  for (const [title, values] of visibleGroups) {
    const group = document.createElement("div");
    group.className = "birth-plan-group";
    const heading = document.createElement("h4");
    heading.textContent = title;
    group.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "agent-card-list";
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      list.appendChild(item);
    }
    group.appendChild(list);
    grid.appendChild(group);
  }
  section.appendChild(grid);
  node.appendChild(section);
}

function compactPackingGroups(groups) {
  if (!Array.isArray(groups)) return [];
  const merged = new Map();
  groups
    .filter((group) => group && typeof group === "object")
    .forEach((group, index) => {
      const items = compactPackingItems(group.items);
      if (!items.length) return;
      const scene = hospitalBagSceneGroup(group, index);
      const existing = merged.get(scene.id);
      if (existing) {
        existing.items.push(...items);
        existing._order = Math.min(existing._order, scene.order);
        return;
      }
      merged.set(scene.id, {
        ...group,
        group_id: scene.id,
        title: scene.title,
        items,
        _order: scene.order,
      });
    });
  return Array.from(merged.values())
    .sort((a, b) => a._order - b._order)
    .map(({ _order, ...group }) => group);
}

function hospitalBagSceneGroup(group, fallbackOrder) {
  const text = `${group?.group_id || ""} ${group?.title || ""}`.toLowerCase();
  if (/(documents|certificate|证件|资料|文件)/.test(text)) return { id: "documents", title: "证件文件包", order: 0 };
  if (/(baby|宝宝|新生儿)/.test(text)) return { id: "baby_discharge_bag", title: "宝宝出院包", order: 2 };
  if (/(support|partner|companion|陪产|支持人)/.test(text)) return { id: "support_person_bag", title: "陪产人包", order: 3 };
  if (/(car|travel|traffic|transport|车上|交通|停车|路线)/.test(text)) return { id: "car_backup_bag", title: "车上备用包", order: 4 };
  if (/(lactation|breastfeeding|feeding|postpartum|哺乳|喂养|产后回家|产后护理)/.test(text)) {
    return { id: "postpartum_home_first_week", title: "产后回家第一周用品", order: 5 };
  }
  if (/(mom|mother|communication|food|妈妈|衣物|清洁|护理|通讯|饮食|住院)/.test(text)) {
    return { id: "mom_hospital_bag", title: "妈妈住院包", order: 1 };
  }
  return { id: group?.group_id || `custom_${fallbackOrder}`, title: group?.title || formatLabel(group?.group_id || "Group"), order: 20 + fallbackOrder };
}

function compactPackingItems(items) {
  if (!Array.isArray(items)) return [];
  if (items.length <= 4) return items;

  const pumpIndex = items.findIndex(isBreastPumpItem);
  if (pumpIndex === -1 || pumpIndex < 4) return items.slice(0, 4);

  const visibleItems = items.slice(0, 4);
  visibleItems[3] = items[pumpIndex];
  return visibleItems;
}

function isBreastPumpItem(item) {
  const text = JSON.stringify(item || {}).toLowerCase();
  return text.includes("吸奶") || text.includes("breast pump") || text.includes("pump");
}

function renderUnsupportedCard(node, card) {
  addCardHeader(node, card.card_type || "Unsupported card", card.schema_version ? `Schema ${card.schema_version}` : "");
  const pre = document.createElement("pre");
  pre.className = "agent-card-json";
  pre.textContent = JSON.stringify(card.card_json || {}, null, 2);
  node.appendChild(pre);
}

function addCardHeader(node, titleText, subtitleText) {
  const header = document.createElement("header");
  header.className = "agent-card-header";

  const text = document.createElement("div");
  text.className = "agent-card-header-text";

  const title = document.createElement("h2");
  title.textContent = titleText;
  text.appendChild(title);
  if (subtitleText) {
    const subtitle = document.createElement("p");
    subtitle.textContent = subtitleText;
    text.appendChild(subtitle);
  }

  const logo = document.createElement("img");
  logo.className = "agent-card-logo";
  logo.src = MOMCOZY_LOGO_SRC;
  logo.alt = "Momcozy";

  header.append(text, logo);
  node.appendChild(header);
}

function addKeyValueSection(node, title, data, options = {}) {
  if (!data || typeof data !== "object") return;
  const entries = Object.entries(data).filter(([key, value]) => {
    if (options.skipKeys?.includes(key)) return false;
    return hasDisplayValue(value);
  });
  if (!entries.length) return;

  const section = createCardSection(title);
  const grid = document.createElement("dl");
  grid.className = "agent-card-kv";
  for (const [key, value] of entries) {
    const dt = document.createElement("dt");
    dt.textContent = formatLabel(key);
    const dd = document.createElement("dd");
    appendValue(dd, value);
    grid.append(dt, dd);
  }
  section.appendChild(grid);
  node.appendChild(section);
}

function addListSection(node, title, values, options = {}) {
  const items = normalizeList(values);
  if (!items.length) return;
  const section = createCardSection(title, options.muted ? "muted" : "");
  const list = document.createElement("ul");
  list.className = "agent-card-list";
  for (const item of items) {
    const li = document.createElement("li");
    appendValue(li, item);
    list.appendChild(li);
  }
  section.appendChild(list);
  node.appendChild(section);
}

function addPackingGroups(node, groups) {
  if (!Array.isArray(groups) || !groups.length) return;
  const section = createCardSection("物品清单");
  for (const group of groups) {
    if (!group || typeof group !== "object") continue;
    const groupNode = document.createElement("div");
    groupNode.className = "packing-group";
    const title = document.createElement("h4");
    title.textContent = group.title || formatLabel(group.group_id || "Group");
    groupNode.appendChild(title);

    const list = document.createElement("ul");
    list.className = "agent-card-list";
    for (const item of group.items || []) {
      const li = document.createElement("li");
      li.className = "packing-item";
      const main = document.createElement("div");
      main.className = "packing-item-main";
      const label = document.createElement("span");
      label.textContent = item?.label || String(item || "");
      main.appendChild(label);
      const personalization = personalizationLabel(item?.personalized_by, item, group);
      if (personalization) {
        const source = document.createElement("small");
        source.className = "packing-item-source";
        source.textContent = personalization;
        main.appendChild(source);
      }
      li.appendChild(main);
      if (item?.priority) {
        const badge = document.createElement("span");
        badge.className = `priority priority-${String(item.priority).replaceAll("_", "-")}`;
        badge.textContent = priorityLabel(item.priority);
        li.appendChild(badge);
      }
      if (item?.note) {
        const note = document.createElement("small");
        note.textContent = item.note;
        li.appendChild(note);
      }
      list.appendChild(li);
    }
    groupNode.appendChild(list);
    section.appendChild(groupNode);
  }
  node.appendChild(section);
}

function personalizationLabel(sources, item, group) {
  if (shouldSuppressHospitalBagReason(item, group)) return "";
  if (!Array.isArray(sources) || !sources.length) return "";
  const entries = sources
    .map((source) => {
      if (!source || typeof source !== "object") return "";
      return {
        clause: personalizationClause(source),
        effect: String(source.effect || ""),
      };
    })
    .filter((entry) => entry?.clause);
  const clauses = uniquePersonalizationClauses(entries.map((entry) => entry.clause), 4);
  if (!clauses.length) return "";
  const userClauses = clauses.filter((clause) => clause.subject === "user").map((clause) => clause.text);
  const externalClauses = clauses.filter((clause) => clause.subject === "external").map((clause) => clause.text);
  const reasonParts = [
    userClauses.length ? `你${userClauses.join("加上")}` : "",
    externalClauses.join("，"),
  ].filter(Boolean);
  const reasonText = reasonParts.join("，且");
  if (!reasonText) return "";
  const effects = entries.map((entry) => entry.effect);
  if (String(item?.priority || "") === "confirm_first") return `${reasonText}，建议准备`;
  if (effects.some((effect) => effect.includes("数量调整"))) return `${reasonText}，数量已按这个情况调整`;
  if (effects.some((effect) => effect.includes("降级") || effect.includes("暂缓"))) return `${reasonText}，可以按需准备`;
  return `${reasonText}，${String(item?.priority || "") === "must" ? "必须准备" : "建议准备"}`;
}

function shouldSuppressHospitalBagReason(item, group) {
  const groupId = String(group?.group_id || "");
  const label = String(item?.label || "");
  return HOSPITAL_BAG_REASON_SUPPRESSED_GROUP_IDS.has(groupId) || HOSPITAL_BAG_REASON_SUPPRESSED_ITEM_LABELS.has(label);
}

function uniquePersonalizationClauses(clauses, maxItems) {
  const seen = new Set();
  const unique = [];
  for (const clause of clauses) {
    if (!clause?.text) continue;
    const key = `${clause.subject}:${clause.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(clause);
    if (unique.length >= maxItems) break;
  }
  return unique;
}

function personalizationClause(source) {
  const field = String(source.field || "");
  const fieldLabel = String(source.field_label || source.field || "");
  const condition = String(source.condition || "").trim();
  if (!condition || isConfirmPlaceholder(condition)) return null;
  if (field === "first_birth" || fieldLabel === "是否第一胎") {
    if (condition === "是") return { text: "是第一胎", subject: "user" };
    if (condition === "否") return { text: "不是第一胎", subject: "user" };
  }
  if (field === "birth_path" || fieldLabel === "分娩方式") {
    if (condition.includes("剖")) return { text: "是剖宫产", subject: "user" };
    if (condition.includes("顺")) return { text: "计划顺产", subject: "user" };
    return { text: `分娩方式是${condition}`, subject: "user" };
  }
  if (field === "feeding_intention" || fieldLabel === "喂养意向") {
    if (condition.includes("母乳")) return { text: "希望母乳喂养", subject: "user" };
    if (condition.includes("混合")) return { text: "计划混合喂养", subject: "user" };
    if (condition.includes("配方")) return { text: "计划配方喂养", subject: "user" };
    if (condition.includes("泵")) return { text: "计划泵奶喂养", subject: "user" };
    return { text: "还没确定喂养方式", subject: "user" };
  }
  if (field === "fetus_count" || fieldLabel === "胎数") {
    if (condition.includes("双胎")) return { text: "是双胎", subject: "user" };
    if (condition.includes("三胎")) return { text: "是三胎及以上", subject: "user" };
    if (condition.includes("单胎")) return { text: "是单胎", subject: "user" };
  }
  if (field === "return_to_work_timing" || fieldLabel === "返工时间") {
    return { text: returnToWorkClause(condition), subject: "user" };
  }
  if (field === "support_person" || fieldLabel === "支持情况") {
    if (condition.includes("支持少")) return { text: "产后支持较少", subject: "user" };
    return { text: `产后支持情况是${condition}`, subject: "user" };
  }
  if (field === "top_worries" || fieldLabel === "焦虑点") {
    return { text: worryClause(condition), subject: "user" };
  }
  if (field === "pregnancy_history_or_notes" || fieldLabel === "医生提示") {
    if (condition === "已填写医生提示") return { text: "医生有特别提示", subject: "external" };
    return { text: `医生提示${condition}`, subject: "external" };
  }
  if (field === "due_date_or_week" || fieldLabel === "孕周/预产期") {
    return { text: condition.endsWith("版") ? `处于${condition}` : `当前是${condition}`, subject: "user" };
  }
  if (!fieldLabel) return null;
  return { text: `${fieldLabel}是${condition}`, subject: "user" };
}

function returnToWorkClause(condition) {
  if (condition.includes("暂不") || condition.includes("不返工")) return "暂不返工";
  if (condition.startsWith("产后")) return `${condition}返工`;
  return `产后${condition}返工`;
}

function worryClause(condition) {
  if (condition.startsWith("怕")) return `担心${condition.slice(1)}`;
  if (condition.startsWith("担心")) return condition;
  return `担心${condition}`;
}

function addDisclaimer(node, text) {
  if (!text) return;
  const disclaimer = document.createElement("p");
  disclaimer.className = "agent-card-disclaimer";
  disclaimer.textContent = text;
  node.appendChild(disclaimer);
}

function createCardSection(title, extraClass = "") {
  const section = document.createElement("section");
  section.className = `agent-card-section ${extraClass}`.trim();
  section.dataset.section = slugifyClassName(title);
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.appendChild(heading);
  return section;
}

function priorityLabel(priority) {
  const labels = {
    must: "Essential",
    recommended: "Helpful",
    nice_to_have: "Optional",
    confirm_first: "和医院确认",
    先确认: "和医院确认",
  };
  return labels[String(priority || "")] || formatLabel(priority);
}

function slugifyClassName(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function createFormField(field) {
  if (isMultiSelectField(field)) {
    return createCheckboxGroup(field);
  }

  const hasDefaultValue = hasFormDefaultValue(field.default_value);
  const wrapper = document.createElement("label");
  wrapper.className = "agent-form-field";

  wrapper.appendChild(createFieldLabel("span", field));

  let control;
  if (field.type === "select") {
    control = document.createElement("select");
    if (!hasDefaultValue) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = field.placeholder || "请选择";
      placeholder.disabled = true;
      placeholder.selected = true;
      control.appendChild(placeholder);
    }
    for (const option of field.options || []) {
      const optionNode = document.createElement("option");
      optionNode.value = option;
      optionNode.textContent = option;
      control.appendChild(optionNode);
    }
  } else if (field.type === "textarea") {
    control = document.createElement("textarea");
    control.rows = 3;
  } else {
    control = document.createElement("input");
    control.type = field.type === "date" ? "date" : "text";
  }

  control.name = field.id;
  control.required = Boolean(field.required);
  if (field.placeholder) control.placeholder = field.placeholder;
  if (hasDefaultValue) control.value = field.default_value;
  wrapper.appendChild(control);

  if (field.help_text) {
    const help = document.createElement("small");
    help.textContent = field.help_text;
    wrapper.appendChild(help);
  }

  return wrapper;
}

function normalizeFormSpec(formSpec) {
  const removedHospitalBagFieldIds = new Set(["hospital_rules_or_notes", "existing_checklist_or_photo_note"]);
  const hospitalBagFieldIds = new Set([
    "due_date_or_week",
    "first_birth",
    "fetus_count",
    "pregnancy_history_or_notes",
    "birth_path",
    "feeding_intention",
    "return_to_work_timing",
    "support_person",
    "top_worries",
  ]);
  const hospitalBagDetectorFieldIds = new Set(["fetus_count", "return_to_work_timing", "top_worries"]);
  const hospitalBagDialoguePrefillFieldIds = new Set([
    "due_date_or_week",
    "return_to_work_timing",
    "top_worries",
  ]);
  const exclusiveBirthPlanMultiSelectOptions = new Set([
    "我还没想好，请帮我整理成温和版本",
    "不需要持续解释，必要时再说就好",
    "无特别偏好，听医生安排",
    "听医生判断即可",
    "灌肠/剃毛：希望按医院常规即可",
    "暂未决定，听医生建议",
    "未确定",
    "还没确定",
    "还没想好",
  ]);
  const isHospitalBag = isHospitalBagFormSpec(formSpec, hospitalBagFieldIds, hospitalBagDetectorFieldIds);
  const isBirthPlan = formSpec?.id === "birth_plan_card_intake";
  if (!isHospitalBag && !isBirthPlan) return formSpec;
  return {
    ...formSpec,
    id: isHospitalBag ? "hospital_bag_intake" : formSpec.id,
    description: "",
    default_values:
      isHospitalBag
        ? sanitizeHospitalBagDefaultValues(formSpec.default_values, hospitalBagDialoguePrefillFieldIds)
        : formSpec.default_values,
    fields: (formSpec.fields || [])
      .filter((field) => !isHospitalBag || !removedHospitalBagFieldIds.has(field?.id))
      .map((field) => {
        const { help_text, ...rest } = field || {};
        if (isHospitalBag) {
          return sanitizeHospitalBagField(rest, hospitalBagDialoguePrefillFieldIds);
        }
        if (isBirthPlan && rest.type === "multi_select" && Array.isArray(rest.options)) {
          return {
            ...rest,
            options: rest.options.filter((option) => !exclusiveBirthPlanMultiSelectOptions.has(String(option))),
          };
        }
        return rest;
      }),
  };
}

function isHospitalBagFormSpec(formSpec, hospitalBagFieldIds, detectorFieldIds) {
  if (formSpec?.id === "hospital_bag_intake") return true;
  const fieldIds = new Set((formSpec?.fields || []).map((field) => String(field?.id || "")));
  const matchedHospitalFields = Array.from(fieldIds).filter((id) => hospitalBagFieldIds.has(id)).length;
  return matchedHospitalFields >= 2 && Array.from(detectorFieldIds).some((id) => fieldIds.has(id));
}

function sanitizeHospitalBagField(field, allowedDefaultFieldIds) {
  const nextField = { ...field };
  if (nextField.id === "pregnancy_history_or_notes" && Array.isArray(nextField.options)) {
    nextField.options = nextField.options.filter((option) => String(option) !== "计划剖宫产");
  }
  if (!allowedDefaultFieldIds.has(String(nextField.id || "")) || !hasFormDefaultValue(nextField.default_value)) {
    delete nextField.default_value;
  }
  return nextField;
}

function sanitizeHospitalBagDefaultValues(defaultValues, allowedDefaultFieldIds) {
  if (!defaultValues || typeof defaultValues !== "object" || Array.isArray(defaultValues)) return {};
  return Object.fromEntries(
    Object.entries(defaultValues).filter(
      ([key, value]) => allowedDefaultFieldIds.has(String(key)) && hasFormDefaultValue(value),
    ),
  );
}

function groupFormFields(fields) {
  const groups = [];
  const groupByTitle = new Map();

  for (const field of fields) {
    const labelParts = splitFormFieldLabel(field.label);
    const title = labelParts.groupTitle;
    const key = title || "__ungrouped";
    let group = groupByTitle.get(key);
    if (!group) {
      group = { title, fields: [] };
      groupByTitle.set(key, group);
      groups.push(group);
    }
    group.fields.push({ ...field, label: labelParts.fieldLabel });
  }

  return groups;
}

function splitFormFieldLabel(label) {
  const text = String(label || "");
  const separatorIndex = text.indexOf("｜");
  if (separatorIndex <= 0) return { groupTitle: "", fieldLabel: text };

  const groupTitle = text.slice(0, separatorIndex).trim();
  const fieldLabel = text.slice(separatorIndex + 1).trim();
  if (!groupTitle || !fieldLabel) return { groupTitle: "", fieldLabel: text };
  return { groupTitle, fieldLabel };
}

function createFormSection(group, formId = "", groupIndex = 0) {
  const section = document.createElement("section");
  const toneClass =
    formId === "hospital_bag_intake"
      ? hospitalBagSectionToneClass(group.title, groupIndex)
      : formId === "birth_plan_card_intake"
        ? birthPlanSectionToneClass(group.title, groupIndex)
        : "";
  section.className = ["agent-form-section", toneClass].filter(Boolean).join(" ");

  const header = document.createElement("div");
  header.className = "agent-form-section-header";
  const heading = document.createElement("h3");
  heading.textContent = group.title;
  header.appendChild(heading);
  section.appendChild(header);

  const fields = document.createElement("div");
  fields.className = "agent-form-section-fields";
  for (const field of group.fields) {
    fields.appendChild(createFormField(field));
  }
  section.appendChild(fields);

  return section;
}

function hospitalBagSectionToneClass(groupTitle, groupIndex) {
  const titleIndexMap = {
    基本信息: 0,
    生产信息: 1,
    医院信息: 2,
    偏好信息: 3,
  };
  const styleIndex = titleIndexMap[groupTitle] ?? groupIndex;
  return `agent-form-section-tone-${(styleIndex % 4) + 1}`;
}

function birthPlanSectionToneClass(groupTitle, groupIndex) {
  const titleIndexMap = {
    基本信息: 0,
    支持与沟通: 1,
    生产过程: 2,
    疼痛和舒适: 3,
    宝宝出生后: 4,
    临时变化: 5,
    提前问医院: 6,
    产程偏好: 2,
    助产干预: 2,
    舒适与镇痛: 3,
    计划变化与紧急情况: 5,
    医院确认与安全: 6,
  };
  const styleIndex = titleIndexMap[groupTitle] ?? groupIndex;
  return `agent-form-section-tone-${(styleIndex % 5) + 1}`;
}

function createCheckboxGroup(field) {
  const wrapper = document.createElement("fieldset");
  wrapper.className = "agent-form-field agent-form-checkbox-group";

  wrapper.appendChild(createFieldLabel("legend", field));

  const options = document.createElement("div");
  options.className = "agent-form-options";
  const defaultValues = defaultMultiSelectValues(field.default_value);
  for (const option of field.options || []) {
    const optionLabel = document.createElement("label");
    optionLabel.className = "agent-form-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = field.id;
    input.value = option;
    input.checked = defaultValues.includes(option);
    input.addEventListener("change", () => wrapper.classList.remove("invalid"));
    optionLabel.appendChild(input);
    const text = document.createElement("span");
    text.textContent = option;
    optionLabel.appendChild(text);
    options.appendChild(optionLabel);
  }
  wrapper.appendChild(options);

  if (field.help_text) {
    const help = document.createElement("small");
    help.textContent = field.help_text;
    wrapper.appendChild(help);
  }

  return wrapper;
}

function createFieldLabel(tagName, field) {
  const label = document.createElement(tagName);
  label.className = "agent-form-label";
  if (field.required) {
    const marker = document.createElement("span");
    marker.className = "agent-form-required-marker";
    marker.textContent = "*";
    marker.setAttribute("aria-hidden", "true");
    label.appendChild(marker);
  }
  label.appendChild(document.createTextNode(field.label || ""));
  return label;
}

function isMultiSelectField(field) {
  return ["multi_select", "checkbox_group"].includes(field?.type);
}

function defaultMultiSelectValues(value) {
  if (!hasFormDefaultValue(value)) return [];
  if (Array.isArray(value)) return value.map(String);
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function collectFormValues(node, formSpec) {
  const data = new FormData(node);
  const values = defaultFormValues(formSpec.default_values);
  for (const field of formSpec.fields || []) {
    values[field.id] = isMultiSelectField(field) ? data.getAll(field.id) : data.get(field.id) || "";
  }
  return values;
}

function defaultFormValues(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, fieldValue]) => [
      key,
      Array.isArray(fieldValue) ? fieldValue.map(String) : fieldValue,
    ]),
  );
}

function validateFormSelection(node, formSpec) {
  for (const field of formSpec.fields || []) {
    if (!field.required || !isMultiSelectField(field)) continue;
    if (new FormData(node).getAll(field.id).length) continue;
    const group = node.querySelector(`[name="${cssEscape(field.id)}"]`)?.closest(".agent-form-checkbox-group");
    if (group) group.classList.add("invalid");
    return `请选择：${splitFormFieldLabel(field.label).fieldLabel}`;
  }
  return "";
}

function buildFormConfirmationMessage(formSpec, values) {
  return [
    `我已确认 ${formSpec.title || "表单"} 信息，请基于这些信息生成对应卡片。`,
    `form_id: ${formSpec.id || "form"}`,
    "confirmed_form_data:",
    JSON.stringify(values, null, 2),
  ].join("\n");
}

function hasDisplayValue(value) {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function hasFormDefaultValue(value) {
  if (Array.isArray(value)) return value.some((item) => hasFormDefaultValue(item));
  if (value && typeof value === "object") return Object.values(value).some((item) => hasFormDefaultValue(item));
  return hasDisplayValue(value) && !isConfirmPlaceholder(value);
}

function isConfirmPlaceholder(value) {
  return ["", "to confirm", "待确认", "未确定", "不确定", "还不确定", "还没确定", "还没想好", "none", "n/a"].includes(
    String(value || "")
      .trim()
      .toLowerCase(),
  );
}

function normalizeList(values) {
  if (!Array.isArray(values)) return [];
  return values.filter(hasDisplayValue);
}

function limitList(values, maxItems) {
  return normalizeList(values).slice(0, maxItems);
}

function appendValue(node, value) {
  if (Array.isArray(value)) {
    node.textContent = value.length ? value.join(", ") : "";
    return;
  }
  if (value && typeof value === "object") {
    node.textContent = Object.entries(value)
      .filter(([, nestedValue]) => hasDisplayValue(nestedValue))
      .map(([key, nestedValue]) => `${formatLabel(key)}: ${formatPlainValue(nestedValue)}`)
      .join("; ");
    return;
  }
  node.textContent = formatPlainValue(value);
  if (value === "To confirm") node.classList.add("to-confirm");
}

function formatPlainValue(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function formatLabel(key) {
  return String(key)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function setBusy(isBusy) {
  send.disabled = isBusy;
  input.disabled = isBusy;
  if (attachImage) attachImage.disabled = isBusy;
  if (imageInput) imageInput.disabled = isBusy;
}

function removeMessageElement(node) {
  const row = node?.closest(".message-row");
  if (row) {
    row.remove();
  } else if (node) {
    node.remove();
  }
}

function updateMeta(data = {}) {
  const threadId = data.conversation_id || data.thread_id || data.threadId;
  if (threadId) {
    conversationId = threadId;
    localStorage.setItem("momcozy_conversation_id", conversationId);
  }
  if (conversationLabel) {
    conversationLabel.textContent = conversationId ? `Conversation: ${conversationId.slice(0, 8)}` : "No conversation yet";
  }
  const skills = data.loaded_skill_ids || [];
  if (skillsLabel) {
    skillsLabel.textContent = skills.length ? `Loaded skills: ${skills.join(", ")}` : "No loaded skills";
  }
}

async function addPendingImageFiles(files) {
  if (!files.length) return;
  const remainingSlots = MAX_IMAGE_ATTACHMENTS - pendingImages.length;
  if (remainingSlots <= 0) {
    addMessage("error", `最多一次发送 ${MAX_IMAGE_ATTACHMENTS} 张图片。`);
    return;
  }

  const acceptedFiles = files.slice(0, remainingSlots);
  if (files.length > remainingSlots) {
    addMessage("error", `最多一次发送 ${MAX_IMAGE_ATTACHMENTS} 张图片，已忽略多余图片。`);
  }

  for (const file of acceptedFiles) {
    if (!file.type.startsWith("image/")) {
      addMessage("error", `无法添加 ${file.name || "该文件"}：只支持图片。`);
      continue;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      addMessage("error", `无法添加 ${file.name || "该图片"}：图片不能超过 6MB。`);
      continue;
    }
    pendingImages.push(await imageFileToAttachment(file));
  }
  renderPendingImages();
}

function imageFileToAttachment(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve({
        id: crypto.randomUUID(),
        name: file.name,
        mime_type: file.type,
        size: file.size,
        image_url: String(reader.result || ""),
        detail: "auto",
      });
    };
    reader.onerror = () => reject(reader.error || new Error("Image read failed."));
    reader.readAsDataURL(file);
  });
}

function renderPendingImages() {
  if (!imagePreviewStrip) return;
  imagePreviewStrip.innerHTML = "";
  imagePreviewStrip.hidden = pendingImages.length === 0;
  for (const image of pendingImages) {
    const item = document.createElement("div");
    item.className = "image-preview";

    const img = document.createElement("img");
    img.src = image.image_url;
    img.alt = image.name || "Selected image";
    item.appendChild(img);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "image-remove";
    remove.setAttribute("aria-label", "Remove image");
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      pendingImages = pendingImages.filter((pendingImage) => pendingImage.id !== image.id);
      renderPendingImages();
      input.focus();
    });
    item.appendChild(remove);

    imagePreviewStrip.appendChild(item);
  }
}

function clearPendingImages() {
  pendingImages = [];
  renderPendingImages();
  if (imageInput) imageInput.value = "";
}

function clientLocale() {
  return navigator.language || "en-US";
}

function clientTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Los_Angeles";
}

function clientMessageSentAt(date = new Date()) {
  const pad = (value, length = 2) => String(Math.trunc(Math.abs(value))).padStart(length, "0");
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const offsetHours = Math.floor(Math.abs(offsetMinutes) / 60);
  const offsetRemainder = Math.abs(offsetMinutes) % 60;
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    "T",
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
    ".",
    pad(date.getMilliseconds(), 3),
    `${sign}${pad(offsetHours)}:${pad(offsetRemainder)}`,
  ].join("");
}

function buildAgUiPayload(text, images = []) {
  const threadId = conversationId || `thread_${crypto.randomUUID()}`;
  conversationId = threadId;
  localStorage.setItem("momcozy_conversation_id", conversationId);
  runCount += 1;
  localStorage.setItem("momcozy_run_count", String(runCount));
  const locale = clientLocale();
  const timezone = clientTimezone();
  const messageSentAt = clientMessageSentAt();
  const content = images.length
    ? [
        ...(text ? [{ type: "text", text }] : []),
        ...images.map((image) => ({
          type: "image",
          image_url: image.image_url,
          mime_type: image.mime_type,
          name: image.name,
          size: image.size,
          detail: image.detail || "auto",
        })),
      ]
    : text;

  return {
    threadId,
    userId: clientUserId,
    runId: `run_${Date.now()}_${runCount}`,
    state: {
      user_id: clientUserId,
      locale,
      timezone,
      message_sent_at: messageSentAt,
      user_profile: {
        user_id: clientUserId,
        language: locale,
      },
    },
    messages: [
      {
        id: `msg_${Date.now()}`,
        role: "user",
        content,
      },
    ],
    tools: [],
    context: [],
    forwardedProps: {
      user_id: clientUserId,
      locale,
      timezone,
      message_sent_at: messageSentAt,
    },
  };
}

async function streamChat(text, workRun, images = []) {
  const response = await fetch("/api/ag-ui", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(buildAgUiPayload(text, images)),
  });

  if (!response.ok || !response.body) {
    let message = `Request failed with ${response.status}`;
    try {
      const data = await response.json();
      message = data.error || message;
    } catch (_) {
      // Keep the status-based message when the body is not JSON.
    }
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalData = {};
  let activeToolName = "";
  const toolNamesByKey = new Map();
  const usedTools = new Set();
  let lastStatus = "";
  let assistantNode = null;
  let formShown = false;
  let cardShown = false;
  let statusNode = null;

  function ensureStatusNode() {
    if (!statusNode) {
      statusNode = addStatusNode();
    }
    return statusNode;
  }

  function ensureAssistantNode() {
    if (!assistantNode) {
      assistantNode = addMessage("assistant", "");
    }
    return assistantNode;
  }

  function rememberToolName(event, toolName) {
    toolName = normalizeToolName(toolName);
    if (!toolName) return;
    for (const key of workItemKeysForToolCall(event)) {
      toolNamesByKey.set(key, toolName);
    }
  }

  function toolNameForEvent(event, fallback = "tool") {
    return normalizeToolName(event.tool_call_name || toolNamesByKey.get(workItemKeyForToolCall(event)) || activeToolName || fallback);
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";

    for (const rawEvent of events) {
      const event = parseSseEvent(rawEvent);
      if (!event) continue;

      if (event.type === "RUN_STARTED") {
        updateMeta(event);
      } else if (event.type === "ACTIVITY_SNAPSHOT") {
        updateMeta(event.content?.metadata || {});
      } else if (event.type === "CUSTOM" && event.name === THINKING_EVENT_NAME) {
        if (["started", "running"].includes(event.value?.status)) {
          showThinking(workRun, {
            afterOutput: Boolean(event.value?.metadata?.after_output_text),
            title: event.value?.metadata?.after_output_text ? "正在准备下一步" : "正在思考",
          });
        } else if (["completed", "failed"].includes(event.value?.status)) {
          completeThinking(workRun);
        }
      } else if (event.type === "CUSTOM" && event.name === "momcozy.agent.status") {
        updateMeta(event.value?.metadata || {});
      } else if (event.type === "STEP_STARTED") {
        updateMeta(event.metadata || {});
      } else if (event.type === "STEP_FINISHED") {
        updateMeta(event.metadata || {});
      } else if (event.type === "TOOL_CALL_START") {
        activeToolName = normalizeToolName(event.tool_call_name || "");
        if (activeToolName) usedTools.add(activeToolName);
        rememberToolName(event, activeToolName);
        addWorkToolStart(workRun, event);
      } else if (event.type === "TOOL_CALL_ARGS") {
        const toolName = toolNameForEvent(event);
        if (toolName) usedTools.add(toolName);
        addWorkToolArgs(workRun, event, toolName);
      } else if (event.type === "TOOL_CALL_END") {
        const toolName = toolNameForEvent(event);
        if (toolName) usedTools.add(toolName);
        addWorkToolEnd(workRun, event, toolName);
      } else if (event.type === "TOOL_CALL_RESULT") {
        const result = parseJson(event.content) || {};
        const toolName = normalizeToolName(result.tool_name || event.tool_call_name || toolNameForEvent(event));
        usedTools.add(toolName);
        rememberToolName(event, toolName);
        updateMeta({ loaded_skill_ids: result.skill_id ? [result.skill_id] : undefined });
        addWorkToolResult(workRun, event, result, toolName);
      } else if (event.type === "ARTIFACT_CREATED") {
        const artifactKind = addArtifactFromEvent(event);
        if (artifactKind === "form") formShown = true;
        if (artifactKind === "card") cardShown = true;
        if (artifactKind) assistantNode = null;
      } else if (event.type === "CONFIRMATION_REQUIRED") {
        addWorkConfirmationRequired(workRun, event);
      } else if (event.type === "TEXT_MESSAGE_CONTENT") {
        workRun.hasOutput = true;
        completeThinking(workRun);
        appendAssistantMarkdown(ensureAssistantNode(), event.delta || "");
        messages.scrollTop = messages.scrollHeight;
      } else if (event.type === "TEXT_MESSAGE_END") {
        if (statusNode) lastStatus = updateStatus(statusNode, "Answer ready.", { lastStatus, usedTools, done: true });
      } else if (event.type === "RUN_FINISHED") {
        finalData = event;
        completeThinking(workRun);
        if (statusNode) lastStatus = updateStatus(statusNode, "Run finished.", { lastStatus, usedTools, done: true });
        if (!formShown && !cardShown && (!assistantNode || !getAssistantMarkdown(assistantNode).trim())) {
          setAssistantMarkdown(ensureAssistantNode(), "(No text response)");
        }
      } else if (event.type === "RUN_ERROR") {
        completeThinking(workRun);
        throw new Error(event.message || "Run failed");
      }
    }

    if (done) break;
  }

  return finalData;
}

async function sendUserText(text, options = {}) {
  const images = options.images || [];
  if (!text.trim() && images.length === 0) return;

  addMessage("user", options.displayText || text, { images });
  const workRun = createWorkRun();
  setBusy(true);

  try {
    await streamChat(text, workRun, images);
    finishWorkPanel(workRun);
  } catch (error) {
    finishWorkPanel(workRun, { failed: true, message: error.message });
    addMessage("error", error.message);
  } finally {
    setBusy(false);
    input.focus();
  }
}

function parseSseEvent(rawEvent) {
  const dataLines = rawEvent
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());
  if (!dataLines.length) return null;
  return JSON.parse(dataLines.join("\n"));
}

function updateStatus(node, text, options = {}) {
  if (!node || !text) return options.lastStatus || "";
  if (text === options.lastStatus && !options.done) return options.lastStatus;

  const statusText = node.querySelector(".status-text");
  const statusTools = node.querySelector(".status-tools");
  const normalizedText = labelForStatus(text);
  if (statusText) statusText.textContent = normalizedText;
  if (statusTools) {
    const stepCount = (options.usedTools || new Set()).size || 0;
    statusTools.textContent = stepCount ? `已处理 ${stepCount} 个步骤` : "";
  }
  node.classList.toggle("done", Boolean(options.done));
  messages.scrollTop = messages.scrollHeight;
  return text;
}

function labelForStatus(text) {
  const labels = {
    "Agent loop started.": "正在处理...",
    "Evaluating user intent and safety context.": "正在判断需求...",
    "Requesting model response.": "正在理解你的需求...",
    "Requesting model response with tool outputs.": "正在整理结果...",
    "Selecting an application tool.": "正在选择合适能力...",
    "Executing an application tool.": "正在读取相关信息...",
    "Selecting the next step.": "正在选择下一步...",
    "Loading relevant context.": "正在读取相关信息...",
    "Reading relevant information.": "正在读取相关信息...",
    "Running a processing step.": "正在执行处理流程...",
    "Processing relevant information.": "正在处理相关信息...",
    "Step completed.": "步骤已完成。",
    "Step failed.": "步骤没有完成。",
    "Answer ready.": "回答已准备好。",
    "Run finished.": "已完成。",
  };
  return labels[text] || text;
}

function labelForStep(stepName, state) {
  if (stepName === "routing") {
    return state === "started" ? "正在判断需求..." : "需求判断完成。";
  }
  return state === "started" ? "正在处理当前步骤..." : "当前步骤已完成。";
}

function normalizeToolName(toolName) {
  const token = String(toolName || "").trim();
  if (!token) return "";
  return token.split(".").pop().replace(/^milk_management__/, "");
}

function toolWorkPhase(toolName) {
  toolName = normalizeToolName(toolName);
  if (toolName === "tool_search" || toolName === "tool_search_call") return "select";
  if (
    [
      "load_skill",
      "list_skills",
      "read_skill_file",
      "search_skill_assets",
      "profile_get",
      "memory_search",
      "milk_snapshot_get",
      "milk_status_query",
      "milk_records_query",
      "milk_plan_query",
      "milk_calendar_query",
      "device_manual_search",
      "knowledge_search",
      "reminder_list",
    ].includes(toolName)
  ) {
    return "read";
  }
  if (["milk_assessment_evaluate", "infant_growth_evaluate", "risk_evaluate"].includes(toolName)) {
    return "evaluate";
  }
  if (
    [
      "ui_form_create",
      "labor_communication_card_create",
      "birth_journey_plan_card_create",
      "hospital_bag_card_create",
      "ibclc_consult_card_create",
      "support_ticket_draft_create",
    ].includes(toolName)
  ) {
    return "prepare_result";
  }
  if (["milk_plan_preview", "milk_calendar_change_preview"].includes(toolName)) return "preview";
  if (
    [
      "milk_record_mutate",
      "milk_plan_mutate",
      "milk_calendar_mutate",
      "milk_task_complete",
      "infant_growth_mutate",
      "reminder_create",
      "reminder_update",
      "reminder_delete",
    ].includes(toolName)
  ) {
    return "save";
  }
  if (toolName === "run_approved_skill_script") return "process";
  return "work";
}

function toolStartCopy(toolName) {
  switch (toolWorkPhase(toolName)) {
    case "select":
      return { title: "正在选择合适能力" };
    case "read":
      return { title: "正在读取相关信息" };
    case "evaluate":
      return { title: "正在评估情况" };
    case "prepare_result":
      return { title: "正在准备结果" };
    case "preview":
      return { title: "正在生成预览" };
    case "save":
      return { title: "正在准备保存修改" };
    case "process":
      return { title: "正在执行处理流程" };
    default:
      return { title: "正在处理请求" };
  }
}

function toolEndCopy(toolName) {
  switch (toolWorkPhase(toolName)) {
    case "select":
      return { title: "正在确认可用能力" };
    case "read":
      return { title: "正在整理相关信息" };
    case "evaluate":
      return { title: "正在计算评估结果" };
    case "prepare_result":
      return { title: "正在生成结果" };
    case "preview":
      return { title: "正在生成预览" };
    case "save":
      return { title: "正在保存修改" };
    case "process":
      return { title: "正在执行处理流程" };
    default:
      return { title: "正在处理请求" };
  }
}

function toolResultCopy(toolName, result) {
  const errorMessage = result?.error?.message || "这个步骤没有成功完成。";
  if (result?.ok === false) {
    return { title: "步骤没有完成", detail: errorMessage };
  }

  const status = String(result?.status || "");
  if (status === "plan_preview_needs_revision") {
    return { title: "结果需要调整", detail: "保存前校验未通过，暂不能确认。" };
  }
  if (status === "plan_preview_not_recommended") {
    return { title: "当前方案暂不建议继续" };
  }
  if (status === "plan_preview_needs_medical_confirmation") {
    return { title: "需要先确认健康边界" };
  }
  if (result?.requires_confirmation === true) {
    return { title: "预览已准备好", detail: "确认后才会生效。" };
  }

  switch (toolWorkPhase(toolName)) {
    case "select":
      return { title: "可用能力已准备好" };
    case "read":
      return { title: "相关信息已读取" };
    case "evaluate":
      return { title: "评估已完成" };
    case "prepare_result":
    case "preview":
      return { title: "结果已准备好" };
    case "save":
      return { title: saveResultTitle(status) };
    case "process":
      return { title: "处理流程已完成" };
    default:
      return { title: "步骤已完成" };
  }
}

function saveResultTitle(status) {
  if (status.includes("deleted")) return "修改已删除";
  if (status.includes("idempotent_replay")) return "已复用已有修改";
  if (status === "milk_task_completion_cancelled") return "修改已取消";
  return "修改已保存";
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

function parseJson(value) {
  if (!value || typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch (_) {
    return null;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  const images = pendingImages.slice();
  if (!text && images.length === 0) return;

  input.value = "";
  clearPendingImages();
  await sendUserText(text, { images });
});

reset.addEventListener("click", () => {
  conversationId = "";
  localStorage.removeItem("momcozy_conversation_id");
  localStorage.removeItem(IBCLC_CONSULT_COMPLETED_KEY);
  messages.innerHTML = "";
  clearPendingImages();
  addNewConversationGreeting();
  updateMeta();
  input.focus();
});

attachImage?.addEventListener("click", () => {
  imageInput?.click();
});

imageInput?.addEventListener("change", async () => {
  try {
    await addPendingImageFiles(Array.from(imageInput.files || []));
  } catch (error) {
    addMessage("error", error.message || "图片读取失败。");
  } finally {
    if (imageInput) imageInput.value = "";
    input.focus();
  }
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

window.addEventListener("storage", (event) => {
  if (event.key === IBCLC_CONSULT_COMPLETED_KEY) {
    handleIbclcConsultCompleted(parseJson(event.newValue));
  }
});

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;
  handleIbclcConsultCompleted(event.data);
});

window.addEventListener("focus", () => {
  handleIbclcConsultCompleted(readStoredIbclcConsultCompletion());
});

window.addEventListener("pageshow", () => {
  handleIbclcConsultCompleted(readStoredIbclcConsultCompletion());
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    handleIbclcConsultCompleted(readStoredIbclcConsultCompletion());
  }
});

updateMeta();
addNewConversationGreeting();
