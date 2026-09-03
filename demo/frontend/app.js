/* API-backed cockpit UI. The workflow and livestream contracts are unchanged.
 * Endpoints: /api/runs, /api/livestream (same-origin).
 * Local dev convenience: if(location.protocol==="file:")apiBase="http://127.0.0.1:8765" */
(function () {
  "use strict";

  var apiBase = "/api";
  var DEFAULT_HLS_URL = "/live/isaac/index.m3u8";
  if(location.protocol==="file:")apiBase="http://127.0.0.1:8765";

  var state = {
    runId: null,
    afterSequence: 0,
    loopActive: false,
    runRequestInFlight: false,
    eventRequestInFlight: false,
    submitInFlight: false,
    terminal: false,
    hls: null,
    live: false,
    session: null,
    currentView: "home",
  };

  function $(id) { return document.getElementById(id); }

  function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || "GET", credentials: "same-origin" };
    if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    var url = apiBase + (path.charAt(0) === "/" ? path : "/" + path);
    return fetch(url, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var error = new Error(data.error || ("HTTP " + response.status));
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function toast(message) {
    var node = $("toast");
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(function () { node.hidden = true; }, 4200);
  }

  function statusText(value) {
    var map = { ok: "正常", online: "正常", healthy: "正常", offline: "离线", degraded: "降级", not_probed: "未探测", unknown: "未知" };
    return map[String(value || "unknown").toLowerCase()] || String(value || "未知");
  }

  function setHealthBadge(key, value) {
    var text = statusText(value);
    var top = document.querySelector('#systemHealth [data-component="' + key + '"]');
    var row = document.querySelector('[data-health-row="' + key + '"]');
    [top, row].forEach(function (node) {
      if (!node) return;
      var label = node.querySelector("b");
      if (label) label.textContent = text;
      node.classList.remove("ok", "warn", "bad");
      var normalized = String(value || "").toLowerCase();
      node.classList.add(normalized === "ok" || normalized === "online" || normalized === "healthy" ? "ok" : normalized === "degraded" || normalized === "not_probed" ? "warn" : "bad");
    });
  }

  function renderHealth() {
    return api("health").then(function (data) {
      var components = data.components || {};
      setHealthBadge("cloud", (components.cloud || {}).status);
      setHealthBadge("relay", (components.relay || {}).status);
      setHealthBadge("isaac", (components.isaac || {}).status);
      setHealthBadge("livestream", (components.livestream || {}).status);
      setHealthBadge("providers", (components.providers || {}).status);
      if ($("footerNote")) $("footerNote").textContent = "健康检查 · " + statusText(data.status);
    }).catch(function () {
      ["cloud", "relay", "isaac", "livestream", "providers"].forEach(function (key) { setHealthBadge(key, "offline"); });
      if ($("footerNote")) $("footerNote").textContent = "健康检查失败";
    });
  }

  function renderScenarios() {
    return api("scenarios").then(function (data) {
      var list = data.scenarios || [];
      var container = $("scenarioList");
      if (!container) return;
      container.innerHTML = "";
      if (!list.length) { container.innerHTML = '<p class="muted">暂无已验证场景</p>'; return; }
      list.forEach(function (scenario, index) {
        var label = document.createElement("label");
        label.className = "scenario-item" + (index === 0 ? " selected" : "");
        var radio = document.createElement("input");
        radio.type = "radio"; radio.name = "scenario"; radio.value = scenario.id; radio.checked = index === 0;
        var span = document.createElement("span");
        span.textContent = scenario.name || scenario.id;
        label.appendChild(radio); label.appendChild(span); container.appendChild(label);
        radio.addEventListener("change", function () {
          document.querySelectorAll(".scenario-item").forEach(function (item) { item.classList.remove("selected"); });
          label.classList.add("selected");
          if (scenario.instruction && $("instruction")) $("instruction").value = scenario.instruction;
          updateInstructionCount();
        });
      });
      if (list[0].instruction && $("instruction")) { $("instruction").value = list[0].instruction; updateInstructionCount(); }
    }).catch(function () {
      if ($("scenarioList")) $("scenarioList").innerHTML = '<p class="muted">场景目录不可用</p>';
    });
  }

  function isAdminSession() {
    return Boolean(
      state.session &&
      state.session.authenticated &&
      state.session.role === "admin"
    );
  }

  function syncConfigAccess() {
    var isAdmin = isAdminSession();

    document.querySelectorAll("#modelConfigForm [data-module]").forEach(function (control) {
      control.disabled = !isAdmin;
    });

    if ($("saveConfigBtn")) {
      $("saveConfigBtn").disabled = !isAdmin;
    }

    if ($("configMessage")) {
      $("configMessage").textContent = isAdmin
        ? "管理员模式：可修改 A/D API Key 与模型配置"
        : "比赛公开模式：配置只读，仅管理员可保存配置";
    }
  }

  function setSessionFields(session) {
    var authenticated = Boolean(session && session.authenticated);
    var user = authenticated ? (session.user || "未知用户") : "未登录";
    var role = authenticated ? (session.role || "—") : "—";
    var openAccess = Boolean(session && session.demo_open_access);
    var isAdmin = authenticated && role === "admin";
    var canLogout = authenticated && (isAdmin || !openAccess);

    if ($("sessionState")) {
      $("sessionState").textContent =
        authenticated ? role + " · " + user : "未登录";
    }

    if ($("sessionRowState")) {
      $("sessionRowState").textContent =
        authenticated ? "正常" : "未登录";
    }

    if ($("userName")) $("userName").textContent = user;
    if ($("userRole")) $("userRole").textContent = role;

    if ($("userSessionStatus")) {
      $("userSessionStatus").textContent = isAdmin
        ? "管理员会话"
        : authenticated
          ? "已登录"
          : "未登录";
    }

    if ($("loginBtn")) {
      $("loginBtn").hidden = isAdmin || (authenticated && !openAccess);
      $("loginBtn").textContent =
        openAccess && !isAdmin ? "管理员登录" : "登录";
    }

    if ($("logoutBtn")) {
      $("logoutBtn").hidden = !canLogout;
      $("logoutBtn").textContent = isAdmin ? "退出管理员" : "退出";
    }

    if ($("userLogoutBtn")) {
      $("userLogoutBtn").hidden = !canLogout;
      $("userLogoutBtn").textContent =
        isAdmin ? "退出管理员" : "退出登录";
    }

    if ($("run")) {
      $("run").disabled =
        !authenticated || state.submitInFlight || state.loopActive;
    }

    if ($("runHint")) {
      $("runHint").textContent =
        authenticated
          ? "将以 " + role + " 身份执行"
          : "需要登录后才能执行";
    }

    setHealthBadge("session", authenticated ? "online" : "offline");
    syncConfigAccess();
  }

  function renderSession() {
    return api("session")
      .then(function (session) {
        state.session = session;
        setSessionFields(session);
        return session;
      })
      .catch(function () {
        state.session = { authenticated: false };
        setSessionFields(state.session);
      });
  }

  function login() {
    var user = window.prompt("管理员用户名", "admin");
    if (!user) return;

    var password = window.prompt("管理员密码");
    if (!password) return;

    api("login", {
      method: "POST",
      body: {
        user: user,
        password: password
      }
    })
      .then(function () {
        return renderSession();
      })
      .then(function () {
        toast(
          isAdminSession()
            ? "管理员登录成功"
            : "登录成功"
        );
      })
      .catch(function (error) {
        toast("登录失败: " + error.message);
      });
  }

  function logout() {
    api("logout", {
      method: "POST",
      body: {}
    })
      .then(function () {
        state.session = null;
        return renderSession();
      })
      .then(function () {
        toast(
          state.session &&
          state.session.demo_open_access
            ? "已退出管理员，恢复比赛 operator"
            : "已退出登录"
        );
      })
      .catch(renderSession);
  }

  function markLivestream(value) {
    var node = $("livestreamStatus");
    if (!node) return;
    node.className = "live-badge " + String(value || "offline").toLowerCase();
    node.innerHTML = "<i></i>" + value;
  }

  function initLivestream(url) {
    var video = $("livestream");
    if (!video || video.dataset.initialized === "true") return;
    video.dataset.initialized = "true";
    var message = $("livestreamMsg");
    if (!url) { if (message) message.textContent = "直播地址未配置"; markLivestream("OFFLINE"); return; }
    video.addEventListener("timeupdate", function () { if (video.currentTime > 0) { state.live = true; markLivestream("LIVE"); if (message) message.textContent = ""; } });
    video.addEventListener("error", function () { if (!state.live) markLivestream("OFFLINE"); if (message) message.textContent = "直播暂不可用"; });
    if (window.Hls && window.Hls.isSupported()) {
      var hls = new window.Hls({ liveSyncDurationCount: 2 });
      hls.loadSource(url); hls.attachMedia(video); state.hls = hls;
      hls.on(window.Hls.Events.ERROR, function (_event, data) { if (data && data.fatal) { markLivestream("RECONNECTING"); if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) window.setTimeout(function () { hls.startLoad(); }, 3000); } });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      video.addEventListener("loadedmetadata", function () { markLivestream("CONNECTING"); });
    } else { if (message) message.textContent = "浏览器不支持 HLS"; markLivestream("OFFLINE"); }
  }

  function selectedScene() { var checked = document.querySelector('input[name="scenario"]:checked'); return checked ? checked.value : ""; }
  function updateInstructionCount() { if ($("instructionCount") && $("instruction")) $("instructionCount").textContent = $("instruction").value.length + " / 500"; }

  function resetStages() {
    ["A", "B", "C", "D"].forEach(function (letter) { var stage = $("stage" + letter); var status = $("stage" + letter + "State"); var detail = $("stage" + letter + "Detail"); if (stage) stage.className = "stage"; if (status) { status.className = "stage-state pending"; status.textContent = "等待中"; } if (detail) detail.textContent = "等待任务"; });
  }

  function setStage(letter, value, detail) {
    var stage = $("stage" + letter); var status = $("stage" + letter + "State"); var detailNode = $("stage" + letter + "Detail");
    if (!stage || !status) return;
    var failed = ["失败", "阻断", "安全停止", "已取消"].indexOf(value) >= 0;
    var done = value === "已完成";
    stage.className = "stage " + (failed ? "failed" : done ? "done" : value === "进行中" ? "running" : "");
    status.className = "stage-state " + (failed ? "failed" : done ? "done" : value === "进行中" ? "running" : "pending");
    status.textContent = value;
    if (detail && detailNode) detailNode.textContent = detail;
  }

  function renderStagesFromState(runState, runStage) {
    var map = { UNDERSTANDING: "A", PLANNING: "B", QUEUED_C: "C", EXECUTING: "C", VERIFYING: "D" };
    var target = map[runState] || map[runStage];
    if (runState === "SUCCEEDED") { ["A", "B", "C", "D"].forEach(function (letter) { setStage(letter, "已完成"); }); return; }
    if (["FAILED", "BLOCKED", "SAFE_STOPPED", "CANCELLED"].indexOf(runState) >= 0) { var failedLetter = target || map[runStage] || "A"; setStage(failedLetter, runState === "BLOCKED" ? "阻断" : runState === "SAFE_STOPPED" ? "安全停止" : runState === "CANCELLED" ? "已取消" : "失败"); return; }
    if (!target) return;
    ["A", "B", "C", "D"].forEach(function (letter, index) { var targetIndex = ["A", "B", "C", "D"].indexOf(target); if (index < targetIndex) setStage(letter, "已完成"); else if (index === targetIndex) setStage(letter, "进行中"); else setStage(letter, "等待中"); });
  }

  function renderStagesFromEvent(event) {
    var type = event.type || ""; var payload = event.payload || {}; var message = payload.message ? String(payload.message) : "";
    var mapping = { A_STARTED: "A", B_STARTED: "B", C_EXECUTION_QUEUED: "C", C_EVIDENCE_VERIFYING: "D", D_REPAIR_QUEUED: "D" };
    if (type === "RUN_SUCCEEDED") { ["A", "B", "C", "D"].forEach(function (letter) { setStage(letter, "已完成", message); }); return; }
    if (mapping[type]) setStage(mapping[type], "进行中", message);
  }

  function renderSnapshot(run) {
    if (!run) return;
    var runState = run.state || "无数据";
    if ($("runId")) $("runId").textContent = run.run_id || "—";
    if ($("runStateBadge")) { $("runStateBadge").textContent = runState; $("runStateBadge").className = "state-badge " + (["SUCCEEDED"].indexOf(runState) >= 0 ? "success" : ["FAILED", "BLOCKED", "SAFE_STOPPED", "CANCELLED"].indexOf(runState) >= 0 ? "failed" : "running"); }
    if ($("currentAction")) $("currentAction").textContent = run.current_action || "无数据";
    if ($("runTarget")) $("runTarget").textContent = run.scene_id || "无数据";
    if ($("safetyEvent")) $("safetyEvent").textContent = run.error_message || run.error_code || "无数据";
    if ($("repairAttempts")) $("repairAttempts").textContent = run.repair_attempts == null ? "无数据" : String(run.repair_attempts);
    var terminal = ["SUCCEEDED", "FAILED", "SAFE_STOPPED", "BLOCKED", "CANCELLED"].indexOf(runState) >= 0;
    if ($("resultSummary")) { $("resultSummary").textContent = runState + (run.result ? " · " + run.result : ""); $("resultSummary").className = terminal ? (runState === "SUCCEEDED" ? "result-ok" : "result-bad") : "result-wait"; }
    if ($("evidenceSummary")) $("evidenceSummary").textContent = run.audit_eligible ? "证据审计通过" : terminal ? "暂无审计证据" : "等待验证证据";
    renderStagesFromState(runState, run.stage);
    if (terminal) { state.terminal = true; state.loopActive = false; if ($("run")) $("run").disabled = !(state.session && state.session.authenticated); }
  }

  function appendEvent(event) {
    var timeline = $("eventTimeline"); if (!timeline) return;
    var placeholder = timeline.querySelector(".muted"); if (placeholder) timeline.innerHTML = "";
    var row = document.createElement("p"); var time = document.createElement("time"); time.textContent = event.created_at || ""; var text = document.createElement("span"); text.textContent = "[" + (event.sequence || "?") + "] " + (event.type || "event") + (event.payload && event.payload.message ? " · " + event.payload.message : ""); row.appendChild(time); row.appendChild(text); timeline.appendChild(row); timeline.scrollTop = timeline.scrollHeight;
  }

  function pollRun() {
    if (!state.runId || state.runRequestInFlight) return;
    state.runRequestInFlight = true;
    api("runs/" + encodeURIComponent(state.runId)).then(function (data) { renderSnapshot(data.run || {}); }).catch(function (error) { if (error.status === 404 && $("runStateBadge")) $("runStateBadge").textContent = "运行不存在"; }).finally(function () { state.runRequestInFlight = false; });
  }

  function pollEvents() {
    if (!state.runId || state.eventRequestInFlight) return;
    state.eventRequestInFlight = true;
    api("runs/" + encodeURIComponent(state.runId) + "/events?after_sequence=" + state.afterSequence).then(function (data) { (data.events || []).forEach(function (event) { state.afterSequence = Math.max(state.afterSequence, Number(event.sequence || 0)); appendEvent(event); renderStagesFromEvent(event); }); }).catch(function () {}).finally(function () { state.eventRequestInFlight = false; });
  }

  function startPolling() {
    if (state.loopActive) return;
    state.loopActive = true; state.submitInFlight = false; if ($("run")) $("run").disabled = true;
    function tick() { if (!state.runId || state.terminal) { state.loopActive = false; if ($("run")) $("run").disabled = !(state.session && state.session.authenticated); return; } pollRun(); pollEvents(); window.setTimeout(tick, 1500); }
    tick();
  }

  function startRun() {
    if (state.submitInFlight || state.loopActive) return;
    var instruction = $("instruction") ? $("instruction").value.trim() : ""; var sceneId = selectedScene();
    if (!sceneId) { toast("请先选择场景"); return; } if (!instruction) { toast("请输入任务指令"); return; }
    state.submitInFlight = true; if ($("run")) $("run").disabled = true; resetStages();
    api("runs", { method: "POST", body: { scene_id: sceneId, instruction: instruction } }).then(function (data) { var run = data.run || {}; state.runId = run.run_id || run.id; if (!state.runId) throw new Error("no run_id in response"); state.afterSequence = 0; state.terminal = false; try { localStorage.setItem("closed_loop_run_id", state.runId); } catch (error) {} if ($("eventTimeline")) $("eventTimeline").innerHTML = '<p class="muted">等待运行事件…</p>'; toast("任务已创建: " + state.runId); startPolling(); }).catch(function (error) { state.submitInFlight = false; if ($("run")) $("run").disabled = !(state.session && state.session.authenticated); toast(error.status === 401 ? "需要登录" : "创建失败: " + error.message); if (error.status === 401) renderSession(); });
  }

  function resumeLastRun() { try { var runId = localStorage.getItem("closed_loop_run_id"); if (runId) { state.runId = runId; state.terminal = false; startPolling(); } } catch (error) {} }

  function renderConfig(data) {
    var config = data.config || {};
    var modules = config.modules || {};
    var container = $("configFields");
    if (!container) return;
    container.innerHTML = "";

    function addField(card, id, module, key, labelText, options) {
      var label = document.createElement("label");
      label.textContent = labelText;
      var control = document.createElement(options && options.select ? "select" : "input");
      control.name = id + "." + key;
      control.dataset.module = id;
      control.dataset.key = key;
      control.setAttribute("data-config-field", id + "." + key);
      if (options && options.secret) {
        control.type = "password";
        control.autocomplete = "new-password";
        control.setAttribute("data-config-secret", id + "." + key);
        control.placeholder = module.api_key_configured ? "已配置，留空保持不变" : "请输入 API Key";
        var secretState = document.createElement("small");
        secretState.className = "config-secret-state";
        secretState.textContent = module.api_key_configured ? "当前密钥已配置；不会回显明文" : "当前未配置密钥";
        label.appendChild(control);
        label.appendChild(secretState);
      } else {
        if (options && options.select) {
          options.select.forEach(function (value) {
            var option = document.createElement("option");
            option.value = value;
            option.textContent = value.toUpperCase();
            control.appendChild(option);
          });
        }
        control.value = module[key] == null ? "" : module[key];
        label.appendChild(control);
      }
      card.appendChild(label);
    }

    ["A", "B", "D"].forEach(function (id) {
      var module = modules[id] || {};
      var card = document.createElement("section");
      card.className = "config-card";
      card.innerHTML = "<header><strong></strong><span></span></header>";
      card.querySelector("strong").textContent = id + " · " + (module.name || "模块");
      card.querySelector("span").textContent = module.contract || "";

      addField(card, id, module, "mode", "运行模式", { select: ["rule", "mock", "smart"] });
      addField(card, id, module, "provider", "服务提供方");
      addField(card, id, module, "model", "模型");

      if (id === "A" || id === "D") {
        addField(card, id, module, "base_url", "接口地址");
        addField(card, id, module, "api_key", "API Key", { secret: true });
      }

      addField(card, id, module, "backend", "执行后端");

      if (id === "B") {
        var credentials = document.createElement("div");
        credentials.className = "credential-status-box";
        credentials.innerHTML = "<div><span>CodeArts AK</span><b></b></div><div><span>CodeArts SK</span><b></b></div><small>AK/SK 由服务器 codearts.env 管理，页面不回显或保存。</small>";
        ["ak", "sk"].forEach(function (credential) {
          var status = credentials.querySelectorAll("b")[credential === "ak" ? 0 : 1];
          status.setAttribute("data-credential-status", "B." + credential);
          status.textContent = credential === "ak" ? (module.api_key_configured ? "服务器已配置" : "服务器未配置") : "服务器管理";
        });
        card.appendChild(credentials);
      }
      container.appendChild(card);
    });
    if ($("configSource")) $("configSource").textContent = (config.source || "当前配置") + (config.updated_at ? " · " + config.updated_at : "");
    syncConfigAccess();
  }

  function loadConfig() { return Promise.all([api("model-config"), api("settings")]).then(function (values) { renderConfig(values[0]); var settings = values[1].settings || {}; [["settingRuntime", settings.runtime_mode], ["settingScene", settings.default_scene], ["settingSafety", settings.safe_control ? "已启用" : "未启用"], ["settingAudit", settings.audit_enabled ? "已启用" : "未启用"]].forEach(function (item) { if ($(item[0])) $(item[0]).textContent = item[1] == null ? "—" : item[1]; }); }).catch(function (error) { if ($("configFields")) $("configFields").innerHTML = '<p class="muted">配置读取失败: ' + error.message + "</p>"; }); }

  function saveConfig(event) {
    event.preventDefault();

    if (!isAdminSession()) {
      if ($("configMessage")) {
        $("configMessage").textContent =
          "保存失败：仅管理员可以修改运行配置";
      }
      toast("请先进行管理员登录");
      return;
    }

    var modules = {};

    document.querySelectorAll("[data-module]").forEach(function (control) {
      if (control.dataset.configSecret && !control.value.trim()) return;

      var id = control.dataset.module;
      modules[id] = modules[id] || {};
      modules[id][control.dataset.key] = control.value;
    });

    api("model-config", {
      method: "PUT",
      body: { modules: modules }
    })
      .then(function (data) {
        renderConfig(data);
        if ($("configMessage")) {
          $("configMessage").textContent = "配置已保存并应用";
        }
        toast("配置保存成功");
      })
      .catch(function (error) {
        if ($("configMessage")) {
          $("configMessage").textContent =
            "保存失败: " + error.message;
        }
        toast("配置保存失败");
      });
  }

  function formatTime(value) { if (!value) return "—"; var date = new Date(Number(value)); return isNaN(date.getTime()) ? String(value) : date.toLocaleString(); }
  function loadRecordEvents(runId, target) { target.textContent = "读取事件…"; api("runs/" + encodeURIComponent(runId) + "/events").then(function (data) { target.textContent = (data.events || []).map(function (item) { return formatTime(item.created_at) + "  " + item.type + (item.payload && item.payload.message ? " · " + item.payload.message : ""); }).join("\n") || "暂无事件"; }).catch(function (error) { target.textContent = "事件读取失败: " + error.message; }); }
  function loadRecords() { return api("runs").then(function (data) { var list = $("recordsList"); if (!list) return; list.innerHTML = ""; var runs = data.runs || []; if (!runs.length) { list.innerHTML = '<p class="muted">暂无运行记录</p>'; return; } runs.forEach(function (run) { var details = document.createElement("details"); details.className = "record-row"; var summary = document.createElement("summary"); summary.innerHTML = "<strong></strong><span></span><span></span><b class=\"record-state\"></b>"; summary.querySelector("strong").textContent = run.run_id || "—"; summary.querySelectorAll("span")[0].textContent = run.scene_id || "—"; summary.querySelectorAll("span")[1].textContent = formatTime(run.created_at); summary.querySelector("b").textContent = run.state || "—"; summary.querySelector("b").className = "record-state " + (["FAILED", "BLOCKED", "SAFE_STOPPED", "CANCELLED"].indexOf(run.state) >= 0 ? "failed" : run.state === "SUCCEEDED" ? "" : "running"); var eventBox = document.createElement("pre"); eventBox.className = "record-events"; eventBox.textContent = "展开读取事件"; details.appendChild(summary); details.appendChild(eventBox); details.addEventListener("toggle", function () { if (details.open && eventBox.textContent === "展开读取事件") loadRecordEvents(run.run_id, eventBox); }); list.appendChild(details); }); }).catch(function (error) { if ($("recordsList")) $("recordsList").innerHTML = '<p class="muted">记录读取失败: ' + error.message + "</p>"; }); }

  function switchView(view) { state.currentView = view; document.querySelectorAll(".nav-item").forEach(function (item) { item.classList.toggle("active", item.dataset.view === view); }); document.querySelectorAll("[data-view-panel]").forEach(function (panel) { var active = panel.dataset.viewPanel === view; panel.hidden = !active; panel.classList.toggle("active-view", active); }); if (view === "config") loadConfig(); if (view === "records") { if (state.session && state.session.authenticated) loadRecords(); else if ($("recordsHint")) $("recordsHint").textContent = "请先登录"; } if (view === "user") renderSession(); }

  function init() {
    document.querySelectorAll("[data-view]").forEach(function (button) { button.addEventListener("click", function () { switchView(button.dataset.view); }); });
    if ($("loginBtn")) $("loginBtn").addEventListener("click", login); if ($("logoutBtn")) $("logoutBtn").addEventListener("click", logout); if ($("userLogoutBtn")) $("userLogoutBtn").addEventListener("click", logout); if ($("run")) $("run").addEventListener("click", startRun); if ($("refreshHealth")) $("refreshHealth").addEventListener("click", renderHealth); if ($("refreshRecords")) $("refreshRecords").addEventListener("click", loadRecords); if ($("modelConfigForm")) $("modelConfigForm").addEventListener("submit", saveConfig); if ($("instruction")) $("instruction").addEventListener("input", updateInstructionCount);
    resetStages(); renderHealth(); renderScenarios(); renderSession(); api("livestream").then(function (data) { initLivestream(data.url || DEFAULT_HLS_URL); }).catch(function () { markLivestream("OFFLINE"); if ($("livestreamMsg")) $("livestreamMsg").textContent = "直播接口不可用"; }); resumeLastRun(); window.setInterval(function () { if (!state.loopActive) renderHealth(); }, 10000);
  }
  document.addEventListener("DOMContentLoaded", init);
})();
