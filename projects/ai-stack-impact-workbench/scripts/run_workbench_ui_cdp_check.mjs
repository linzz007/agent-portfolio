import { spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const BASE_URL = process.env.WORKBENCH_BASE_URL || "http://127.0.0.1:8501/";
const OUT_DIR = resolve("data/acceptance");
const SCREENSHOT_DIR = join(OUT_DIR, "screenshots");
const VIEWPORT = {
  width: Number(process.env.WORKBENCH_VIEWPORT_WIDTH || 1365),
  height: Number(process.env.WORKBENCH_VIEWPORT_HEIGHT || 900),
};

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean);
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) {
    throw new Error("Chrome/Edge executable not found. Set CHROME_PATH to run UI checks.");
  }
  return found;
}

function wait(ms) {
  return new Promise((resolveWait) => setTimeout(resolveWait, ms));
}

async function waitForJson(url, timeoutMs = 8000) {
  const started = Date.now();
  let lastError = null;
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await wait(200);
  }
  throw lastError || new Error(`Timed out waiting for ${url}`);
}

class CdpClient {
  constructor(webSocketUrl) {
    this.ws = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
  }

  async open() {
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (!msg.id || !this.pending.has(msg.id)) return;
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    };
    await new Promise((resolveOpen, rejectOpen) => {
      this.ws.onopen = resolveOpen;
      this.ws.onerror = rejectOpen;
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolveSend, rejectSend) => {
      this.pending.set(id, { resolve: resolveSend, reject: rejectSend });
    });
  }

  close() {
    this.ws.close();
  }
}

function buildInspectionExpression() {
  return `(() => {
    const rect = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return {
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
        right: Math.round(r.right),
        bottom: Math.round(r.bottom),
        text: (el.innerText || el.value || '').slice(0, 700),
        display: style.display,
        overflowY: style.overflowY,
      };
    };
    const overlap = (a, b) => Boolean(a && b && a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y);
    const sidebar = rect('#sidebar');
    const sessions = rect('.sessions-section');
    const sessionList = rect('.session-list');
    const consoleBox = rect('.console-section');
    const composer = rect('.composer');
    const composerBox = rect('.composer-box');
    const modelPicker = rect('#model-picker');
    const stream = rect('#chat-stream');
    const visibleThinking = (() => {
      const el = document.querySelector('.thinking-card');
      if (!el || !stream) return null;
      const r = el.getBoundingClientRect();
      return {
        x: Math.round(r.x),
        y: Math.round(Math.max(r.y, stream.y)),
        w: Math.round(r.width),
        h: Math.round(Math.max(0, Math.min(r.bottom, stream.bottom) - Math.max(r.y, stream.y))),
        right: Math.round(r.right),
        bottom: Math.round(Math.min(r.bottom, stream.bottom)),
      };
    })();
    return {
      viewport: { w: innerWidth, h: innerHeight },
      title: document.querySelector('#session-title')?.innerText || '',
      sessionCountText: document.querySelector('#session-count')?.innerText || '',
      sessionItems: document.querySelectorAll('.session-item').length,
      companyCardText: document.querySelector('#company-card')?.innerText || '',
      companyValue: document.querySelector('#company-select')?.value || '',
      companyOptionText: document.querySelector('#company-select')?.selectedOptions?.[0]?.textContent?.trim() || '',
      commandRows: document.querySelectorAll('.command-row').length,
      toolRows: document.querySelectorAll('.tool-row').length,
      traceExists: Boolean(document.querySelector('.thinking-card')),
      traceEvidenceCells: document.querySelectorAll('.trace-evidence-cell').length,
      stepItems: document.querySelectorAll('.step-item').length,
      runtimeText: document.querySelector('#runtime-status')?.innerText || '',
      modelPickerValue: document.querySelector('#model-picker')?.value || '',
      modelOptionCount: document.querySelectorAll('#model-picker option').length,
      modelOptionLabels: [...document.querySelectorAll('#model-picker option')].map((option) => option.textContent.trim()),
      hasModelManager: Boolean(document.querySelector('#model-settings, #model-modal, #open-model-manager')),
      hasReplacementChar: document.body.innerText.includes('�'),
      hasChineseText: document.body.innerText.includes('默认对话') && document.body.innerText.includes('新建对话'),
      hasWikiViewTab: Boolean(document.querySelector('[data-view="wiki"]')),
      bodyStable: document.body.scrollHeight <= window.innerHeight + 2,
      bodyWidthStable: document.documentElement.scrollWidth <= window.innerWidth + 2,
      sessionListScrollable: sessionList ? document.querySelector('.session-list').scrollHeight > sessionList.h : false,
      sessionConsoleOverlap: overlap(sessions, consoleBox),
      visibleTraceComposerOverlap: overlap(visibleThinking, composer),
      modelPickerInsideComposer: Boolean(
        modelPicker && composerBox &&
        modelPicker.x >= composerBox.x && modelPicker.right <= composerBox.right &&
        modelPicker.y >= composerBox.y && modelPicker.bottom <= composerBox.bottom
      ),
      routeSummaryVisible: document.body.innerText.includes('路由'),
      contextSummaryVisible: document.body.innerText.includes('上下文'),
      observabilitySummaryVisible: document.body.innerText.includes('可观测') && document.body.innerText.includes('workbench.trace.v2'),
      rects: { sidebar, sessions, sessionList, consoleBox, stream, visibleThinking, composer, composerBox, modelPicker },
    };
  })()`;
}

