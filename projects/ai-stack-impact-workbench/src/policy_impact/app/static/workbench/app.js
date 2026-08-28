const DEFAULT_MODEL_ID = "deepseek-v4-flash";

const state = {
  companies: [],
  companyId: "company_001",
  sessions: [],
  currentSessionId: "",
  skills: [],
  models: [],
  modelStatus: null,
  messages: [],
  trace: null,
  tracesByRun: {},
  wiki: null,
  wikiAction: "",
  currentView: "chat",
  selectedWikiPath: "",
  sending: false,
  slashOpen: false,
  slashIndex: 0,
  slashQuery: "",
  slashMatches: [],
  showTestSessions: false,
  sessionLoadEpoch: 0,
  lastError: "",
  streaming: null,
  composing: false,
};

const $ = (id) => document.getElementById(id);

const labels = {
  modes: {
    auto: "默认对话",
    chat: "普通对话",
    skill: "指定技能",
    impact: "影响分析",
    policy: "政策分析",
    news: "新闻报告",
    research: "调研报告",
    wiki: "知识库规划",
  },
  skills: {
    "": "默认对话",
    general_chat: "普通对话",
    external_impact_report: "外部变化影响分析",
    policy_weekly_impact: "政策影响分析",
    recent_news_report: "新闻影响报告",
    research_report: "调研报告生成",
    company_wiki_blueprint: "企业知识库规划",
  },
  status: {
    running: "运行中",
    done: "完成",
    failed: "失败",
    blocked: "阻塞",
  },
  stepTypes: {
    intent_classification: "意图识别",
    skill_selection: "技能选择",
    context_build: "上下文构建",
    memory_read: "读取记忆",
    memory_write: "写入记忆",
    evidence_replay: "复现证据",
    tool_call: "工具调用",
    gate_check: "门控检查",
    model_call: "模型调用",
    subagent_plan: "子智能体规划",
    subagent_context: "子智能体上下文",
    subagent_delegate: "子智能体执行",
    artifact_write: "写入产物",
    workflow_stage: "工作流阶段",
    final_answer: "最终回答",
  },
  loopPhases: {
    think: "思考",
    act: "执行",
    observe: "观察",
    answer: "回答",
  },
  artifact: {
    report: "Markdown 报告",
    html_report: "HTML 报告",
    run_artifact: "运行产物",
    wiki_blueprint: "知识库规划",
  },
};

const slashCommands = [
  {
    command: "/report",
    skillId: "external_impact_report",
    name: "生成影响报告",
    description: "分析新闻、政策、行业事件、技术发布或数据源状态，并沉淀报告产物。",
  },
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
  }
  return data;
}

function parseSseBlock(block) {
  const event = { event: "message", data: "" };
  block.split(/\r?\n/).forEach((line) => {
    if (line.startsWith("event:")) event.event = line.slice(6).trim();
    if (line.startsWith("data:")) event.data += line.slice(5).trim();
  });
  if (!event.data) return null;
  try {
    return { type: event.event, payload: JSON.parse(event.data) };
  } catch (error) {
    return { type: "error", payload: { detail: `SSE JSON 解析失败：${error.message}` } };
  }
}

async function streamApi(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_error) {
      data = { detail: text };
    }
    throw new Error(data.detail || response.statusText);
  }
  if (!response.body?.getReader) {
    throw new Error("当前浏览器不支持流式响应读取");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (event) await onEvent(event.type, event.payload);
      }
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer.trim());
    if (event) await onEvent(event.type, event.payload);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function currentSession() {
  return state.sessions.find((item) => item.session_id === state.currentSessionId) || null;
}

function currentSkillId() {
  return currentSession()?.active_skill_id || "";
}

function skillById(skillId) {
  return state.skills.find((skill) => skill.id === skillId) || null;
}

function skillLabel(skillId) {
  return labels.skills[skillId] || skillById(skillId)?.name || skillId || "自动选择";
}

function statusLabel(status) {
  return labels.status[status] || status || "未知";
}

function stepTypeLabel(stepType) {
  return labels.stepTypes[stepType] || stepType || "步骤";
}

function loopPhaseLabel(phase) {
  return labels.loopPhases[phase] || phase || "阶段";
}

