const state = {
  scenarios: [],
  response: null,
  running: false,
  stageOrder: ["perception", "intent", "strategy", "execution", "feedback"],
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const pretty = (value) => escapeHtml(JSON.stringify(value, null, 2));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const STATUS_LABELS = {
  READY: "已就绪",
  NEEDS_CLARIFICATION: "需要澄清",
  BLOCKED: "安全阻断",
  SUCCEEDED: "执行成功",
  FAILED: "执行失败",
  SAFE_STOP: "安全停止",
  SUCCESS: "动作成功",
  SKIPPED: "已跳过",
};
const ACTION_LABELS = {
  pick: "单独抓取",
  pick_and_place: "抓取并放置",
  place: "放置",
  transfer: "搬运",
  fetch: "取物到目标",
  stack: "堆叠",
  detect_object: "检测物体",
  move_to_object: "移动到物体",
  grasp: "抓取",
  move_to_target: "移动到目标区",
  release: "释放物体",
  push: "推动物体",
  stop: "停止执行",
};
const STRATEGY_MODE_LABELS = {
  primitive_plan: "本地原子策略",
  primitive_plan_fallback: "本地安全回退策略",
  tracecoder_demo_baseline: "TraceCoder 修复演示基线",
  codearts_agent: "CodeArts 智能体策略",
  blocked: "策略已阻断",
};
const STOP_REASON_LABELS = {
  EXECUTION_SUCCEEDED: "执行成功，闭环结束",
  SAFETY_STOP: "触发安全停止",
  FEEDBACK_NOT_RETRYABLE: "反馈判定不可重试",
  SAFETY_EVENT: "检测到安全事件",
  PATCH_INVALID: "修复 patch 不合法",
  PATCH_MISSING: "没有可用 patch",
  PATCH_UNCHANGED: "修复策略没有变化",
  PATCH_TASK_ID_MISMATCH: "patch 任务编号不匹配",
  PATCH_CODE_NOT_ALLOWED: "patch 含不允许执行代码",
  NO_TRACE_CODER: "未配置反馈修复模块",
  MAX_RETRIES_EXCEEDED: "达到最大重试次数",
};

function codeWithMeaning(value, meanings = {}) {
  const code = String(value ?? "—");
  const meaning = meanings[code];
  return meaning
    ? `<span class="meaning-label">${escapeHtml(meaning)}</span><code>${escapeHtml(code)}</code>`
    : `<code>${escapeHtml(code)}</code>`;
}

function idWithMeaning(value, meaning = "对象编号") {
  if (!value) return "—";
  return `<span class="meaning-label">${escapeHtml(meaning)}</span><code>${escapeHtml(value)}</code>`;
}

function provenanceHtml(provenance, label = "来源证据") {
  if (!provenance || typeof provenance !== "object") return "";
  const source = provenance.source || provenance.provider || "—";
  const agent = provenance.agent || "—";
  const model = provenance.model || "—";
  const requestId = provenance.request_id || provenance.run_id || "—";
  const latency = provenance.latency_ms == null ? "—" : `${Number(provenance.latency_ms).toFixed(1)} ms`;
  const fallback = provenance.fallback === true || provenance.used_fallback === true ? "是" : "否";
  const validation = provenance.validation || provenance.patch_validation;
  const validationText = validation && validation.passed === false ? "未通过" : validation ? "通过" : "—";
  return `<div class="provenance-detail"><strong>${escapeHtml(label)}</strong><div class="kv-grid"><div class="kv"><small>实际来源</small><strong>${escapeHtml(String(source))}</strong></div><div class="kv"><small>Agent</small><strong>${escapeHtml(String(agent))}</strong></div><div class="kv"><small>模型</small><strong>${escapeHtml(String(model))}</strong></div><div class="kv"><small>请求/运行 ID</small><strong>${escapeHtml(String(requestId))}</strong></div><div class="kv"><small>调用耗时</small><strong>${escapeHtml(latency)}</strong></div><div class="kv"><small>发生回退</small><strong class="${fallback === "是" ? "status-warn" : "status-ok"}">${fallback}</strong></div><div class="kv"><small>校验结论</small><strong>${escapeHtml(validationText)}</strong></div></div></div>`;
}

document.addEventListener("DOMContentLoaded", init);

async function init() {
  buildTimeline();
  initModuleToggles();
  $("sceneSelect").addEventListener("change", onSceneChanged);
  $("instruction").addEventListener("input", () => {
    if (!state.response) {
      updateEnvironmentQuick();
      renderAcceptance(null);
    }
  });
  $("runButton").addEventListener("click", runDemo);
  $("resetButton").addEventListener("click", resetDemo);
  try {
    const [scenarioResponse, healthResponse] = await Promise.all([fetchJson("/api/scenarios"), fetchJson("/api/health")]);
    state.scenarios = scenarioResponse.scenarios || [];
    fillScenarios();
    setHealth(healthResponse);
  } catch (error) {
    setHealth(null);
    showError(`无法连接演示服务：${error.message}。请在仓库根目录执行 python demo/server.py。`);
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function buildTimeline() {
  const names = { perception: ["P", "感知"], intent: ["A", "意图"], strategy: ["B", "策略"], execution: ["C", "模拟执行（Mock）"], feedback: ["D", "反馈"] };
  const timeline = $("timeline");
  timeline.innerHTML = '<div class="timeline-line"><span id="timelineProgress"></span></div>' + state.stageOrder.map((stage, index) => `
    <div class="timeline-item" data-stage="${stage}">
      <div class="timeline-dot">${names[stage][0]}</div><div class="timeline-label">${names[stage][1]}</div>
    </div>`).join("");
}

function fillScenarios() {
  const select = $("sceneSelect");
  select.innerHTML = state.scenarios.map((scenario) => `<option value="${escapeHtml(scenario.id)}">${escapeHtml(scenario.name)}</option>`).join("");
  onSceneChanged();
}

function selectedScenario() {
  return state.scenarios.find((item) => item.id === $("sceneSelect").value) || state.scenarios[0];
}

function onSceneChanged() {
  const scenario = selectedScenario();
  if (!scenario) return;
  $("sceneDescription").textContent = scenario.description;
  $("instruction").value = scenario.instruction;
  const commands = scenario.commands?.length ? scenario.commands : [{ instruction: scenario.instruction }];
  $("quickCommands").innerHTML = commands.map((command) => `<button class="quick-btn" type="button">${escapeHtml(command.instruction)}</button>`).join("");
  $("quickCommands").querySelectorAll("button").forEach((button, index) => {
    button.addEventListener("click", () => {
      $("instruction").value = commands[index].instruction;
      updateEnvironmentQuick();
      renderAcceptance(null);
    });
  });
  renderMiniScene(scenario.scene);
  renderEnvironmentDetails(scenario.scene);
  updateEnvironmentQuick();
  resetDemo(false);
}

function initModuleToggles() {
  document.querySelectorAll("[data-module-toggle]").forEach((button) => {
    button.dataset.expandLabel = button.textContent.replace(/[＋－]/g, "").trim();
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const card = button.closest(".module-card");
      if (!card) return;
      const expanded = card.classList.toggle("expanded");
      button.setAttribute("aria-expanded", String(expanded));
      button.innerHTML = expanded ? "收起详情 <span>－</span>" : `${button.dataset.expandLabel} <span>＋</span>`;
      if (!expanded) return;
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
}

const SCENE_BOUNDS = { xMin: -0.05, xMax: 0.50, yMin: -0.25, yMax: 0.25 };

function selectedExpected() {
  const scenario = selectedScenario();
  if (!scenario) return "—";
  const instruction = $("instruction")?.value?.trim();
  const command = scenario.commands?.find((item) => item.instruction === instruction);
  return command?.expected || scenario.expected;
}

function updateEnvironmentQuick() {
  const scenario = selectedScenario();
  if (!scenario || !$("environmentQuick")) return;
  $("environmentQuick").innerHTML = `<span class="quick-label">${escapeHtml(scenario.focus || "闭环演示")}</span><strong>${escapeHtml(scenario.name)}</strong><span class="scenario-expectation">当前指令预期：${codeWithMeaning(selectedExpected(), STATUS_LABELS)}</span>`;
}

function worldToScreen(pose, width, height, margin) {
  const x = Number(pose?.x || 0);
  const y = Number(pose?.y || 0);
  const lateral = Math.max(0, Math.min(1, (y - SCENE_BOUNDS.yMin) / (SCENE_BOUNDS.yMax - SCENE_BOUNDS.yMin)));
  const depth = Math.max(0, Math.min(1, (x - SCENE_BOUNDS.xMin) / (SCENE_BOUNDS.xMax - SCENE_BOUNDS.xMin)));
  return { x: margin + lateral * (width - margin * 2), y: height - margin - depth * (height - margin * 2) };
}

function sceneObjectColor(item) {
  return item?.attributes?.color || (item?.execution?.valid_destination ? "target" : "blue");
}

function sceneObjectLabel(item) {
  const displayName = item?.attributes?.display_name || item?.category || item?.id || "对象";
  return displayName.length > 8 ? displayName.slice(0, 8) : displayName;
}

function sceneSpatialMessages(scene, limit = 4) {
  return (scene?.spatial_messages || []).slice(0, limit).map((item) => item.message || item.text).filter(Boolean);
}

function renderMiniScene(scene) {
  const objects = scene?.objects || [];
  const html = objects.map((item) => {
    const point = worldToScreen(item.pose, 100, 100, 14);
    const color = sceneObjectColor(item);
    return `<span class="mini-object ${escapeHtml(color)}${item.execution?.valid_destination ? " target" : ""}" style="left:${point.x.toFixed(1)}%;top:${point.y.toFixed(1)}%" title="${escapeHtml(`${sceneObjectLabel(item)} · ${item.id} · X=${Number(item.pose?.x || 0).toFixed(2)}, Y=${Number(item.pose?.y || 0).toFixed(2)}, Z=${Number(item.pose?.z || 0).toFixed(2)}`)}"></span>`;
  }).join("");
  $("sceneMini").innerHTML = html || '<span class="scene-empty">场景暂无对象</span>';
  const messages = sceneSpatialMessages(scene, 3);
  if ($("sceneSpatialMessage")) {
    $("sceneSpatialMessage").innerHTML = messages.length
      ? `<span class="scene-spatial-label">空间消息</span>${messages.map((message) => `<span>${escapeHtml(message)}</span>`).join("")}`
      : '<span class="scene-spatial-label">空间消息</span><span>暂无可用空间关系</span>';
  }
}

function renderEnvironmentDetails(scene) {
  const detail = $("environmentDetail");
  if (!detail || !scene) return;
  const objects = scene.objects || [];
  const messages = sceneSpatialMessages(scene, 8);
  detail.innerHTML = `<div class="kv-grid"><div class="kv"><small>场景编号</small><strong>${idWithMeaning(scene.scene_id, "场景")}</strong></div><div class="kv"><small>坐标系</small><strong>${escapeHtml(scene.coordinate_frame || "world")} · X前后 / Y左右 / Z高度</strong></div></div><div class="object-list">${objects.map((item) => `<div class="object-row"><span class="object-name"><i class="object-dot ${escapeHtml(sceneObjectColor(item))}"></i>${escapeHtml(sceneObjectLabel(item))}</span><span class="object-meta">${idWithMeaning(item.id)}<br/>(${Number(item.pose?.x || 0).toFixed(2)}, ${Number(item.pose?.y || 0).toFixed(2)}, ${Number(item.pose?.z || 0).toFixed(2)})</span></div>`).join("")}</div><div class="spatial-detail"><strong>空间关系</strong>${messages.length ? messages.map((message) => `<div>${escapeHtml(message)}</div>`).join("") : "<div>暂无空间关系</div>"}</div>`;
}

function setHealth(data) {
  const pill = $("healthPill");
  if (!data) { pill.classList.remove("ready"); pill.innerHTML = '<span class="health-dot"></span>服务未连接'; return; }
  const modules = Object.values(data.modules || {});
  const ready = data.status === "ok" && data.healthy === true
    && modules.every((item) => item.healthy !== false && (item.healthy === true || item.status === "ok" || item.status === "healthy"));
  pill.classList.toggle("ready", ready);
  pill.innerHTML = ready
    ? '<span class="health-dot"></span>本地服务已连接 · 所有模块健康'
    : '<span class="health-dot"></span>服务已连接 · 存在模块异常';
}

function renderAcceptance(response = null) {
  const banner = $("acceptanceBanner");
  const status = $("acceptanceStatus");
  const detail = $("acceptanceDetail");
  if (!banner || !status || !detail) return;

  if (!response) {
    banner.dataset.state = "pending";
    status.textContent = "等待运行";
    detail.innerHTML = selectedScenario()
      ? `预期结果：${codeWithMeaning(selectedExpected(), STATUS_LABELS)} · 实际结果：尚未运行`
      : "选择场景并运行后，对比预期结果和系统实际结果。";
    return;
  }

  const acceptance = response.acceptance || {};
  const passed = acceptance.passed === true;
  const metrics = response.metrics || acceptance.metrics || {};
  const metricText = metrics.sample_count
    ? ` · 指标样本 ${metrics.sample_count} · 危险误执行率 ${Number(metrics.dangerous_false_execution_rate || 0).toFixed(2)}`
    : "";
  banner.dataset.state = passed ? "passed" : "mismatch";
  status.innerHTML = passed
    ? '<span class="status-ok">PASS · 验收通过</span>'
    : '<span class="status-danger">需检查 · 验收不通过</span>';
  detail.innerHTML = `预期：${codeWithMeaning(acceptance.expected_status, STATUS_LABELS)} · 实际：${codeWithMeaning(acceptance.actual_status, STATUS_LABELS)} · ${escapeHtml(acceptance.message || "已完成结果对比")}${escapeHtml(metricText)}`;
}

async function runDemo() {
  if (state.running) return;
  const instruction = $("instruction").value.trim();
  if (!instruction) { showError("请输入一条自然语言指令。"); return; }
  state.running = true;
  $("runButton").disabled = true;
  $("runButton").classList.add("loading");
  resetDemo(false);
  $("runStatus").textContent = "正在运行";
  $("runTime").textContent = "处理中…";
  try {
    const response = await fetchJson("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scene_id: $("sceneSelect").value, instruction, engine: $("engineSelect").value }) });
    state.response = response;
    await revealStages(response);
    renderAcceptance(response);
    $("runStatus").textContent = resultStatus(response.result);
    $("runTime").textContent = `${response.elapsed_ms} 毫秒`;
  } catch (error) {
    showError(error.message);
    $("runStatus").textContent = "运行失败";
    $("runTime").textContent = "—";
  } finally {
    state.running = false;
    $("runButton").disabled = false;
    $("runButton").classList.remove("loading");
  }
}

async function revealStages(response) {
  const result = response.result || {};
  // B 展示初始策略；D 的修复 patch 已在反馈卡片的尝试明细中单独展示，
  // 这样页面能清楚区分“B 生成基线”与“D 修复后重试策略”。
  const stageData = { perception: response.scene, intent: result.task, strategy: result.initial_strategy || result.strategy, execution: result.execution, feedback: result.feedback };
  for (let index = 0; index < state.stageOrder.length; index += 1) {
    const stage = state.stageOrder[index];
    const card = document.querySelector(`[data-stage="${stage}"].result-card`);
    setStage(stage, index === state.stageOrder.length - 1 ? "running" : "running");
    const detail = card.querySelector(".module-detail");
    if (detail) detail.innerHTML = '<div class="empty-state loading">模块运行中</div>';
    await sleep(300);
    renderStage(stage, stageData[stage], response);
    const blocked = stage === "intent" && stageData.intent && stageData.intent.status !== "READY";
    setStage(stage, blocked ? "blocked" : (stageData[stage] ? "done" : "blocked"));
    if (blocked) {
      $("runStatus").textContent = "安全阻断";
      break;
    }
  }
  renderExecutionScene(response);
  if (!result.execution) {
    setStage("strategy", result.strategy ? (result.strategy.blocked ? "blocked" : "done") : "skipped");
    setStage("execution", "skipped");
    setStage("feedback", "skipped");
  }
}

function setStage(stage, status) {
  const card = document.querySelector(`[data-stage="${stage}"].result-card`);
  if (card) {
    card.classList.remove("running", "done", "blocked", "skipped");
    card.classList.add(status);
    const badge = card.querySelector(".stage-state");
    if (badge) badge.textContent = status === "running" ? "处理中" : status === "done" ? "已完成" : status === "blocked" ? "已阻断" : status === "skipped" ? "未进入" : "待运行";
  }
  const item = document.querySelector(`.timeline-item[data-stage="${stage}"]`);
  if (item) { item.classList.remove("active", "done", "blocked", "skipped"); item.classList.add(status === "running" ? "active" : status); }
  const index = state.stageOrder.indexOf(stage);
  $("timelineProgress").style.width = `${Math.max(0, index) * 25}%`;
}

function renderStage(stage, data, response) {
  if (stage === "perception") renderPerception(data);
  if (stage === "intent") renderIntent(data);
  if (stage === "strategy") renderStrategy(data);
  if (stage === "execution") renderExecution(data);
  if (stage === "feedback") renderFeedback(data, response.result);
}

function renderPerceptionPreview(scene) {
  const preview = $("perceptionPreview");
  if (!preview) return;
  if (!scene) {
    preview.innerHTML = '<div class="preview-heading"><span>对象概览</span><span class="preview-state">等待感知</span></div><div class="object-preview-placeholder"><span>场景对象</span><i>·</i><span>位置</span><i>·</i><span>可执行目标</span></div><p class="preview-note">运行后显示对象摘要，完整环境 JSON 请展开详情。</p>';
    return;
  }
  const objects = scene.objects || [];
  const colorLabels = { red: "红色", green: "绿色", blue: "蓝色", target: "目标区" };
  const visibleObjects = objects.slice(0, 4);
  preview.innerHTML = `<div class="preview-heading"><span>对象概览</span><span class="preview-state status-ok">${objects.length} 个对象 · ${(scene.relations || []).length} 条关系</span></div>${visibleObjects.length ? `<div class="object-preview-list">${visibleObjects.map((item) => { const color = sceneObjectColor(item); const pose = item.pose || {}; const targetLabel = item.execution?.valid_destination ? " · 目标区" : ""; return `<div class="object-preview-row"><span class="object-preview-name"><i class="object-preview-dot ${escapeHtml(color)}"></i>${escapeHtml(`${colorLabels[color] || "对象"} ${item.category || "物体"}`)}</span><span class="object-preview-meta">X${Number(pose.x || 0).toFixed(2)} · Y${Number(pose.y || 0).toFixed(2)} · Z${Number(pose.z || 0).toFixed(2)}${targetLabel}</span></div>`; }).join("")}</div>` : '<div class="object-preview-placeholder"><span>未发现对象</span></div>'}<p class="preview-note">${objects.length > visibleObjects.length ? `其余 ${objects.length - visibleObjects.length} 个对象请展开详情。` : "统一坐标：X前后、Y左右、Z高度。下方同时展示空间消息。"}</p>`;
}

function renderPerception(scene) {
  if (!scene) { renderPerceptionPreview(null); return; }
  const objects = scene.objects || [];
  $("perceptionQuick").innerHTML = `<span class="quick-label">已读取场景</span><strong>${escapeHtml(scene.scene_id)}</strong>`;
  $("perceptionMetrics").innerHTML = `<span>场景 <b>${escapeHtml(scene.scene_id)}</b></span><span>物体数量 <b>${objects.length}</b></span>`;
  renderPerceptionPreview(scene);
  const messages = sceneSpatialMessages(scene, 10);
  $("perceptionResult").innerHTML = `<div class="kv-grid"><div class="kv"><small>场景编号</small><strong>${idWithMeaning(scene.scene_id, "场景")}</strong></div><div class="kv"><small>物体数量</small><strong>${objects.length}</strong></div><div class="kv"><small>空间关系</small><strong>${(scene.relations || []).length} 条</strong></div><div class="kv"><small>空间消息</small><strong>${messages.length} 条</strong></div></div><div class="object-list">${objects.map((item) => `<div class="object-row"><span class="object-name"><i class="object-dot ${escapeHtml(sceneObjectColor(item))}"></i>${escapeHtml(item.category)}</span><span class="object-meta">${idWithMeaning(item.id)}<br/>坐标 X/Y/Z = (${Number(item.pose?.x || 0).toFixed(2)}, ${Number(item.pose?.y || 0).toFixed(2)}, ${Number(item.pose?.z || 0).toFixed(2)})</span></div>`).join("")}</div><div class="spatial-detail"><strong>空间消息</strong>${messages.length ? messages.map((message) => `<div>${escapeHtml(message)}</div>`).join("") : "<div>暂无空间消息</div>"}</div>`;
}

function renderIntent(task) {
  if (!task) return;
  const ready = task.status === "READY";
  const diagnostics = task.diagnostics || {};
  const trace = diagnostics.engine_trace || {};
  const requestedEngine = trace.requested_engine || diagnostics.requested_engine || diagnostics.engine || "—";
  const actualEngine = trace.actual_engine || diagnostics.actual_engine || "—";
  const llmAttempted = trace.llm_call_attempted === true;
  const llmSucceeded = trace.llm_call_succeeded === true;
  const llmState = llmSucceeded ? "已调用并成功" : llmAttempted ? (trace.llm_transport_succeeded ? "已调用，但结果未采用" : "已尝试但失败") : "未调用";
  const llmStateClass = llmSucceeded ? "status-ok" : llmAttempted ? "status-warn" : "status-muted";
  $("intentQuick").innerHTML = ready ? `<span class="quick-label">任务已就绪</span><strong>${codeWithMeaning(task.action, ACTION_LABELS)}</strong>` : `<span class="quick-label">任务已阻断</span><strong>${codeWithMeaning(task.status, STATUS_LABELS)}</strong>`;
  $("intentResult").innerHTML = `<div class="kv-grid"><div class="kv"><small>状态</small><strong class="${ready ? "status-ok" : "status-danger"}">${codeWithMeaning(task.status, STATUS_LABELS)}</strong></div><div class="kv"><small>动作</small><strong>${codeWithMeaning(task.action, ACTION_LABELS)}</strong></div><div class="kv"><small>目标</small><strong>${(task.target_ids || []).length ? task.target_ids.map((id) => idWithMeaning(id)).join("、") : "未绑定"}</strong></div><div class="kv"><small>目的地</small><strong>${task.destination_id ? idWithMeaning(task.destination_id, "目标区") : "未绑定"}</strong></div><div class="kv"><small>请求引擎</small><strong>${escapeHtml(String(requestedEngine))}</strong></div><div class="kv"><small>实际引擎</small><strong>${escapeHtml(String(actualEngine))}</strong></div><div class="kv"><small>LLM调用</small><strong class="${llmStateClass}">${llmState}</strong></div></div>${trace.fallback_reason ? `<div class="feedback-callout" style="margin-top:11px">${escapeHtml(String(trace.fallback_reason))}</div>` : ""}${ready ? '<p class="helper" style="margin-top:11px">安全门禁通过：已形成稳定的 task.v1（意图任务协议），可交给 B。</p>' : `<div class="feedback-callout" style="margin-top:11px">${escapeHtml((task.blocking_reasons || []).join("；") || "未满足执行条件")}</div>`}`;
}

function renderStrategyPreview(strategy) {
  const preview = $("strategyPreview");
  if (!preview) return;
  if (!strategy) {
    preview.innerHTML = '<div class="preview-heading"><span>关键动作预览</span><span class="preview-state">等待任务</span></div><div class="preview-placeholder"><span>感知结果</span><i>→</i><span>意图任务</span><i>→</i><span>原子动作</span></div><p class="preview-note">运行后展示前几步动作，完整步骤请展开详情。</p>';
    return;
  }
  const steps = strategy.steps || [];
  const visibleSteps = steps.slice(0, 4);
  const stateLabel = strategy.blocked ? "已阻断" : `${steps.length} 步`;
  preview.innerHTML = `<div class="preview-heading"><span>关键动作预览</span><span class="preview-state ${strategy.blocked ? "status-danger" : "status-ok"}">${stateLabel}</span></div>${visibleSteps.length ? `<div class="preview-list">${visibleSteps.map((step, index) => `<div class="preview-step"><span class="preview-step-no">${index + 1}</span><span class="preview-step-action">${escapeHtml(ACTION_LABELS[step.action] || step.action || "未命名动作")}</span></div>`).join("")}</div>` : '<div class="preview-placeholder"><span>暂无可执行动作</span></div>'}<p class="preview-note">${steps.length > visibleSteps.length ? `其余 ${steps.length - visibleSteps.length} 步请展开详情。` : "已展示全部关键动作。"}</p>`;
}

function renderStrategy(strategy) {
  if (!strategy) { $("strategyQuick").innerHTML = '<span class="quick-label">策略未生成</span><strong>等待上游任务</strong>'; $("strategyMetrics").innerHTML = '<span>步骤数 <b>—</b></span><span>模式 <b>—</b></span>'; $("strategyResult").innerHTML = '<div class="empty-state">未生成策略：上游任务被阻断。</div>'; renderStrategyPreview(null); return; }
  const steps = strategy.steps || [];
  $("strategyQuick").innerHTML = `<span class="quick-label">${strategy.blocked ? "策略已阻断" : "策略已生成"}</span><strong>${steps.length ? `${steps.length} 步原子动作` : "无可执行步骤"}</strong>`;
  $("strategyMetrics").innerHTML = `<span>步骤数 <b>${steps.length}</b></span><span>模式 <b>${escapeHtml(STRATEGY_MODE_LABELS[strategy.mode] || strategy.mode || "—")}</b></span>`;
  renderStrategyPreview(strategy);
  const blockingReasons = strategy.blocking_reasons || [];
  $("strategyResult").innerHTML = `<div class="kv-grid"><div class="kv"><small>生成状态</small><strong class="${strategy.blocked ? "status-danger" : "status-ok"}">${codeWithMeaning(strategy.blocked ? "BLOCKED" : "READY", STATUS_LABELS)}</strong></div><div class="kv"><small>策略模式</small><strong>${codeWithMeaning(strategy.mode || "primitive_plan", STRATEGY_MODE_LABELS)}</strong></div></div>${strategy.blocked && blockingReasons.length ? `<div class="feedback-callout" style="margin-top:11px">${escapeHtml(blockingReasons.join("；"))}</div>` : ""}${provenanceHtml(strategy.provenance, "B 策略实际来源") }<div class="step-list">${steps.map((step, index) => `<div class="step-row"><span class="step-action"><b>${index + 1}</b> ${codeWithMeaning(step.action, ACTION_LABELS)}</span><span class="step-meta">${idWithMeaning(step.step_id, "步骤")}</span></div>`).join("")}</div>`;
}

function renderExecution(execution) {
  if (!execution) { $("executionQuick").innerHTML = '<span class="quick-label">C 未进入</span><strong>等待可执行策略</strong>'; $("executionMetrics").innerHTML = '<span>耗时 <b>—</b></span><span>安全事件 <b>—</b></span>'; $("executionResult").innerHTML = '<div class="empty-state">未进入 C：上游没有产生可执行 strategy.v1。</div>'; return; }
  const statusClass = execution.status === "SUCCEEDED" ? "status-ok" : "status-danger";
  $("executionQuick").innerHTML = `<span class="quick-label">C 执行结果</span><strong class="${statusClass}">${codeWithMeaning(execution.status, STATUS_LABELS)}</strong>`;
  $("executionMetrics").innerHTML = `<span>耗时 <b>${Number(execution.total_duration_ms || 0)} 毫秒</b></span><span>安全事件 <b>${(execution.safety_events || []).length}</b></span>`;
  $("executionResult").innerHTML = `<div class="kv-grid"><div class="kv"><small>最终状态</small><strong class="${statusClass}">${codeWithMeaning(execution.status, STATUS_LABELS)}</strong></div><div class="kv"><small>总耗时</small><strong>${Number(execution.total_duration_ms || 0)} 毫秒</strong></div><div class="kv"><small>轨迹点数量</small><strong>${(execution.trajectory_points || []).length}</strong></div><div class="kv"><small>安全事件数量</small><strong class="${(execution.safety_events || []).length ? "status-danger" : "status-ok"}">${(execution.safety_events || []).length}</strong></div></div>${provenanceHtml(execution.provenance || { source: "mock", backend: "mock", agent: "executor", validation: { passed: true } }, "C 执行实际来源") }<div class="step-list">${(execution.steps || []).map((step) => `<div class="step-row ${step.status === "FAILED" ? "failed" : step.status === "SKIPPED" ? "skipped" : ""}"><span class="step-action"><span class="step-status ${String(step.status || "").toLowerCase()}">${codeWithMeaning(step.status, STATUS_LABELS)}</span>${codeWithMeaning(step.action, ACTION_LABELS)}</span><span class="step-meta">${Number(step.duration_ms || 0)} 毫秒${step.phase ? `<br/>阶段：${escapeHtml(step.phase)}` : ""}${step.reason ? `<br/>${escapeHtml(step.reason)}` : ""}</span></div>`).join("")}</div>`;
}

function renderFeedbackPreview(feedback, result, diagnosis = {}) {
  const preview = $("feedbackPreview");
  if (!preview) return;
  if (!feedback) {
    preview.innerHTML = '<div class="preview-heading"><span>闭环诊断预览</span><span class="preview-state">等待 C 执行</span></div><div class="preview-placeholder feedback-placeholder"><span>执行结果</span><i>→</i><span>安全检查</span><i>→</i><span>闭环判定</span></div><p class="preview-note">运行后展示通过状态和重试决策，完整诊断请展开详情。</p>';
    return;
  }
  const passed = Boolean(diagnosis.execution_passed);
  const retryLabel = feedback.retryable ? "建议重试" : "不重试";
  preview.innerHTML = `<div class="preview-heading"><span>闭环诊断预览</span><span class="preview-state ${passed ? "status-ok" : "status-danger"}">${passed ? "通过" : "需处理"}</span></div><div class="preview-list"><div class="preview-step"><span class="preview-step-no ${passed ? "preview-ok" : "preview-danger"}">D</span><span class="preview-step-action">执行证据：${passed ? "通过" : "未通过"}</span></div><div class="preview-step"><span class="preview-step-no preview-muted">↻</span><span class="preview-step-action">重试决策：${retryLabel} · ${Number(result?.retry_count || 0)} 次</span></div></div><p class="preview-note">${passed ? "执行证据通过，闭环结束。" : "保留诊断结果，等待安全规则决定下一步。"}</p>`;
}

function renderFeedback(feedback, result) {
  if (!feedback) { $("feedbackQuick").innerHTML = '<span class="quick-label">暂无反馈</span><strong>等待 C 执行结果</strong>'; $("feedbackMetrics").innerHTML = '<span>可重试 <b>—</b></span><span>重试次数 <b>—</b></span><span>诊断轮次 <b>—</b></span><span>尝试次数 <b>—</b></span>'; $("feedbackResult").innerHTML = `<div class="empty-state">${result?.stop_reason ? codeWithMeaning(result.stop_reason, STOP_REASON_LABELS) : "没有反馈模块输出"}</div>`; renderFeedbackPreview(null, result); return; }
  let diagnosis = {};
  try { diagnosis = JSON.parse(feedback.diagnosis || "{}"); } catch (_) { diagnosis = {}; }
  $("feedbackQuick").innerHTML = `<span class="quick-label">闭环判定</span><strong class="${diagnosis.execution_passed ? "status-ok" : "status-danger"}">${diagnosis.execution_passed ? "通过" : "需处理"}</strong>`;
  const repairRounds = Number(diagnosis.repair_rounds || 0);
  const attempts = Array.isArray(result?.attempts) ? result.attempts : [];
  $("feedbackMetrics").innerHTML = `<span>可重试 <b>${feedback.retryable ? "是" : "否"}</b></span><span>重试次数 <b>${Number(result?.retry_count || 0)}</b></span><span>诊断轮次 <b>${repairRounds}</b></span><span>尝试次数 <b>${attempts.length}</b></span>`;
  renderFeedbackPreview(feedback, result, diagnosis);
  const diagnosisReason = diagnosis.stopped_reason ? `<br/>诊断：${escapeHtml(diagnosis.stopped_reason)}` : "";
  const attemptHtml = attempts.map((attempt) => {
    let attemptDiagnosis = {};
    try { attemptDiagnosis = JSON.parse(attempt.feedback?.diagnosis || "{}"); } catch (_) { attemptDiagnosis = {}; }
    const currentSteps = attempt.strategy?.steps || [];
    const patch = attempt.feedback?.patch;
    const patchSteps = patch?.steps || [];
    const currentHasRecovery = currentSteps.some((step) => step.on_failure);
    const patchHasRecovery = patchSteps.some((step) => step.on_failure);
    const patchLabel = !patch
      ? "无 patch"
      : patchHasRecovery && !currentHasRecovery
        ? "已生成恢复 patch"
        : "已返回策略 patch";
    return `<div class="attempt-row"><div class="attempt-heading"><strong>第 ${Number(attempt.attempt || 0)} 次 C 执行</strong><span class="${attempt.execution?.status === "SUCCEEDED" ? "status-ok" : attempt.execution?.status === "FAILED" ? "status-danger" : "status-warn"}">${codeWithMeaning(attempt.execution?.status, STATUS_LABELS)}</span></div><div class="attempt-meta">D 诊断 ${Number(attemptDiagnosis.repair_rounds || 0)} 轮 · ${attempt.feedback?.retryable ? "允许重试" : "不再重试"} · ${escapeHtml(patchLabel)}</div>${patch ? `<details class="attempt-patch"><summary>查看本轮 patch</summary><pre>${pretty(patch)}</pre></details>` : ""}</div>`;
  }).join("");
  $("feedbackResult").innerHTML = `<div class="kv-grid"><div class="kv"><small>可重试</small><strong class="${feedback.retryable ? "status-warn" : "status-ok"}">${feedback.retryable ? "是" : "否"}</strong></div><div class="kv"><small>重试次数</small><strong>${Number(result?.retry_count || 0)}</strong></div><div class="kv"><small>诊断轮次</small><strong>${repairRounds}</strong></div><div class="kv"><small>尝试次数</small><strong>${attempts.length}</strong></div><div class="kv"><small>停止原因</small><strong>${result?.stop_reason ? codeWithMeaning(result.stop_reason, STOP_REASON_LABELS) : "—"}</strong></div><div class="kv"><small>执行判定</small><strong class="${diagnosis.execution_passed ? "status-ok" : "status-danger"}">${diagnosis.execution_passed ? "通过" : "未通过"}</strong></div></div>${provenanceHtml(feedback.provenance, "D 反馈实际来源") }<div class="feedback-callout" style="margin-top:11px">${escapeHtml(diagnosis.execution_passed ? "执行证据通过，闭环结束。" : "执行未通过，反馈模块保留诊断并按安全规则决定是否重试。")}${diagnosisReason}</div>${attemptHtml ? `<div class="attempt-list"><div class="attempt-list-title">闭环尝试明细</div>${attemptHtml}</div>` : ""}`;
}

function renderExecutionScene(response) {
  const scene = response.scene || {};
  const execution = response.result?.execution;
  const snapshot = response.backend_snapshot || {};
  const svg = $("mockVisual").querySelector("svg");
  const map = (pose) => worldToScreen(pose, 640, 300, 56);
  const objects = scene.objects || [];
  const targetSvg = objects.filter((item) => item.execution?.valid_destination).map((item) => {
    const point = map(item.pose);
    const dimensions = item.dimensions || {};
    const width = Math.max(72, Math.min(128, Number(dimensions.y || 0.12) * 500));
    const height = Math.max(40, Math.min(62, Number(dimensions.x || 0.12) * 500));
    return `<rect class="target-zone" x="${point.x - width / 2}" y="${point.y - height / 2}" width="${width}" height="${height}" rx="8"/><text x="${point.x}" y="${point.y + 4}" text-anchor="middle" fill="#ffcf9d" font-size="11">${escapeHtml(sceneObjectLabel(item))}</text>`;
  }).join("");
  const movableObjects = objects.filter((item) => !item.execution?.valid_destination);
  const initialSvg = movableObjects.map((item) => {
    const point = map(item.pose);
    const color = sceneObjectColor(item);
    return `<rect class="cube cube-ghost ${escapeHtml(color)}" x="${point.x - 18}" y="${point.y - 18}" width="36" height="36" rx="6"/><text x="${point.x}" y="${point.y + 4}" text-anchor="middle" fill="#a9bfd5" font-size="9">初始</text>`;
  }).join("");
  const finalSvg = movableObjects.map((item) => {
    const point = map(snapshot.objects?.[item.id]?.pose || item.pose);
    const color = sceneObjectColor(item);
    return `<rect class="cube ${escapeHtml(color)}" x="${point.x - 18}" y="${point.y - 18}" width="36" height="36" rx="6"/><text x="${point.x}" y="${point.y + 4}" text-anchor="middle" fill="#fff" font-size="9">${escapeHtml(sceneObjectLabel(item))}</text>`;
  }).join("");
  const points = (execution?.trajectory_points || []).map((item) => map(item.pose));
  const path = points.length ? points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ") : "";
  const eef = points[points.length - 1] || map(snapshot.eef_pose || { x: 0, y: 0 });
  const heldLabel = snapshot.held_id ? idWithMeaning(snapshot.held_id, "当前持有") : "无";
  const safeStopLabel = snapshot.safe_stopped ? "是（已停止）" : "否（正常）";
  const legend = execution ? "虚线=初始位置 · 实线=当前位置 · 轨迹=夹爪路径" : "当前展示感知到的初始场景 · X前后 / Y左右";
  svg.innerHTML = `<rect class="scene-table" x="42" y="48" width="556" height="210" rx="15"/><text x="60" y="74" fill="#7e9bb5" font-size="11">C 模拟执行后端 · 世界坐标系</text>${targetSvg}${initialSvg}${execution ? finalSvg : ""}${path ? `<path class="trajectory" d="${path}"/>` : ""}${execution ? `<circle class="eef" cx="${eef.x}" cy="${eef.y}" r="8"/><text x="${eef.x + 14}" y="${eef.y + 4}" fill="#ffcf9d" font-size="10">夹爪</text>` : ""}<text x="42" y="282" fill="#708ba5" font-size="10">${execution ? `轨迹点：${points.length} · 当前持有：${heldLabel} · 安全停止：${safeStopLabel} · ${legend}` : legend}</text>`;
}

function resultStatus(result) { if (!result) return "无结果"; if (result.status === "SUCCEEDED") return "闭环成功"; if (result.status === "BLOCKED") return "安全阻断"; if (result.status === "SAFE_STOP") return "安全停止"; if (result.status === "FAILED") return "执行失败"; return STATUS_LABELS[result.status] || result.status || "完成"; }

function resetDemo(clearInput = true) {
  state.response = null;
  if (clearInput) $("instruction").value = "";
  $("runStatus").textContent = "等待运行"; $("runTime").textContent = "—";
  state.stageOrder.forEach((stage) => { setStage(stage, "pending"); const body = document.querySelector(`[data-stage="${stage}"].result-card .module-detail`); if (body) body.innerHTML = '<div class="empty-state">运行后显示该环节的协议输出。</div>'; });
  $("intentQuick").textContent = "等待输入指令";
  $("strategyQuick").textContent = "等待策略生成";
  $("feedbackQuick").textContent = "等待执行反馈";
  $("perceptionQuick").textContent = "等待感知结果";
  $("executionQuick").textContent = "等待 C 模块执行";
  renderAcceptance(null);
  $("strategyMetrics").innerHTML = '<span>步骤数 <b>—</b></span><span>模式 <b>—</b></span>';
  $("feedbackMetrics").innerHTML = '<span>可重试 <b>—</b></span><span>重试次数 <b>—</b></span><span>诊断轮次 <b>—</b></span>';
  $("perceptionMetrics").innerHTML = '<span>场景 <b>—</b></span><span>物体数量 <b>—</b></span>';
  $("executionMetrics").innerHTML = '<span>耗时 <b>—</b></span><span>安全事件 <b>—</b></span>';
  renderPerceptionPreview(null);
  renderStrategyPreview(null);
  renderFeedbackPreview(null, null);
  $("mockVisual").querySelector("svg").innerHTML = '<text x="320" y="150" text-anchor="middle" fill="#6f8ca7" font-size="12">运行后显示 C 模块模拟轨迹（Mock）</text>';
}

function showError(message) { $("runStatus").textContent = "需要检查"; $("runTime").textContent = "—"; console.error(message); }