async function main() {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
  const chromePath = findChrome();
  const port = Number(process.env.WORKBENCH_CDP_PORT || 9300 + Math.floor(Math.random() * 500));
  const userDataDir = join(tmpdir(), `policy-impact-cdp-${Date.now()}`);
  const chrome = spawn(
    chromePath,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${userDataDir}`,
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  let client;
  try {
    await waitForJson(`http://127.0.0.1:${port}/json/version`);
    const tab = await (
      await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(`${BASE_URL}?v=ui-check-${Date.now()}`)}`, {
        method: "PUT",
      })
    ).json();
    client = new CdpClient(tab.webSocketDebuggerUrl);
    await client.open();
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: VIEWPORT.width,
      height: VIEWPORT.height,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await client.send("Page.navigate", { url: `${BASE_URL}?v=ui-check-${Date.now()}` });
    await wait(Number(process.env.WORKBENCH_UI_WAIT_MS || 4500));
    await client.send("Runtime.evaluate", {
      expression: "(() => { const trace = document.querySelector('.thinking-card'); if (trace) trace.open = true; return Boolean(trace); })()",
      returnByValue: true,
    });
    await wait(200);

    const inspectionResult = await client.send("Runtime.evaluate", {
      expression: buildInspectionExpression(),
      returnByValue: true,
      awaitPromise: true,
    });
    const inspection = inspectionResult.result.value;
    const apiBase = BASE_URL.replace(/\/$/, "");
    const modelPayload = await waitForJson(`${apiBase}/companies/company_001/workbench/models`);
    const configuredModel = modelPayload.models?.[0] || {};
    const modelAvailable = Boolean(configuredModel.metadata?.available);

    const screenshot = await client.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
    const screenshotPath = join(SCREENSHOT_DIR, `workbench-ui-check-${stamp}.png`);
    writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64"));

    const checks = [
      ["has_session_items", inspection.sessionItems >= 3, `${inspection.sessionItems}`],
      ["has_company_workspace", inspection.companyValue === "company_001" && inspection.companyOptionText.includes("示例公司"), JSON.stringify({ value: inspection.companyValue, label: inspection.companyOptionText })],
      ["has_report_slash_command", inspection.commandRows === 1, `${inspection.commandRows}`],
      ["has_trace", inspection.traceExists, `${inspection.traceExists}`],
      ["has_trace_evidence_grid", inspection.traceEvidenceCells >= 7, `${inspection.traceEvidenceCells}`],
      ["has_trace_steps", inspection.stepItems >= 4, `${inspection.stepItems}`],
      ["has_composer_model_picker", inspection.modelPickerInsideComposer, `${inspection.modelPickerInsideComposer}`],
      [
        "model_picker_matches_flash_availability",
        modelAvailable
          ? inspection.modelOptionCount === 1 &&
              inspection.modelPickerValue === "deepseek-v4-flash" &&
              inspection.modelOptionLabels[0] === "deepseek-v4-flash"
          : inspection.modelOptionCount === 1 &&
              inspection.modelPickerValue === "" &&
              inspection.modelOptionLabels[0] === "无可用模型",
        JSON.stringify({
          available: modelAvailable,
          value: inspection.modelPickerValue,
          count: inspection.modelOptionCount,
          labels: inspection.modelOptionLabels,
        }),
      ],
      ["no_fake_model_manager", !inspection.hasModelManager, `${inspection.hasModelManager}`],
      ["no_replacement_char", !inspection.hasReplacementChar, `${inspection.hasReplacementChar}`],
      ["has_chinese_text", inspection.hasChineseText, `${inspection.hasChineseText}`],
      ["has_wiki_view_tab", inspection.hasWikiViewTab, `${inspection.hasWikiViewTab}`],
      ["body_not_page_scrolling", inspection.bodyStable, `${inspection.bodyStable}`],
      ["no_horizontal_page_overflow", inspection.bodyWidthStable, `${inspection.bodyWidthStable}`],
      ["session_list_scrollable", inspection.sessionListScrollable, `${inspection.sessionListScrollable}`],
      ["session_console_not_overlap", !inspection.sessionConsoleOverlap, `${inspection.sessionConsoleOverlap}`],
      ["trace_not_under_composer", !inspection.visibleTraceComposerOverlap, `${inspection.visibleTraceComposerOverlap}`],
      [
        "runtime_status_visible",
        modelAvailable
          ? inspection.modelPickerValue === "deepseek-v4-flash" &&
              (inspection.viewport.w <= 720 || inspection.runtimeText.includes("就绪"))
          : inspection.runtimeText.includes("不可用") ||
              inspection.runtimeText.includes("未就绪") ||
              inspection.runtimeText.includes("缺少"),
        inspection.runtimeText || "mobile status dot",
      ],
      ["route_summary_visible", inspection.routeSummaryVisible, `${inspection.routeSummaryVisible}`],
      ["context_summary_visible", inspection.contextSummaryVisible, `${inspection.contextSummaryVisible}`],
      ["observability_summary_visible", inspection.observabilitySummaryVisible, `${inspection.observabilitySummaryVisible}`],
    ].map(([name, passed, detail]) => ({ name, passed: Boolean(passed), detail }));

    const summary = {
      generated_at: new Date().toISOString(),
      base_url: BASE_URL,
      viewport: VIEWPORT,
      model_available: modelAvailable,
      screenshot_path: screenshotPath,
      inspection,
      checks,
      passed: checks.every((item) => item.passed),
    };
    const latestPath = join(OUT_DIR, "latest_workbench_ui_cdp_check.json");
    writeFileSync(latestPath, JSON.stringify(summary, null, 2), "utf8");
    console.log(JSON.stringify({ passed: summary.passed, screenshot_path: screenshotPath, checks }, null, 2));
    process.exitCode = summary.passed ? 0 : 1;
  } finally {
    if (client) client.close();
    chrome.kill("SIGKILL");
    await wait(300);
    try {
      rmSync(userDataDir, { recursive: true, force: true });
    } catch (error) {
      console.warn(`Warning: failed to remove temporary Chrome profile: ${error.message}`);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