function artifactLabel(name) {
  return labels.artifact[name] || name;
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 8)}...${text.slice(-4)}` : text;
}

function artifactDisplayValue(value) {
  const text = String(value || "");
  const parts = text.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : text;
}

function artifactHref(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return `/companies/${encodeURIComponent(state.companyId)}/workbench/artifact?path=${encodeURIComponent(text)}`;
}

function artifactActionLabel(key) {
  if (key === "html_report") return "打开 HTML 报告";
  if (key === "report") return "查看 Markdown";
  if (key === "run_artifact") return "查看 RunArtifact";
  return "查看产物";
}

function redactLocalPaths(value) {
  return String(value || "")
    .replace(/[A-Za-z]:\\[^\s<>"']+/g, (path) => {
      const name = path.split(/[/\\]/).filter(Boolean).pop() || "本地产物";
      return `[本地产物: ${name}]`;
    });
}

function redactTraceValue(value) {
  if (Array.isArray(value)) return value.map(redactTraceValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactTraceValue(item)]));
  }
  return typeof value === "string" ? redactLocalPaths(value) : value;
}

function summarizeValue(value) {
  if (value === null || value === undefined || value === "") return "空";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === "object") {
    const keys = Object.keys(value);
    return keys.length ? `${keys.slice(0, 4).join(" / ")}${keys.length > 4 ? " ..." : ""}` : "空对象";
  }
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length > 110 ? `${text.slice(0, 110)}...` : text;
}

function sumNumericValues(value) {
  if (!value || typeof value !== "object") return 0;
  return Object.values(value).reduce((total, item) => total + (Number(item) || 0), 0);
}

function uniqueNames(items, key, limit = 4) {
  const values = [];
  items.forEach((item) => {
    const value = item?.[key];
    if (value && !values.includes(value)) values.push(value);
  });
  const visible = values.slice(0, limit);
  const suffix = values.length > limit ? ` +${values.length - limit}` : "";
  return visible.join(" / ") + suffix;
}

function renderCompactDict(obj, emptyText = "无") {
  const entries = Object.entries(obj || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!entries.length) return `<div class="detail-empty">${escapeHtml(emptyText)}</div>`;
  return entries
    .slice(0, 10)
    .map(
      ([key, value]) => `
        <div class="detail-kv">
          <span>${escapeHtml(key)}</span>
          <strong>${escapeHtml(summarizeValue(value))}</strong>
        </div>
      `,
    )
    .join("");
}

function commandByToken(token) {
  const normalized = String(token || "").trim().toLowerCase();
  return slashCommands.find((item) => item.command === normalized) || null;
}

function getPromptEditor() {
  return $("prompt-input");
}

function getPromptText() {
  return (getPromptEditor()?.textContent || "").replace(/\u00a0/g, " ");
}

function getCaretOffset(editor = getPromptEditor()) {
  const selection = window.getSelection();
  if (!editor || !selection?.rangeCount || !editor.contains(selection.anchorNode)) {
    return getPromptText().length;
  }
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(editor);
  range.setEnd(selection.anchorNode, selection.anchorOffset);
  return range.toString().length;
}

function setCaretOffset(editor, offset) {
  if (!editor) return;
  const target = Math.max(0, Number(offset) || 0);
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let remaining = target;
  let node = walker.nextNode();
  while (node) {
    const length = node.textContent.length;
    if (remaining <= length) {
      range.setStart(node, remaining);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    remaining -= length;
    node = walker.nextNode();
  }
  range.selectNodeContents(editor);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function highlightPromptText(text) {
  const raw = String(text || "");
  if (!raw) return "";
  const pattern = /(^|[\s\u3000])(\/[A-Za-z_][\w-]*)/g;
  let html = "";
  let index = 0;
  let match = pattern.exec(raw);
  while (match) {
    const prefix = match[1] || "";
    const token = match[2] || "";
    const tokenStart = match.index + prefix.length;
    if (commandByToken(token)) {
      html += escapeHtml(raw.slice(index, tokenStart));
      html += `<span class="inline-command-token">${escapeHtml(token)}</span>`;
      index = tokenStart + token.length;
    }
    match = pattern.exec(raw);
  }
  html += escapeHtml(raw.slice(index));
  return html;
}

function renderPromptSyntax(caretOffset = getCaretOffset()) {
  const editor = getPromptEditor();
  if (!editor || state.composing) return;
  const raw = getPromptText();
  editor.innerHTML = highlightPromptText(raw);
  if (document.activeElement === editor) {
    setCaretOffset(editor, Math.min(caretOffset, raw.length));
  }
  autoGrow(editor);
}

function setPromptText(text, caretOffset = String(text || "").length) {
  const editor = getPromptEditor();
  if (!editor) return;
  editor.textContent = String(text || "");
  renderPromptSyntax(caretOffset);
}

function slashQueryRange(raw = getPromptText(), caretOffset = getCaretOffset()) {
  const beforeCursor = raw.slice(0, caretOffset);
  const match = beforeCursor.match(/(^|[\s\u3000])(\/[A-Za-z_][\w-]*)$/) || beforeCursor.match(/(^|[\s\u3000])(\/)$/);
  if (!match) return null;
  const token = match[2] || "";
  return {
    start: caretOffset - token.length,
    end: caretOffset,
    query: token.toLowerCase(),
  };
}

function replacePromptRange(start, end, insertion) {
  const raw = getPromptText();
  const safeStart = Math.max(0, Math.min(start, raw.length));
  const safeEnd = Math.max(safeStart, Math.min(end, raw.length));
  const next = `${raw.slice(0, safeStart)}${insertion}${raw.slice(safeEnd)}`;
  setPromptText(next, safeStart + insertion.length);
}

function extractInlineSlashCommand(raw) {
  const text = String(raw || "").trim();
  const pattern = /(^|[\s\u3000])(\/[A-Za-z_][\w-]*)(?=$|[\s\u3000])/g;
  let match = pattern.exec(text);
  while (match) {
    const command = commandByToken(match[2]);
    if (command) {
      const tokenStart = match.index + (match[1] || "").length;
      const tokenEnd = tokenStart + match[2].length;
      const message = `${text.slice(0, tokenStart)} ${text.slice(tokenEnd)}`
        .replace(/\s+/g, " ")
        .trim();
      return {
        command,
        message: message || `请执行${command.name}。`,
      };
    }
    match = pattern.exec(text);
  }
  return { command: null, message: text };
}

function normalizePromptForRuntime(raw) {
  const parsed = extractInlineSlashCommand(raw);
  return parsed.command ? `${parsed.command.command} ${parsed.message}`.trim() : parsed.message;
}

function setPromptDisabled(disabled) {
  const editor = getPromptEditor();
  if (!editor) return;
  editor.setAttribute("contenteditable", disabled ? "false" : "true");
  editor.classList.toggle("is-disabled", Boolean(disabled));
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => node.classList.add("hidden"), 3000);
}

function modelAvailable() {
  return Boolean(state.modelStatus?.available);
}

function modelStatusText() {
  if (!state.modelStatus) return "模型状态读取中";
  if (state.modelStatus.available) return `${DEFAULT_MODEL_ID} 已就绪`;
  const missing = (state.modelStatus.missing || []).join("、") || "模型鉴权";
  return `${DEFAULT_MODEL_ID} 未就绪：缺少 ${missing}`;
}

function isBrokenText(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  if (text.includes("�")) return true;
  const questionMarks = (text.match(/\?/g) || []).length;
  const meaningful = text.replace(/[?\s_\-./:]/g, "");
  return questionMarks >= 3 && (questionMarks / text.length >= 0.2 || meaningful.length < 4);
}

function sessionTitle(session) {
  const title = String(session?.title || "").trim();
  const genericTitle = new Set(["policy", "news", "research", "chat", "auto"]).has(title.toLowerCase());
  if (title && !isBrokenText(title) && title !== "新对话" && !genericTitle) return title;
  const firstMessage = String(session?.first_user_message || "").replace(/\s+/g, " ").trim();
  if (firstMessage && !isBrokenText(firstMessage)) {
    return firstMessage.length > 26 ? `${firstMessage.slice(0, 26)}…` : firstMessage;
  }
  return title && !isBrokenText(title) ? title : "未命名对话";
}

function isTestSession(session) {
  const title = `${session?.title || ""} ${session?.first_user_message || ""}`.toLowerCase();
  return [
    "api test",
    "blind test",
    "test -",
    "测试用例",
    "验收-",
    "验收_",
    "acceptance",
    "benchmark",
    "fullcap",
    "multi-turn",
    "dialogue acceptance",
    "credential check",
    "design check",
    "no credential",
    "no model fetch",
    "please analyze recent policy impact",
  ].some((marker) => title.includes(marker));
}

function sessionGroup(session) {
  const raw = session.last_message_at || session.updated_at || session.created_at;
  const value = new Date(raw);
  if (Number.isNaN(value.getTime())) return "更早";
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startValue = new Date(value.getFullYear(), value.getMonth(), value.getDate());
  const days = Math.round((startToday - startValue) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days <= 7) return "最近 7 天";
  return "更早";
}

function normalizeCompany(item) {
  if (typeof item === "string") {
    return {
      company_id: item,
      company_name: item,
      short_name: item,
      stock_code: "",
      business_keywords: [],
      fact_count: 0,
      high_importance_fact_count: 0,
      open_question_count: 0,
    };
  }
  return {
    company_id: item.company_id || item.id || "company_001",
    company_name: item.company_name || item.name || item.company_id || "company_001",
    short_name: item.short_name || item.company_name || item.company_id || "company_001",
    stock_code: item.stock_code || "",
    business_keywords: Array.isArray(item.business_keywords) ? item.business_keywords : [],
    fact_count: Number(item.fact_count || 0),
    high_importance_fact_count: Number(item.high_importance_fact_count || 0),
    open_question_count: Number(item.open_question_count || 0),
  };
}

function companyIdOf(company) {
  return typeof company === "string" ? company : company.company_id;
}

function currentCompany() {
  return state.companies.find((company) => companyIdOf(company) === state.companyId) || normalizeCompany(state.companyId);
}

function companyOptionLabel(company) {
  const item = normalizeCompany(company);
  const code = item.stock_code ? ` · ${item.stock_code}` : "";
  return `${item.short_name || item.company_id}${code}`;
}

function renderCompanies() {
  $("company-select").innerHTML = state.companies
    .map((company) => {
      const item = normalizeCompany(company);
      return `<option value="${escapeHtml(item.company_id)}">${escapeHtml(companyOptionLabel(item))}</option>`;
    })
    .join("");
  $("company-select").value = state.companyId;
  const company = normalizeCompany(currentCompany());
  const keywordText = company.business_keywords.slice(0, 3).join(" / ");
  $("company-card").innerHTML = `
    <div class="company-name">${escapeHtml(company.company_name)}</div>
    <div class="company-meta">${escapeHtml([company.company_id, company.stock_code].filter(Boolean).join(" · "))}</div>
    <div class="company-metrics">
      <span>${escapeHtml(String(company.fact_count))} facts</span>
      <span>${escapeHtml(String(company.high_importance_fact_count))} high</span>
      <span>${escapeHtml(String(company.open_question_count))} open</span>
    </div>
    ${keywordText ? `<div class="company-keywords">${escapeHtml(keywordText)}</div>` : ""}
  `;
}

function renderSessions() {
  const query = $("session-search").value.trim().toLowerCase();
  const sessions = state.sessions
    .slice()
    .sort((a, b) => {
      const aHasMessages = Number(a.message_count || 0) > 0 ? 1 : 0;
      const bHasMessages = Number(b.message_count || 0) > 0 ? 1 : 0;
      if (aHasMessages !== bHasMessages) return bHasMessages - aHasMessages;
      return String(b.last_message_at || b.updated_at || "").localeCompare(String(a.last_message_at || a.updated_at || ""));
    })
    .filter((session) => {
      const haystack = [
        session.title || "",
        session.first_user_message || "",
        sessionTitle(session),
        session.session_id || "",
        session.active_skill_id || "",
        skillLabel(session.active_skill_id || ""),
      ]
        .join(" ")
        .toLowerCase();
      const matchesQuery = !query || haystack.includes(query);
      const visibleByType = state.showTestSessions || !isTestSession(session) || Boolean(query);
      const hasMessages = Number(session.message_count || 0) > 0;
      const visibleEmptySession = session.session_id === state.currentSessionId;
      return matchesQuery && visibleByType && (hasMessages || visibleEmptySession);
    })
    .slice(0, 60);
  const filterButton = $("toggle-test-sessions");
  filterButton.textContent = state.showTestSessions ? "隐藏测试" : "显示测试";
  filterButton.setAttribute("aria-pressed", String(state.showTestSessions));
  if (!sessions.length) {
    $("session-list").innerHTML = `
      <div class="session-empty">
        <strong>暂无匹配会话</strong>
        <span>清空搜索词，或点击上方新建对话。</span>
      </div>
    `;
    return;
  }
  const groups = ["今天", "昨天", "最近 7 天", "更早"];
  $("session-list").innerHTML = groups
    .map((group) => {
      const items = sessions.filter((session) => sessionGroup(session) === group);
      if (!items.length) return "";
      return `
        <div class="session-group-label">${escapeHtml(group)}</div>
        ${items
          .map((session) => {
            const active = session.session_id === state.currentSessionId ? "active" : "";
            const meta = [skillLabel(session.active_skill_id || ""), formatTime(session.last_message_at || session.updated_at)]
              .filter(Boolean)
              .join(" · ");
            return `
              <button class="session-item ${active}" data-session-id="${escapeHtml(session.session_id)}" ${state.sending ? "disabled" : ""}>
                <span class="session-name">${escapeHtml(sessionTitle(session))}</span>
                <span class="session-meta-line">${escapeHtml(meta)}</span>
              </button>
            `;
          })
          .join("")}
      `;
    })
    .join("");
  document.querySelectorAll(".session-item").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function renderCommands() {
  $("command-list").innerHTML = slashCommands
    .map(
      (item) => `
        <div class="command-row">
          <span class="slash-code">${escapeHtml(item.command)}</span>
          <span class="command-name">${escapeHtml(item.name)}</span>
        </div>
      `,
    )
    .join("");
}

function renderTools() {
  const sessionSkillId = currentSkillId();
  const activeSkillId = sessionSkillId || "general_chat";
  $("active-skill-chip").textContent = skillLabel(sessionSkillId);
  $("composer-skill-chip").textContent = state.currentView === "wiki" ? "Wiki 数据页" : skillLabel(sessionSkillId);
  const skill = skillById(activeSkillId);
  const tools = Object.entries(skill?.tool_policy || {}).flatMap(([stage, values]) =>
    (values || []).map((tool) => ({ stage, tool })),
  );
  $("tool-list").innerHTML = tools.length
    ? tools
        .map(
          (item) => `
            <div class="tool-row">
              <div class="tool-name">${escapeHtml(item.tool)}</div>
              <div class="tool-meta">${escapeHtml(item.stage)}</div>
            </div>
          `,
        )
        .join("")
    : `<div class="tool-row"><div class="tool-name">本技能不调用工具</div><div class="tool-meta">仅使用已构建上下文和模型边界</div></div>`;
}

function renderModelPicker() {
  const picker = $("model-picker");
  const available = state.models.filter((model) => model.metadata?.available);
  picker.innerHTML = available.length
    ? available
        .map(
          (model) => `<option value="${escapeHtml(model.model_id)}">${escapeHtml(model.model_id)}</option>`,
        )
        .join("")
    : `<option value="">无可用模型</option>`;
  picker.value = available.some((model) => model.model_id === DEFAULT_MODEL_ID) ? DEFAULT_MODEL_ID : available[0]?.model_id || "";
  picker.disabled = !available.length;
  const wrap = picker.closest(".model-picker-wrap");
  if (wrap) wrap.classList.toggle("blocked", !available.length);
}

function renderHeader() {
  const session = currentSession();
  $("session-title").textContent = state.currentView === "wiki"
    ? "企业 Wiki"
    : session
      ? sessionTitle(session)
      : "Agent Workbench";
  if (state.currentView === "wiki") {
    $("session-meta").textContent = "按层级浏览公司资料、页面、事实和来源";
  } else if (session) {
    const metaParts = [...new Set([
      skillLabel(session.active_skill_id || ""),
      labels.modes[session.mode] || session.mode,
    ].filter(Boolean))];
    $("session-meta").textContent = metaParts.join(" / ");
  } else {
    $("session-meta").textContent = "默认对话读取 Memory 与 Wiki；输入 /report 生成可审计报告。";
  }

  const node = $("runtime-status");
  if (node) {
    const runtimeState = state.sending ? "running" : modelAvailable() ? "ready" : "blocked";
    const runtimeText = state.sending ? "运行中" : modelAvailable() ? "就绪" : "不可用";
    node.innerHTML = `
      <div class="runtime-status-pill ${runtimeState}">
        <span class="status-dot"></span>
        <span>${runtimeText}</span>
      </div>
    `;
  }
  const sendButton = $("send-message");
  if (sendButton) {
    sendButton.disabled = state.sending || !modelAvailable();
    sendButton.title = state.sending ? "正在执行" : modelAvailable() ? "发送" : "当前没有可用模型";
  }
  const promptInput = $("prompt-input");
  if (promptInput) promptInput.setAttribute("aria-busy", String(state.sending));
}

function renderChat(options = {}) {
  const stream = $("chat-stream");
  if (!state.messages.length) {
    stream.innerHTML = `
      <div class="empty-state">
        <div>
          <h2>从一个任务开始</h2>
          <div class="starter-list">
            <button data-prompt="/report 请基于当前 Wiki 画像，生成最近一周外部新闻、技术发布和行业动态对我的影响报告，要求包含来源、影响判断和行动建议。">默认影响报告</button>
            <button data-prompt="/report 请分析最近6个月 Agent Runtime、Agent Harness、Subagent 和 Tool Policy 相关变化对示例公司 AI 产品的影响，并生成带来源的 HTML 报告。">指定主题报告</button>
            <button data-prompt="最近有什么外部变化值得我关注？请先结合当前 Wiki 判断哪些和我有关。">先问影响方向</button>
            <button data-prompt="请基于企业 Wiki 说明示例产品和 iFinD 的产品边界，并指出哪些事实会影响报告判断。">查询 Wiki 画像</button>
          </div>
          ${renderErrorNotice()}
        </div>
      </div>
    `;
    stream.querySelectorAll("[data-prompt]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = $("prompt-input");
        setPromptText(button.dataset.prompt || "");
        input.focus();
      });
    });
    return;
  }

  const latestRunId = state.trace?.run_id || "";
  const visibleMessages = state.streaming
    ? [
      ...state.messages,
      {
        role: "user",
        content: state.streaming.userMessage,
        metadata: { streaming: true },
      },
      {
        role: "assistant",
        content: state.streaming.content || "正在生成...",
        metadata: {
          run_id: state.streaming.runId || "",
          artifacts: state.streaming.artifacts || {},
          streaming: true,
        },
      },
    ]
    : state.messages;
  stream.innerHTML = `
    ${renderErrorNotice()}
    ${visibleMessages
      .map((message) => {
        const runId = message.metadata?.run_id || "";
        const cachedTrace = runId === latestRunId ? state.trace : state.tracesByRun[runId];
        const showTrace = message.role === "assistant" && runId && cachedTrace;
        const showStreamingTrace = message.role === "assistant" && message.metadata?.streaming && !cachedTrace;
        return `
          ${showTrace ? renderThinking(cachedTrace) : ""}
          ${showStreamingTrace ? renderStreamingThinking(state.streaming) : ""}
          <div class="message-block">
            <div class="message ${message.role === "assistant" ? "assistant" : "user"}">
              <div class="avatar">${message.role === "assistant" ? "A" : "你"}</div>
              <div class="bubble ${message.metadata?.streaming ? "streaming-bubble" : ""}">
                ${formatMessage(message.content, message.metadata?.artifacts || {})}
                ${renderMessageArtifacts(message.metadata?.artifacts || {})}
                ${message.role === "assistant" && runId && !cachedTrace
                  ? `<button class="message-trace-trigger" type="button" data-run-trace="${escapeHtml(runId)}" title="查看这一轮的完整运行过程">运行记录</button>`
                  : ""}
              </div>
            </div>
          </div>
        `;
      })
      .join("")}
  `;
  stream.querySelectorAll("[data-run-trace]").forEach((button) => {
    button.addEventListener("click", () => loadHistoricalTrace(button.dataset.runTrace, button));
  });
  const thinkingCards = stream.querySelectorAll(".thinking-card");
  const requestedThinking = options.scrollRunId
    ? stream.querySelector(`.thinking-card[data-run-id="${options.scrollRunId}"]`)
    : null;
  const targetThinking = requestedThinking || thinkingCards[thinkingCards.length - 1];
  if (targetThinking) {
    const streamTop = stream.getBoundingClientRect().top;
    const cardTop = targetThinking.getBoundingClientRect().top;
    stream.scrollTop = Math.max(0, stream.scrollTop + cardTop - streamTop - 12);
  } else {
    stream.scrollTop = stream.scrollHeight;
  }
}

function renderWorkspace(options = {}) {
  const chatActive = state.currentView !== "wiki";
  $("chat-stream").classList.toggle("hidden", !chatActive);
  $("wiki-view").classList.toggle("hidden", chatActive);
  $("composer").classList.toggle("hidden", !chatActive);
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.currentView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (chatActive) renderChat(options);
  else renderWiki();
}

function renderWiki() {
  const view = $("wiki-view");
  const wiki = state.wiki || {};
  const pages = Array.isArray(wiki.pages) ? wiki.pages : [];
  const stats = wiki.stats || {};
  if (!pages.length) {
    view.innerHTML = `
      <div class="wiki-empty">
        <h2>暂无 Wiki 数据</h2>
        <p>${escapeHtml(wiki.error || "当前企业空间还没有可展示的知识库页面。")}</p>
      </div>
    `;
    return;
  }
  if (!state.selectedWikiPath || !pages.some((page) => page.path === state.selectedWikiPath)) {
    state.selectedWikiPath = pages[0].path;
  }
  const selected = pages.find((page) => page.path === state.selectedWikiPath) || pages[0];
  const facts = Array.isArray(selected.facts) ? selected.facts : [];
  const profile = wiki.profile || {};
  view.innerHTML = `
    <div class="wiki-shell">
      <section class="wiki-index" aria-label="Wiki 目录">
        <div class="wiki-index-head">
          <span>Company Wiki</span>
          <strong>${escapeHtml(String(stats.page_count || pages.length))} 页</strong>
        </div>
        <div class="wiki-company">
          <strong>${escapeHtml(profile.company_name || profile.short_name || wiki.company_id || state.companyId)}</strong>
          <span>${escapeHtml([profile.stock_code, profile.industry].filter(Boolean).join(" · ") || "本地企业资料")}</span>
        </div>
        <div class="wiki-page-list">
          ${pages
            .map((page) => {
              const active = page.path === selected.path ? "active" : "";
              const label = page.parts?.length ? page.parts.join(" / ") : page.path;
              return `
                <button class="wiki-page-item ${active}" data-wiki-path="${escapeHtml(page.path)}">
                  <span>${escapeHtml(page.title || label)}</span>
                  <small>${escapeHtml(label)} · ${escapeHtml(String(page.fact_count || 0))} facts</small>
                </button>
              `;
            })
            .join("")}
        </div>
      </section>
      <section class="wiki-detail" aria-label="Wiki 页面详情">
        <div class="wiki-detail-head">
          <div>
            <div class="wiki-kicker">${escapeHtml(selected.path)}</div>
            <h2>${escapeHtml(selected.title || selected.path)}</h2>
          </div>
          <div class="wiki-head-actions">
            <div class="wiki-stat-row">
              <span>${escapeHtml(String(stats.fact_count || wiki.facts?.length || 0))} facts</span>
              <span>${escapeHtml(String(stats.high_importance_fact_count || 0))} high</span>
              <span>${escapeHtml(String(facts.length))} visible</span>
            </div>
            <div class="wiki-action-row">
              <button id="wiki-refresh" class="wiki-secondary-button" type="button" ${state.wikiAction ? "disabled" : ""}>${state.wikiAction === "refresh" ? "读取中" : "重新读取"}</button>
              <button id="wiki-save" class="wiki-primary-button" type="button" ${state.wikiAction ? "disabled" : ""}>
                ${state.wikiAction === "save" ? "保存中" : "保存页面"}
              </button>
            </div>
          </div>
        </div>
        <div class="wiki-meta-grid">
          ${renderWikiMeta("领域", selected.domain)}
          ${renderWikiMeta("负责人", selected.owner)}
          ${renderWikiMeta("新鲜度", selected.freshness)}
          ${renderWikiMeta("更新", selected.last_updated)}
          ${renderWikiMeta("来源", selected.source)}
        </div>
        <div class="wiki-guidance-panel">
          <div class="wiki-section-title">这页应该维护什么</div>
          <p>${escapeHtml(wikiPageGuidance(selected))}</p>
        </div>
        <div class="wiki-structured-editor">
          <div class="wiki-editor-head">
            <div>
              <div class="wiki-section-title">页面说明</div>
              <p>写这页的稳定背景，不要在这里重复 fact。报告会优先使用下面的结构化事实。</p>
            </div>
          </div>
          <textarea id="wiki-intro-editor" class="wiki-intro-editor" spellcheck="false" ${state.wikiAction ? "disabled" : ""}>${escapeHtml(extractWikiIntro(selected.body || "", selected.title || selected.path))}</textarea>
          <div class="wiki-editor-head">
            <div>
              <div class="wiki-section-title">结构化事实</div>
              <p>每张卡片会写成一个 FACT 块，避免用户把同一个事实散落到多个段落里。</p>
            </div>
            <button id="wiki-add-fact" class="wiki-secondary-button" type="button" ${state.wikiAction ? "disabled" : ""}>添加事实</button>
          </div>
          <div id="wiki-fact-form-list" class="wiki-fact-form-list">
            ${facts.length
              ? facts.map((fact, index) => renderWikiFactEditor(fact, index)).join("")
              : renderWikiFactEditor(defaultWikiFact(selected), 0)}
          </div>
          <datalist id="wiki-source-options">
            <option value="user_confirmed"></option>
            <option value="internal_wiki"></option>
            <option value="public_disclosure"></option>
            <option value="project_design"></option>
            <option value="analysis_inference_from_disclosed_business"></option>
          </datalist>
          <details class="wiki-raw-markdown">
            <summary>查看原始 Markdown 契约</summary>
            <pre>${escapeHtml(selected.body || "")}</pre>
          </details>
        </div>
      </section>
    </div>
  `;
  view.querySelectorAll("[data-wiki-path]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedWikiPath = button.dataset.wikiPath || "";
      renderWiki();
    });
  });
  $("wiki-save")?.addEventListener("click", saveSelectedWikiPage);
  $("wiki-refresh")?.addEventListener("click", refreshWiki);
  $("wiki-add-fact")?.addEventListener("click", addWikiFactCard);
  bindWikiFactEditorActions();
}

function renderWikiMeta(label, value) {
  return `
    <div class="wiki-meta-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "未记录")}</strong>
    </div>
  `;
}

function wikiPageGuidance(page) {
  const domain = String(page?.domain || page?.path || "").toLowerCase();
  const guidance = [
    {
      match: ["profile", "00_profile"],
      text: "维护企业身份、地域、行业和报告默认关注方向。这些事实决定系统默认报告会把哪些外部变化判断为相关。",
    },
    {
      match: ["business", "01_business"],
      text: "维护核心产品、客户类型、收入来源和近期目标。这些事实决定报告里的影响链条和行动建议是否贴近业务。",
    },
    {
      match: ["operations", "02_operations"],
      text: "维护人员、研发、数据、供应链和上线约束。这些事实用于判断政策/技术变化是否会影响交付成本和合规边界。",
    },
    {
      match: ["qualification", "03_qualifications"],
      text: "维护资质、证书、专利、软著和有效期。这些事实用于判断政策适用性和申报机会。",
    },
    {
      match: ["policy_history", "04_policy_history"],
      text: "维护历史申报、获批、失败原因和主管部门沟通记录。这些事实用于避免重复建议和复用历史决策。",
    },
    {
      match: ["preference", "08_preferences"],
      text: "维护报告风格、风险偏好、人工确认边界和固定口径。这些事实用于控制报告输出方式。",
    },
  ];
  const hit = guidance.find((item) => item.match.some((term) => domain.includes(term)));
  return hit?.text || "维护会长期影响报告判断的稳定事实。临时观点和一次性讨论建议放在对话里，不要写进 Wiki。";
}

function extractWikiIntro(body, fallbackTitle = "") {
  const text = String(body || "");
  const index = text.search(/^## FACT:/m);
  const intro = (index >= 0 ? text.slice(0, index) : text).trim();
  if (intro) return intro;
  const title = String(fallbackTitle || "").trim();
  return title ? `# ${title}\n\n请补充这页的稳定背景。` : "请补充这页的稳定背景。";
}

function defaultWikiFact(page) {
  const slug = String(page?.domain || page?.path || "fact")
    .toLowerCase()
    .replace(/^wiki\//, "")
    .replace(/\.md$/, "")
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return {
    fact_id: `${slug || "fact"}.new_fact`,
    value: "",
    importance: Number(page?.importance || 3),
    confidence: 0.7,
    source: "user_confirmed",
    policy_relevance: [],
    text: "",
  };
}

function renderWikiFactEditor(fact, index) {
  const relevance = Array.isArray(fact.policy_relevance)
    ? fact.policy_relevance.join(", ")
    : String(fact.policy_relevance || "");
  const factId = fact.fact_id || `fact.${index + 1}`;
  return `
    <article class="wiki-fact-editor" data-fact-index="${escapeHtml(String(index))}">
      <div class="wiki-fact-editor-top">
        <label>
          <span>事实 ID</span>
          <input class="wiki-fact-id-input" value="${escapeHtml(factId)}" placeholder="business.core_product" spellcheck="false" />
        </label>
        <label>
          <span>重要性</span>
          <select class="wiki-fact-importance-input">
            ${[5, 4, 3, 2, 1]
              .map((value) => `<option value="${value}" ${Number(fact.importance ?? 3) === value ? "selected" : ""}>${value}</option>`)
              .join("")}
          </select>
        </label>
        <label>
          <span>置信度</span>
          <input class="wiki-fact-confidence-input" type="number" min="0" max="1" step="0.05" value="${escapeHtml(String(fact.confidence ?? 0.7))}" />
        </label>
      </div>
      <label class="wiki-field-block">
        <span>事实内容</span>
        <textarea class="wiki-fact-value-input" rows="2" placeholder="这条事实会被报告和 RAG 检索直接引用。">${escapeHtml(fact.value || fact.text || "")}</textarea>
      </label>
      <div class="wiki-fact-editor-grid">
        <label>
          <span>来源</span>
          <input class="wiki-fact-source-input" value="${escapeHtml(fact.source || "user_confirmed")}" list="wiki-source-options" placeholder="user_confirmed" spellcheck="false" />
        </label>
        <label>
          <span>关联标签</span>
          <input class="wiki-fact-relevance-input" value="${escapeHtml(relevance)}" placeholder="agent, finance_ai, compliance" spellcheck="false" />
        </label>
      </div>
      <label class="wiki-field-block">
        <span>补充说明</span>
        <textarea class="wiki-fact-note-input" rows="2" placeholder="可选。记录口径、边界或需要人工确认的信息。">${escapeHtml(fact.text && fact.text !== fact.value ? fact.text : "")}</textarea>
      </label>
      <div class="wiki-fact-editor-actions">
        <button class="wiki-remove-fact" type="button">删除事实</button>
      </div>
    </article>
  `;
}

function addWikiFactCard() {
  const list = $("wiki-fact-form-list");
  if (!list) return;
  const selected = (state.wiki?.pages || []).find((page) => page.path === state.selectedWikiPath) || {};
  const index = list.querySelectorAll(".wiki-fact-editor").length;
  list.insertAdjacentHTML("beforeend", renderWikiFactEditor(defaultWikiFact(selected), index));
  const card = list.lastElementChild;
  card?.querySelector(".wiki-fact-id-input")?.focus();
  bindWikiFactEditorActions();
}

function bindWikiFactEditorActions() {
  document.querySelectorAll(".wiki-remove-fact").forEach((button) => {
    button.onclick = () => {
      const list = $("wiki-fact-form-list");
      const cards = list?.querySelectorAll(".wiki-fact-editor") || [];
      if (cards.length <= 1) {
        toast("至少保留一个事实卡片");
        return;
      }
      button.closest(".wiki-fact-editor")?.remove();
    };
  });
}

function parseWikiTags(value) {
  return String(value || "")
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function wikiInlineValue(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function renderFactMarkdown(fact) {
  const tags = fact.policy_relevance.length
    ? `[${fact.policy_relevance.map((item) => JSON.stringify(wikiInlineValue(item))).join(", ")}]`
    : "[]";
  const note = fact.note ? `\n\n${fact.note}` : "";
  return [
    `## FACT: ${fact.fact_id}`,
    `- value: ${wikiInlineValue(fact.value)}`,
    `- importance: ${fact.importance}`,
    `- confidence: ${fact.confidence}`,
    `- source: ${wikiInlineValue(fact.source)}`,
    `- policy_relevance: ${tags}`,
    note,
  ].join("\n");
}

function buildStructuredWikiBody() {
  const intro = ($("wiki-intro-editor")?.value || "").trim();
  const facts = Array.from(document.querySelectorAll(".wiki-fact-editor")).map((card) => ({
    fact_id: card.querySelector(".wiki-fact-id-input")?.value.trim() || "",
    value: card.querySelector(".wiki-fact-value-input")?.value.trim() || "",
    importance: Number(card.querySelector(".wiki-fact-importance-input")?.value || 3),
    confidence: Number(card.querySelector(".wiki-fact-confidence-input")?.value || 0.7),
    source: card.querySelector(".wiki-fact-source-input")?.value.trim() || "user_confirmed",
    policy_relevance: parseWikiTags(card.querySelector(".wiki-fact-relevance-input")?.value || ""),
    note: card.querySelector(".wiki-fact-note-input")?.value.trim() || "",
  }));
  const seen = new Set();
  for (const fact of facts) {
    if (!fact.fact_id) throw new Error("存在空的事实 ID");
    if (!/^[A-Za-z0-9_.-]+$/.test(fact.fact_id)) {
      throw new Error(`事实 ID 只能包含字母、数字、点、下划线和短横线：${fact.fact_id}`);
    }
    if (seen.has(fact.fact_id)) throw new Error(`事实 ID 重复：${fact.fact_id}`);
    seen.add(fact.fact_id);
    if (!fact.value) throw new Error(`事实 ${fact.fact_id} 缺少事实内容`);
    fact.importance = Math.max(1, Math.min(5, Math.round(fact.importance || 3)));
    fact.confidence = Math.max(0, Math.min(1, Number(fact.confidence || 0)));
  }
  return [intro, ...facts.map(renderFactMarkdown)].filter(Boolean).join("\n\n");
}

function renderWikiFact(fact) {
  const relevance = Array.isArray(fact.policy_relevance) ? fact.policy_relevance.join(" / ") : "";
  return `
    <article class="wiki-fact">
      <div class="wiki-fact-top">
        <strong>${escapeHtml(fact.fact_id || "fact")}</strong>
        <span>${escapeHtml(fact.source_kind || "source")}</span>
      </div>
      <p>${escapeHtml(fact.value || fact.text || "未记录事实内容")}</p>
      <div class="wiki-fact-meta">
        <span>importance ${escapeHtml(String(fact.importance ?? "n/a"))}</span>
        <span>confidence ${escapeHtml(String(fact.confidence ?? "n/a"))}</span>
        ${relevance ? `<span>${escapeHtml(relevance)}</span>` : ""}
      </div>
    </article>
  `;
}

function renderErrorNotice() {
  if (!state.lastError) return "";
  return `
    <div class="error-notice">
      <strong>本轮未执行</strong>
      <span>${escapeHtml(state.lastError)}</span>
    </div>
  `;
}

function formatMessage(content, artifacts = {}) {
  if (isBrokenText(content)) {
    return '<div class="message-line muted">历史消息编码异常，原文不可恢复。</div>';
  }
  const hasArtifacts = Object.values(artifacts || {}).some(Boolean);
  const artifactLine = /^(Markdown 报告|HTML 报告|RunArtifact|运行产物|结构化产物)[：:]/i;
  const visibleContent = redactLocalPaths(
    hasArtifacts
      ? String(content || "")
        .split(/\r?\n/)
        .filter((line) => !artifactLine.test(line.trim()))
        .join("\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim()
      : content,
  );
  return String(visibleContent || "")
    .split(/\r?\n/)
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed) return `<div class="message-spacer"></div>`;
      const inline = formatInlineMarkdown(trimmed.replace(/^[-*]\s+/, "").replace(/^\d+\.\s+/, ""));
      if (/^[-*]\s+/.test(trimmed)) return `<div class="message-list-item"><span>•</span><div>${inline}</div></div>`;
      const numbered = trimmed.match(/^(\d+)\.\s+/);
      if (numbered) return `<div class="message-list-item"><span>${escapeHtml(numbered[1])}.</span><div>${inline}</div></div>`;
      return `<div class="message-line">${inline}</div>`;
    })
    .join("");
}

function formatInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(
      /(https?:\/\/[^\s<>&quot;，。；）)]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">查看原文</a>',
    );
}

function renderMessageArtifacts(artifacts) {
  const entries = Object.entries(artifacts || {}).filter(([, value]) => value);
  if (!entries.length) return "";
  return `
    <div class="message-artifacts">
      ${entries
        .map(
          ([key, value]) => `
            <a class="artifact-card" href="${escapeHtml(artifactHref(value))}" target="_blank" rel="noopener" title="${escapeHtml(artifactDisplayValue(value))}">
              <span>${escapeHtml(artifactLabel(key))}</span>
              <strong>${escapeHtml(artifactActionLabel(key))}</strong>
            </a>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderThinking(trace) {
  const steps = trace?.steps || [];
  if (!steps.length) return "";
  const summary = trace.trace_summary || {};
  const completed = summary.completed_steps ?? steps.filter((step) => step.status === "done").length;
  const toolCalls = steps.flatMap((step) => step.tool_calls || []);
  const gates = steps.filter((step) => step.gate_result && Object.keys(step.gate_result).length);
  const artifacts = trace.artifacts || [];
  const phases = ["think", "act", "observe", "answer"].map((phase) => ({
    phase,
    count: summary.loop_phases?.[phase] ?? steps.filter((step) => step.metadata?.loop_phase === phase).length,
  }));
  const toolCallCount = summary.tool_call_count ?? toolCalls.length;
  const gateCount = summary.gate_count ?? gates.length;
  const artifactCount = summary.artifact_count ?? artifacts.length;
  const selectedModel = summary.selected_model_id || summary.model_id || trace.model_id || DEFAULT_MODEL_ID;
  const executionLabel = summary.model_used === false
    ? `${selectedModel} 已选择 · 确定性执行（0 次模型调用）`
    : selectedModel;
  const openAttribute = ["completed", "done", "success", "ok"].includes(trace.status) ? "" : " open";
  return `
    <details class="thinking-card" data-run-id="${escapeHtml(trace.run_id || "")}"${openAttribute}>
      <summary class="thinking-head">
        <div>
          <div class="thinking-title">运行过程</div>
          <div class="thinking-subtitle">Run ${escapeHtml(shortId(trace.run_id))} · ${escapeHtml(skillLabel(trace.skill_id))} · ${escapeHtml(executionLabel)}</div>
        </div>
        <div class="thinking-status">
          <span class="trace-chip">流程${escapeHtml(statusLabel(trace.status))}</span>
          <span class="trace-chip">${completed}/${steps.length} 步已记录</span>
        </div>
      </summary>
      <div class="thinking-body">
        <div class="trace-metrics">
          <div class="trace-metric"><span>步骤</span><strong>${steps.length}</strong></div>
          <div class="trace-metric"><span>工具</span><strong>${toolCallCount}</strong></div>
          <div class="trace-metric"><span>门控</span><strong>${gateCount}</strong></div>
          <div class="trace-metric"><span>输出产物</span><strong>${artifactCount}</strong></div>
        </div>
        ${renderTraceEvidence(trace)}
        ${renderSubagentPanel(trace)}
        <div class="loop-phase-row">
          ${phases
            .map(
              (item) => `
                <span class="loop-phase ${item.count ? "active" : ""}">
                  ${escapeHtml(loopPhaseLabel(item.phase))}
                  <strong>${item.count}</strong>
                </span>
              `,
            )
            .join("")}
        </div>
        <div class="step-list">
          ${steps.map((step) => renderStep(step)).join("")}
        </div>
      </div>
    </details>
  `;
}

function renderStreamingThinking(streaming) {
  const status = streaming?.status || "模型与工具链路运行中";
  const runId = streaming?.runId || "";
  const started = streaming?.startedAt ? formatTime(streaming.startedAt) : "";
  return `
    <details class="thinking-card streaming-thinking" open>
      <summary class="thinking-head">
        <div>
          <div class="thinking-title">运行过程</div>
          <div class="thinking-subtitle">${escapeHtml(runId ? `Run ${shortId(runId)}` : "等待 Runtime 分配 Run")} · ${escapeHtml(started || "刚刚")}</div>
        </div>
        <div class="thinking-status">
          <span class="trace-chip">SSE 流式输出</span>
          <span class="trace-chip">${escapeHtml(status)}</span>
        </div>
      </summary>
      <div class="thinking-body">
        <div class="stream-progress">
          <span></span>
        </div>
        <div class="stream-hint">完整 Trace 会在本轮结束后自动展开，包括 Context、Memory、Tool、Gate、Subagent 和 Artifact。</div>
      </div>
    </details>
  `;
}

function flattenSubagentOutput(output) {
  if (!output || typeof output !== "object") return [];
  const rows = [];
  Object.entries(output).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.slice(0, 4).forEach((item, index) => {
        rows.push({
          label: `${key}[${index + 1}]`,
          value: typeof item === "object" ? JSON.stringify(item) : String(item),
        });
      });
    } else if (value && typeof value === "object") {
      rows.push({ label: key, value: JSON.stringify(value) });
    } else if (value !== undefined && value !== null && value !== "") {
      rows.push({ label: key, value: String(value) });
    }
  });
  return rows.slice(0, 8);
}

function renderSubagentPanel(trace) {
  const summary = trace?.subagent_summary || {};
  const tasks = Array.isArray(summary.tasks) ? summary.tasks : [];
  const results = Array.isArray(summary.results) ? summary.results : [];
  if (!tasks.length && !results.length) return "";
  const taskByRole = new Map(tasks.map((task) => [task.role, task]));
  const cards = results.length
    ? results.map((result) => ({ ...(taskByRole.get(result.role) || {}), ...result }))
    : tasks;
  return `
    <section class="subagent-panel" aria-label="子智能体委派">
      <div class="subagent-panel-head">
        <span>动态子智能体</span>
        <strong>${escapeHtml(String(summary.delegate_count || results.length || 0))} 次委派</strong>
      </div>
      <div class="subagent-card-grid">
        ${cards
          .map((item) => {
            const outputRows = flattenSubagentOutput(item.output || {});
            return `
              <article class="subagent-card">
                <div class="subagent-card-top">
                  <strong>${escapeHtml(item.role || "subagent")}</strong>
                  <span>${escapeHtml(item.status || item.trigger || "planned")}</span>
                </div>
                <div class="subagent-reason">${escapeHtml(item.reason || "由主 Agent 根据本轮问题动态委派")}</div>
                ${item.trigger ? `<div class="subagent-trigger">触发信号：${escapeHtml(item.trigger)}</div>` : ""}
                ${item.output_schema ? `<div class="subagent-schema">输出契约：${escapeHtml(item.output_schema)}</div>` : ""}
                ${outputRows.length
                  ? `<div class="subagent-output">
                      ${outputRows
                        .map(
                          (row) => `
                            <div class="subagent-output-row">
                              <span>${escapeHtml(row.label)}</span>
                              <p>${escapeHtml(row.value)}</p>
                            </div>
                          `,
                        )
                        .join("")}
                    </div>`
                  : `<div class="subagent-empty">本角色只完成规划或未产生可展示输出。</div>`}
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function sourceSummary(trace) {
  const source = trace.source_summary || {};
  const selected = Array.isArray(source.selected_source_types) ? source.selected_source_types : [];
  const route = source.route_label || source.route || "";
  const snapshotInfo = [
    source.snapshot_news_count ? `新闻快照 ${source.snapshot_news_count}` : "",
    source.snapshot_policy_count ? `政策快照 ${source.snapshot_policy_count}` : "",
  ]
    .filter(Boolean)
    .join(" / ");
  const counts = [
    source.news_source_count ? `新闻 ${source.enabled_news_source_count || 0}/${source.news_source_count || 0}` : "",
    source.policy_source_count ? `政策 ${source.enabled_policy_source_count || 0}/${source.policy_source_count || 0}` : "",
    source.fallback_source_count ? `兜底 ${source.fallback_source_count}` : "",
  ]
    .filter(Boolean)
    .join(" / ");
  if (!route && !selected.length && !counts && !snapshotInfo) return "";
  return {
    value: route || selected.slice(0, 3).join(" / ") || "已自动选择",
    detail: [counts, snapshotInfo].filter(Boolean).join(" · "),
  };
}

function renderTraceEvidence(trace) {
  const context = trace.context_summary || {};
  const memory = trace.memory_summary || {};
  const model = trace.model_summary || {};
  const source = trace.source_summary || {};
  const subagents = trace.subagent_summary || {};
  const tools = trace.tool_summary || {};
  const gates = trace.gate_summary || {};
  const artifacts = trace.artifact_summary || {};
  const structuredEvents = trace.structured_events || [];
  const sourceCell = sourceSummary(trace);
  const budgetUtilization = Number(context.budget_utilization || 0);
  const budgetDetail = context.token_estimate_total
    ? `估算 ${context.token_estimate_total} tokens${budgetUtilization ? ` · 已用预算 ${Math.round(budgetUtilization * 100)}%` : ""}`
    : context.stage_name || "";
  const cells = [
    {
      label: "上下文",
      value: context.manifest_id ? `${context.visible_key_count || 0} 可见块 / ${context.hidden_field_count || 0} 隐藏块` : "未生成",
      detail: budgetDetail,
    },
    {
      label: "记忆",
      value: `${memory.read_steps || 0} 读 / ${memory.actual_write_count || 0} 写`,
      detail: `${memory.total_memory_hits || 0} 命中，过滤 ${memory.total_filtered_out || 0}${memory.deduplicated_write_count ? `，去重 ${memory.deduplicated_write_count}` : ""}`,
    },
    {
      label: "模型",
      value: `${model.call_count || 0} 次调用`,
      detail: model.call_count
          ? [
            model.prompt_contract_ids?.length ? `contract ${model.prompt_contract_ids.join(", ")}` : DEFAULT_MODEL_ID,
            model.total_attempts ? `${model.total_attempts} 次尝试` : "",
            model.retried_call_count ? `${model.retried_call_count} 个调用发生重试` : "",
            model.total_latency_ms ? `${Math.round(model.total_latency_ms)} ms` : "",
          ].filter(Boolean).join(" · ")
        : "确定性工具链，本轮未调用模型",
    },
    {
      label: "子智能体",
      value: `${subagents.delegate_count || 0} 次委派`,
      detail: (subagents.roles || []).join(" / ") || "本轮未触发动态委派",
    },
    {
      label: "数据源",
      value: sourceCell?.value || "未记录",
      detail: sourceCell?.detail || [source.route, source.route_label].filter(Boolean).join(" / ") || "未进入外部变化分析路线",
    },
    {
      label: "工具",
      value: `${tools.total_calls || 0} 次调用`,
      detail: (tools.unique_tools || []).slice(0, 3).join(" / ") || "无工具调用",
    },
    {
      label: "门控",
      value: `${gates.total_gates || 0} 个门控`,
      detail: gates.decisions ? Object.entries(gates.decisions).map(([key, value]) => `${key}:${value}`).join(" / ") : "",
    },
    {
      label: "输出产物",
      value: `${artifacts.total_output_artifacts || artifacts.total_artifacts || 0} 个产物`,
      detail: artifacts.by_type ? Object.keys(artifacts.by_type).slice(0, 3).join(" / ") : "",
    },
    {
      label: "可观测",
      value: `${structuredEvents.length || 0} events`,
      detail: trace.observability_schema_version || "workbench.trace",
    },
  ];
  return `
    <div class="trace-evidence-grid">
      ${cells
        .map(
          (cell) => `
            <div class="trace-evidence-cell">
              <span>${escapeHtml(cell.label)}</span>
              <strong>${escapeHtml(cell.value)}</strong>
              <small>${escapeHtml(cell.detail || "")}</small>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function contextSummary(step) {
  const manifest = step.output_payload?.context_manifest || step.input_payload?.context_manifest || {};
  const visibleKeys = manifest.visible_keys || [];
  const estimates = manifest.token_estimates || {};
  const budget = manifest.token_budget || step.metadata?.token_budget || {};
  if (!Object.keys(manifest).length && !Object.keys(budget).length) return "";
  const visible = Array.isArray(visibleKeys) ? visibleKeys.length : 0;
  const estimateTotal = sumNumericValues(estimates);
  const budgetTotal = sumNumericValues(budget);
  return [`${visible} 个可见块`, estimateTotal ? `估算 ${estimateTotal} tokens` : "", budgetTotal ? `预算 ${budgetTotal}` : ""]
    .filter(Boolean)
    .join(" · ");
}

function toolSummary(step) {
  const calls = step.tool_calls || [];
  if (!calls.length) return "";
  const allowed = calls.filter((call) => call.allowed !== false).length;
  const successfulStatuses = new Set(["ok", "success", "done", "allowed"]);
  const failed = calls.filter((call) => call.status && !successfulStatuses.has(String(call.status).toLowerCase())).length;
  const names = uniqueNames(calls, "tool_name");
  return `${calls.length} 次工具 · ${allowed} 允许${failed ? ` · ${failed} 异常` : ""}${names ? ` · ${names}` : ""}`;
}

function gateSummary(step) {
  const gate = step.gate_result || {};
  const gateCount = step.output_payload?.gate_count || gate.stage_gate_count || 0;
  if (gate.decision) return `${gate.decision}${gateCount ? ` · ${gateCount} 个 gate` : ""}`;
  if (gateCount) return `${gateCount} 个 gate`;
  return "";
}

function artifactSummary(step) {
  const artifacts = step.output_payload?.artifacts || {};
  const artifactKeys = step.output_payload?.artifact_keys || Object.keys(artifacts);
  if (!artifactKeys?.length) return "";
  return artifactKeys.map((key) => artifactLabel(key)).join(" / ");
}

function memorySummary(step) {
  if (step.step_type !== "memory_read" && step.step_type !== "memory_write") return "";
  const output = step.output_payload || {};
  if (step.step_type === "memory_read") {
    const count = Number(output.memory_count ?? output.count ?? 0);
    const raw = Number(output.raw_memory_count ?? 0);
    const filtered = Number(output.filtered_out ?? 0);
    return [`读取 ${count} 条`, raw ? `候选 ${raw} 条` : "", filtered ? `过滤 ${filtered} 条` : ""].filter(Boolean).join(" · ");
  }
  if (output.deduplicated) return `已存在 ${shortId(output.memory_id)} · 未重复写入`;
  return output.memory_id ? `写入 ${shortId(output.memory_id)}` : "写入长期记忆";
}

function modelCallSummary(step) {
  if (step.step_type !== "model_call") return "";
  const input = step.input_payload || {};
  const output = step.output_payload || {};
  return [
    input.schema_name ? `schema ${input.schema_name}` : "",
    output.response_id ? `response ${shortId(output.response_id)}` : "",
    input.prompt_contract?.contract_id ? `contract ${input.prompt_contract.contract_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function routeSummary(step) {
  if (step.step_type === "intent_classification") {
    const output = step.output_payload || {};
    return output.resolved_skill_id || output.route || output.mode || "";
  }
  if (step.step_type === "skill_selection") {
    const output = step.output_payload || {};
    return output.selected_skill_id || output.skill_id || output.skill?.id || "";
  }
  if (step.step_type === "final_answer") {
    const output = step.output_payload || {};
    const artifacts = output.artifact_keys || [];
    return artifacts.length ? `最终回答 · ${artifacts.length} 个产物` : "最终回答已保存";
  }
  return "";
}

function observationSummary(step) {
  const candidates = [
    routeSummary(step) ? { label: "路由", value: routeSummary(step) } : null,
    contextSummary(step) ? { label: "上下文", value: contextSummary(step) } : null,
    memorySummary(step) ? { label: "记忆", value: memorySummary(step) } : null,
    modelCallSummary(step) ? { label: "模型", value: modelCallSummary(step) } : null,
    toolSummary(step) ? { label: "工具", value: toolSummary(step) } : null,
    gateSummary(step) ? { label: "门控", value: gateSummary(step) } : null,
    artifactSummary(step) ? { label: "产物", value: artifactSummary(step) } : null,
  ].filter(Boolean);
  if (candidates.length) return candidates;
  return [{ label: "控制", value: "确定性运行步骤，无额外工具或门控" }];
}

function renderStep(step) {
  const phase = step.metadata?.loop_phase || "";
  const payload = {
    input: step.input_payload || {},
    output: step.output_payload || {},
    tool_calls: step.tool_calls || [],
    gate_result: step.gate_result || {},
    metadata: step.metadata || {},
  };
  return `
    <div class="step-item ${escapeHtml(step.status || "")}">
      <div class="step-dot">${escapeHtml(step.step_index)}</div>
      <div class="step-main">
        <div class="step-topline">
          <div>
            <div class="step-title">${escapeHtml(step.title || stepTypeLabel(step.step_type))}</div>
            <div class="step-meta">${escapeHtml(statusLabel(step.status))} · ${escapeHtml(formatTime(step.created_at))}</div>
          </div>
          <div class="step-tags">
            <span class="step-type">${escapeHtml(stepTypeLabel(step.step_type))}</span>
            ${phase ? `<span class="phase-pill">${escapeHtml(loopPhaseLabel(phase))}</span>` : ""}
          </div>
        </div>
        <div class="step-summary-row">
          ${observationSummary(step)
            .map((item) => `<span class="step-summary"><b>${escapeHtml(item.label)}</b>${escapeHtml(item.value)}</span>`)
            .join("")}
        </div>
        <details class="step-details">
          <summary>展开运行证据</summary>
          <div class="detail-grid">
            <div class="detail-panel">
              <div class="detail-title">输入</div>
              ${renderCompactDict(step.input_payload || {}, "无输入字段")}
            </div>
            <div class="detail-panel">
              <div class="detail-title">输出</div>
              ${renderCompactDict(step.output_payload || {}, "无输出字段")}
            </div>
            <div class="detail-panel">
              <div class="detail-title">工具调用</div>
              ${renderCompactDict(
                {
                  count: (step.tool_calls || []).length,
                  tools: uniqueNames(step.tool_calls || [], "tool_name", 6),
                  status: uniqueNames(step.tool_calls || [], "status", 6),
                },
                "无工具调用",
              )}
            </div>
            <div class="detail-panel">
              <div class="detail-title">门控</div>
              ${renderCompactDict(step.gate_result || {}, "无门控结果")}
            </div>
          </div>
          <details class="raw-details">
            <summary>原始 JSON</summary>
            <pre class="json-box">${escapeHtml(JSON.stringify(redactTraceValue(payload), null, 2))}</pre>
          </details>
        </details>
      </div>
    </div>
  `;
}

function renderSlashPalette() {
  const input = $("prompt-input");
  const raw = getPromptText();
  const range = slashQueryRange(raw, getCaretOffset(input));
  const query = range?.query || "";
  if (!range || !query.startsWith("/")) {
    $("slash-palette").classList.add("hidden");
    $("slash-palette").innerHTML = "";
    state.slashOpen = false;
    state.slashMatches = [];
    state.slashIndex = 0;
    state.slashQuery = "";
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    return;
  }
  const matches = slashCommands.filter((item) => item.command.startsWith(query || "/"));
  if (query !== state.slashQuery) state.slashIndex = 0;
  state.slashQuery = query;
  state.slashMatches = matches;
  state.slashIndex = Math.max(0, Math.min(state.slashIndex, Math.max(0, matches.length - 1)));
  $("slash-palette").innerHTML = matches
    .map(
      (item, index) => `
        <button id="slash-option-${index}" class="slash-option ${index === state.slashIndex ? "active" : ""}" data-command="${escapeHtml(item.command)}" role="option" aria-selected="${index === state.slashIndex}" tabindex="-1">
          <span class="slash-key">${escapeHtml(item.command)}</span>
          <span>
            <span class="slash-title">${escapeHtml(item.name)}</span>
            <span class="slash-desc">${escapeHtml(item.description)}</span>
          </span>
          <span class="small-muted">${escapeHtml(skillLabel(item.skillId))}</span>
        </button>
      `,
    )
    .join("");
  $("slash-palette").classList.toggle("hidden", !matches.length);
  state.slashOpen = Boolean(matches.length);
  input.setAttribute("aria-expanded", String(state.slashOpen));
  if (state.slashOpen) input.setAttribute("aria-activedescendant", `slash-option-${state.slashIndex}`);
  else input.removeAttribute("aria-activedescendant");
  document.querySelectorAll(".slash-option").forEach((button) => {
    button.addEventListener("click", () => applySlashCommand(button.dataset.command));
  });
}

function moveSlashSelection(delta) {
  if (!state.slashOpen || !state.slashMatches.length) return;
  state.slashIndex = (state.slashIndex + delta + state.slashMatches.length) % state.slashMatches.length;
  renderSlashPalette();
  document.getElementById(`slash-option-${state.slashIndex}`)?.scrollIntoView({ block: "nearest" });
}

function applySlashCommand(command) {
  const input = $("prompt-input");
  const raw = getPromptText();
  const caret = getCaretOffset(input);
  const range = slashQueryRange(raw, caret) || { start: caret, end: caret };
  const suffix = raw.slice(range.end).replace(/^\s+/, "");
  const insertion = `${command} `;
  const next = `${raw.slice(0, range.start)}${insertion}${suffix}`;
  setPromptText(next, range.start + insertion.length);
  closeSlashPalette();
  input.focus();
}

function closeSlashPalette() {
  $("slash-palette").classList.add("hidden");
  state.slashOpen = false;
  state.slashMatches = [];
  state.slashIndex = 0;
  $("prompt-input").setAttribute("aria-expanded", "false");
  $("prompt-input").removeAttribute("aria-activedescendant");
}

function parseSlashCommand(raw) {
  const parsed = extractInlineSlashCommand(raw);
  const command = parsed.command;
  if (!command) {
    return { message: parsed.message, skillId: null, command: null };
  }
  return { message: parsed.message, skillId: command.skillId, command };
}

function renderAll() {
  renderCompanies();
  renderSessions();
  renderCommands();
  renderTools();
  renderModelPicker();
  renderHeader();
  renderWorkspace();
  renderPromptSyntax();
  renderSlashPalette();
}

async function loadBootstrap() {
  const companies = await api("/companies");
  state.companies = companies.companies?.length ? companies.companies.map(normalizeCompany) : [normalizeCompany("company_001")];
  if (!state.companies.some((company) => company.company_id === state.companyId)) {
    state.companyId = state.companies[0].company_id;
  }
  await Promise.all([loadSkills(), loadModelStatus(), loadSessions(), loadWiki()]);
  if (!state.currentSessionId) {
    await createSession();
  } else {
    await loadSessionData();
  }
  renderAll();
}

async function loadModelStatus() {
  const data = await api(`/companies/${state.companyId}/workbench/models`);
  state.models = data.models || [];
  const model = state.models.find((item) => item.model_id === DEFAULT_MODEL_ID) || state.models[0] || null;
  state.modelStatus = {
    available: Boolean(model?.metadata?.available),
    missing: model?.metadata?.missing || [],
    authConfigured: Boolean(model?.metadata?.auth_configured),
    configuredModel: model?.metadata?.configured_model || DEFAULT_MODEL_ID,
  };
}

async function loadSkills() {
  const data = await api(`/companies/${state.companyId}/workbench/skills`);
  state.skills = data.skills || [];
}

async function loadWiki() {
  try {
    state.wiki = await api(`/companies/${state.companyId}/workbench/wiki`);
  } catch (error) {
    state.wiki = {
      schema_version: "workbench.wiki_tree.v1",
      company_id: state.companyId,
      profile: {},
      pages: [],
      facts: [],
      stats: {},
      error: error.message,
    };
  }
}

async function refreshWiki() {
  state.wikiAction = "refresh";
  renderWiki();
  try {
    await loadWiki();
    toast("Wiki 已重新读取");
  } catch (error) {
    state.lastError = error.message;
    toast(`Wiki 读取失败：${error.message}`);
  } finally {
    state.wikiAction = "";
    renderAll();
  }
}

async function saveSelectedWikiPage() {
  if (!state.selectedWikiPath || state.wikiAction) return;
  let nextBody = "";
  try {
    nextBody = buildStructuredWikiBody();
  } catch (error) {
    toast(`Wiki 表单未通过校验：${error.message}`);
    return;
  }
  state.wikiAction = "save";
  renderWiki();
  try {
    const data = await api(`/companies/${state.companyId}/workbench/wiki/pages`, {
      method: "POST",
      body: JSON.stringify({
        path: state.selectedWikiPath,
        body: nextBody,
      }),
    });
    state.wiki = data.wiki || state.wiki;
    state.selectedWikiPath = data.page?.path || state.selectedWikiPath;
    toast("Wiki 已保存并重新索引");
  } catch (error) {
    state.lastError = error.message;
    toast(`Wiki 保存失败：${error.message}`);
  } finally {
    state.wikiAction = "";
    renderAll();
  }
}

async function loadSessions() {
  const data = await api(`/companies/${state.companyId}/workbench/sessions`);
  state.sessions = data.sessions || [];
  if (!state.currentSessionId || !state.sessions.some((item) => item.session_id === state.currentSessionId)) {
    state.currentSessionId =
      state.sessions.find((item) => Number(item.message_count || 0) > 0 && !isTestSession(item))?.session_id ||
      state.sessions.find((item) => Number(item.message_count || 0) > 0)?.session_id ||
      state.sessions[0]?.session_id ||
      "";
  }
}

async function loadSessionData(sessionId = state.currentSessionId) {
  if (!sessionId) return;
  const epoch = ++state.sessionLoadEpoch;
  const [messages, trace] = await Promise.all([
    api(`/companies/${state.companyId}/workbench/sessions/${sessionId}/messages`),
    api(`/companies/${state.companyId}/workbench/sessions/${sessionId}/trace`),
  ]);
  if (epoch !== state.sessionLoadEpoch || state.currentSessionId !== sessionId) return;
  state.messages = messages.messages || [];
  state.trace = trace || null;
  state.tracesByRun = trace?.run_id ? { [trace.run_id]: trace } : {};
}

async function loadHistoricalTrace(runId, button) {
  if (!runId || !state.currentSessionId || state.tracesByRun[runId]) return;
  if (button) {
    button.disabled = true;
    button.textContent = "读取中";
  }
  try {
    const trace = await api(
      `/companies/${state.companyId}/workbench/sessions/${state.currentSessionId}/runs/${runId}/trace`,
    );
    state.tracesByRun[runId] = trace;
    renderChat({ scrollRunId: runId });
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重试运行记录";
    }
    toast(`运行记录读取失败：${error.message}`);
  }
}

async function createSession() {
  if (state.sending) return;
  state.currentView = "chat";
  const session = await api(`/companies/${state.companyId}/workbench/sessions`, {
    method: "POST",
    body: JSON.stringify({
      title: "新对话",
      mode: "auto",
      model_id: DEFAULT_MODEL_ID,
      active_skill_id: "",
    }),
  });
  state.currentSessionId = session.session_id;
  state.lastError = "";
  await loadSessions();
  await loadSessionData();
  closeMobileSidebar();
  renderAll();
}

async function selectSession(sessionId) {
  if (state.sending || !sessionId || sessionId === state.currentSessionId) return;
  state.currentSessionId = sessionId;
  state.currentView = "chat";
  state.lastError = "";
  await loadSessionData();
  closeMobileSidebar();
  renderAll();
}

async function updateSessionSettings(partial, options = {}) {
  const session = currentSession();
  if (!session) return null;
  const next = {
    title: session.title,
    mode: session.mode,
    model_id: DEFAULT_MODEL_ID,
    active_skill_id: session.active_skill_id,
    ...partial,
  };
  if (Object.prototype.hasOwnProperty.call(partial, "active_skill_id")) {
    next.mode = partial.active_skill_id ? "skill" : "auto";
  }
  await api(`/companies/${state.companyId}/workbench/sessions/${session.session_id}/settings`, {
    method: "POST",
    body: JSON.stringify(next),
  });
  await loadSessions();
  await loadSessionData();
  if (options.render !== false) {
    renderAll();
  }
  return currentSession();
}

async function sendMessage() {
  const input = $("prompt-input");
  const rawText = getPromptText().trim();
  const raw = rawText;
  if (!raw || state.sending || !state.currentSessionId) return;
  const targetSessionId = state.currentSessionId;
  state.sending = true;
  state.lastError = "";
  setPromptDisabled(true);
  renderHeader();
  renderSessions();
  renderSlashPalette();
  try {
    await loadModelStatus();
    if (!modelAvailable()) {
      throw new Error("当前没有可用模型，无法执行本轮任务");
    }
    if (currentSession()?.model_id !== DEFAULT_MODEL_ID) {
      await updateSessionSettings({ model_id: DEFAULT_MODEL_ID }, { render: false });
    }
    state.streaming = {
      sessionId: targetSessionId,
      userMessage: raw,
      content: "",
      runId: "",
      artifacts: {},
      status: "Runtime 已接收请求",
      startedAt: new Date().toISOString(),
    };
    setPromptText("");
    renderWorkspace();
    await streamApi(`/companies/${state.companyId}/workbench/sessions/${targetSessionId}/messages/stream`, { message: raw }, async (type, payload) => {
      if (!state.streaming || state.streaming.sessionId !== targetSessionId) return;
      if (type === "start") {
        state.streaming.status = "构建 Context / Memory / Tool Policy";
      } else if (type === "result") {
        state.streaming.runId = payload.run_id || state.streaming.runId;
        state.streaming.artifacts = payload.artifacts || {};
        state.streaming.status = payload.selected_skill_id === "external_impact_report"
          ? "报告工作流已完成，正在流式展示"
          : "主 Agent 已完成，正在流式展示";
      } else if (type === "chunk") {
        state.streaming.content += payload.content || "";
      } else if (type === "trace") {
        state.trace = payload || null;
        if (payload?.run_id) state.tracesByRun[payload.run_id] = payload;
        state.streaming.status = "Trace 已回传";
      } else if (type === "done") {
        state.streaming.runId = payload.run_id || state.streaming.runId;
        state.streaming.status = "完成";
      } else if (type === "error") {
        throw new Error(payload.detail || "流式执行失败");
      }
      renderWorkspace({ scrollRunId: state.streaming.runId });
    });
    await loadSessions();
    if (state.currentSessionId === targetSessionId) await loadSessionData(targetSessionId);
    await loadModelStatus();
    state.streaming = null;
    renderAll();
  } catch (error) {
    state.streaming = null;
    state.lastError = error.message;
    setPromptText(rawText);
    toast(`发送失败：${error.message}`);
    await loadSessions();
    if (state.currentSessionId === targetSessionId) await loadSessionData(targetSessionId);
    await loadModelStatus().catch(() => null);
    renderAll();
  } finally {
    state.sending = false;
    setPromptDisabled(false);
    renderHeader();
    renderSessions();
    input.focus();
  }
}

function focusSlashCommand() {
  const input = $("prompt-input");
  input.focus();
  const raw = getPromptText();
  const caret = getCaretOffset(input);
  const needsLeadingSpace = caret > 0 && !/\s/.test(raw[caret - 1] || "") ? " " : "";
  replacePromptRange(caret, caret, `${needsLeadingSpace}/`);
  renderSlashPalette();
}

function autoGrow(editor) {
  editor.style.height = "auto";
  const contentHeight = editor.scrollHeight;
  editor.style.height = `${Math.min(contentHeight, 160)}px`;
  editor.style.overflowY = contentHeight > 160 ? "auto" : "hidden";
}

function toggleSidebar() {
  const app = $("app");
  if (window.matchMedia("(max-width: 900px)").matches) {
    app.classList.remove("sidebar-collapsed", "mobile-sidebar-open");
    syncSidebarControlState();
    return;
  }
  app.classList.toggle("sidebar-collapsed");
  syncSidebarControlState();
}

function toggleMobileSidebar() {
  $("app").classList.remove("sidebar-collapsed");
  $("app").classList.toggle("mobile-sidebar-open");
  syncSidebarControlState();
}

function closeMobileSidebar() {
  $("app").classList.remove("sidebar-collapsed", "mobile-sidebar-open");
  syncSidebarControlState();
}

function syncSidebarControlState() {
  const app = $("app");
  const mobile = window.matchMedia("(max-width: 900px)").matches;
  const label = mobile
    ? "关闭侧边栏"
    : app.classList.contains("sidebar-collapsed")
      ? "展开侧边栏"
      : "收起侧边栏";
  $("sidebar-toggle").title = label;
  $("sidebar-toggle").setAttribute("aria-label", label);
}

function normalizeSidebarForViewport() {
  const app = $("app");
  if (window.matchMedia("(max-width: 900px)").matches) {
    app.classList.remove("sidebar-collapsed");
  } else {
    app.classList.remove("mobile-sidebar-open");
  }
  syncSidebarControlState();
  autoGrow($("prompt-input"));
}

function bindEvents() {
  $("new-session").addEventListener("click", createSession);
  $("sidebar-toggle").addEventListener("click", toggleSidebar);
  $("mobile-sidebar-toggle").addEventListener("click", toggleMobileSidebar);
  $("sidebar-backdrop").addEventListener("click", closeMobileSidebar);
  window.addEventListener("resize", normalizeSidebarForViewport);
  $("focus-slash-command").addEventListener("click", focusSlashCommand);
  $("company-select").addEventListener("change", async (event) => {
    state.companyId = event.target.value;
    state.currentSessionId = "";
    state.messages = [];
    state.trace = null;
    state.tracesByRun = {};
    state.wiki = null;
    state.selectedWikiPath = "";
    state.lastError = "";
    await Promise.all([loadSkills(), loadModelStatus(), loadSessions(), loadWiki()]);
    if (!state.currentSessionId) {
      await createSession();
    } else {
      await loadSessionData();
      renderAll();
    }
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      const nextView = button.dataset.view || "chat";
      if (state.currentView === nextView) return;
      state.currentView = nextView;
      if (nextView === "wiki" && !state.wiki) await loadWiki();
      renderAll();
    });
  });
  $("session-search").addEventListener("input", renderSessions);
  $("toggle-test-sessions").addEventListener("click", () => {
    state.showTestSessions = !state.showTestSessions;
    renderSessions();
  });
  $("model-picker").addEventListener("change", (event) => {
    if (event.target.value !== DEFAULT_MODEL_ID) {
      event.target.value = DEFAULT_MODEL_ID;
      toast("当前只配置了 deepseek-v4-flash");
    }
  });
  $("send-message").addEventListener("click", sendMessage);
  $("prompt-input").addEventListener("compositionstart", () => {
    state.composing = true;
  });
  $("prompt-input").addEventListener("compositionend", (event) => {
    state.composing = false;
    renderPromptSyntax(getCaretOffset(event.target));
    renderSlashPalette();
  });
  $("prompt-input").addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, text);
  });
  $("prompt-input").addEventListener("input", (event) => {
    if (!state.composing) {
      renderPromptSyntax(getCaretOffset(event.target));
      renderSlashPalette();
    } else {
      autoGrow(event.target);
    }
  });
  $("prompt-input").addEventListener("click", renderSlashPalette);
  $("prompt-input").addEventListener("keyup", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) renderSlashPalette();
  });
  $("prompt-input").addEventListener("keydown", (event) => {
    if (state.slashOpen && event.key === "ArrowDown") {
      event.preventDefault();
      moveSlashSelection(1);
      return;
    }
    if (state.slashOpen && event.key === "ArrowUp") {
      event.preventDefault();
      moveSlashSelection(-1);
      return;
    }
    if (event.key === "Escape" && state.slashOpen) {
      event.preventDefault();
      closeSlashPalette();
      return;
    }
    if (state.slashOpen && (event.key === "Enter" || event.key === "Tab")) {
      event.preventDefault();
      const selected = state.slashMatches[state.slashIndex];
      if (selected) applySlashCommand(selected.command);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
}

async function init() {
  bindEvents();
  normalizeSidebarForViewport();
  try {
    await loadBootstrap();
  } catch (error) {
    state.lastError = error.message;
    toast(`初始化失败：${error.message}`);
    $("chat-stream").innerHTML = `
      <div class="empty-state">
        <div>
          <h2>初始化失败</h2>
          <p>${escapeHtml(error.message)}</p>
        </div>
      </div>
    `;
  }
}

init();
