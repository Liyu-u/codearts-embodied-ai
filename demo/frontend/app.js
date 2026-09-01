/* Truthful cloud UI: renders only API data. No fabricated values.
 *
 * API contract (same-origin, prefixed by apiBase = "/api"):
 *   GET  /api/health   /api/scenarios   /api/livestream   /api/session
 *   POST /api/login  /api/logout  /api/runs
 *   GET  /api/runs/{run_id}
 *   GET  /api/runs/{run_id}/events?after_sequence=N
 * The retired synchronous run endpoint answers 410 and is never called.
 */
(function () {
  "use strict";

  var state = {
    runId: null,
    afterSequence: 0,
    loopActive: false,
    requestInFlight: false,
    terminal: false,
    hls: null,
    live: false,
  };

  // Same-origin relative HLS path (deployment constant; never absolute host).
  var DEFAULT_HLS_URL = "/live/isaac/index.m3u8";

  function $(id) {
    return document.getElementById(id);
  }

  // Same-origin relative HLS path (deployment constant; never absolute host).
  var DEFAULT_HLS_URL = "/live/isaac/index.m3u8";
  var apiBase = "/api";
  // Local dev convenience: when opened directly from disk, talk to the
  // local candidate server instead of the same-origin base.
  if(location.protocol==="file:")apiBase="http://127.0.0.1:8765"

  function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || "GET", credentials: "same-origin" };
    if (opts.body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body);
    }
    var url = apiBase + (path.charAt(0) === "/" ? path : "/" + path);
    return fetch(url, init).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok) {
            var err = new Error(data.error || ("HTTP " + response.status));
            err.status = response.status;
            err.data = data;
            throw err;
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
    setTimeout(function () {
      node.hidden = true;
    }, 4000);
  }

  function setHealthBadge(key, value) {
    var node = document.querySelector(
      '#systemHealth [data-component="' + key + '"] b'
    );
    if (node) node.textContent = value;
  }

  function renderHealth() {
    return api("health")
      .then(function (data) {
        var components = data.components || {};
        setHealthBadge("cloud", (components.cloud || {}).status || "未知");
        setHealthBadge("relay", (components.relay || {}).status || "未知");
        setHealthBadge("isaac", (components.isaac || {}).status || "未知");
        setHealthBadge("livestream", (components.livestream || {}).status || "未知");
        var providers = components.providers || {};
        setHealthBadge(
          "providers",
          providers.status === "not_probed" ? "未探测" : providers.status || "未知"
        );
      })
      .catch(function () {
        [
          "cloud",
          "relay",
          "isaac",
          "livestream",
          "providers",
        ].forEach(function (key) {
          setHealthBadge(key, "未连接");
        });
      });
  }

  function renderScenarios() {
    return api("scenarios")
      .then(function (data) {
        var list = data.scenarios || [];
        var container = $("scenarioList");
        if (!container) return;
        if (!list.length) {
          container.innerHTML = '<p class="muted">无数据</p>';
          return;
        }
        container.innerHTML = "";
        list.forEach(function (scenario, index) {
          var label = document.createElement("label");
          label.className = "scenario-item";
          var radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "scenario";
          radio.value = scenario.id;
          if (index === 0) radio.checked = true;
          var span = document.createElement("span");
          span.textContent = scenario.id + (scenario.name ? " · " + scenario.name : "");
          label.appendChild(radio);
          label.appendChild(span);
          container.appendChild(label);
        });
      })
      .catch(function () {
        var container = $("scenarioList");
        if (container) container.innerHTML = '<p class="muted">未连接</p>';
      });
  }

  function renderSession() {
    return api("session")
      .then(function (session) {
        var stateNode = $("sessionState");
        var loginBtn = $("loginBtn");
        var logoutBtn = $("logoutBtn");
        var runBtn = $("run");
        var hint = $("runHint");
        if (session.authenticated) {
          if (stateNode) stateNode.textContent = session.role + " · " + session.user;
          if (loginBtn) loginBtn.hidden = true;
          if (logoutBtn) logoutBtn.hidden = false;
          if (runBtn) runBtn.disabled = false;
          if (hint) hint.textContent = "将以 " + session.role + " 身份执行";
        } else {
          if (stateNode) stateNode.textContent = "未登录";
          if (loginBtn) loginBtn.hidden = false;
          if (logoutBtn) logoutBtn.hidden = true;
          if (runBtn) runBtn.disabled = true;
          if (hint) hint.textContent = "需要登录后才能执行";
        }
      })
      .catch(function () {
        /* keep defaults */
      });
  }

  function login() {
    var user = window.prompt("用户名", "operator");
    if (!user) return;
    var password = window.prompt("密码");
    if (!password) return;
    api("login", {
      method: "POST",
      body: { user: user, password: password },
    })
      .then(function () {
        toast("登录成功");
        renderSession();
      })
      .catch(function (error) {
        toast("登录失败: " + error.message);
      });
  }

  function logout() {
    api("logout", { method: "POST", body: {} })
      .then(renderSession)
      .catch(renderSession);
  }

  // ---------------- HLS ----------------
  function markLivestream(value) {
    var status = $("livestreamStatus");
    if (status) status.textContent = value;
  }

  function initLivestream(url) {
    var video = $("livestream");
    if (!video) return;
    var message = $("livestreamMsg");
    if (!url) {
      if (message) message.textContent = "无数据";
      markLivestream("OFFLINE");
      return;
    }
    if (message) message.textContent = "";
    video.addEventListener("timeupdate", function () {
      // LIVE is only real when the media clock actually advances.
      if (video.currentTime > 0 && !state.live) {
        state.live = true;
        markLivestream("LIVE");
      }
    });
    video.addEventListener("error", function () {
      if (!state.live) markLivestream("OFFLINE");
    });
    if (window.Hls && window.Hls.isSupported()) {
      var hls = new window.Hls({ liveSyncDurationCount: 2 });
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.ERROR, function (_event, data) {
        if (data && data.fatal) {
          markLivestream("RECONNECTING");
          if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
            setTimeout(function () {
              hls.startLoad();
            }, 3000);
          }
        }
      });
      state.hls = hls;
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      video.addEventListener("loadedmetadata", function () {
        markLivestream("CONNECTING");
      });
    } else {
      if (message) message.textContent = "浏览器不支持 HLS";
      markLivestream("OFFLINE");
    }
  }

  // ---------------- Run ----------------
  function selectedScene() {
    var checked = document.querySelector('input[name="scenario"]:checked');
    return checked ? checked.value : "";
  }

  function startRun() {
    var instruction = $("instruction").value.trim();
    var sceneId = selectedScene();
    if (!sceneId) {
      toast("请先选择场景");
      return;
    }
    if (!instruction) {
      toast("请输入任务指令");
      return;
    }
    api("runs", {
      method: "POST",
      body: { scene_id: sceneId, instruction: instruction },
    })
      .then(function (data) {
        var run = data.run || {};
        var runId = run.run_id || run.id;
        if (!runId) throw new Error("no run_id in response");
        state.runId = runId;
        state.afterSequence = 0;
        state.terminal = false;
        try {
          localStorage.setItem("closed_loop_run_id", runId);
        } catch (error) {
          /* storage unavailable */
        }
        toast("任务已创建: " + runId);
        $("eventTimeline").innerHTML = '<p class="muted">等待中</p>';
        startPolling();
      })
      .catch(function (error) {
        if (error.status === 401) {
          toast("需要登录");
          renderSession();
        } else {
          toast("创建失败: " + error.message);
        }
      });
  }

  function resumeLastRun() {
    var runId = null;
    try {
      runId = localStorage.getItem("closed_loop_run_id");
    } catch (error) {
      return;
    }
    if (runId) {
      state.runId = runId;
      startPolling();
    }
  }

  function renderSnapshot(run) {
    if (!run) return;
    var runState = run.state || "无数据";
    $("runId").textContent = run.run_id || "—";
    $("runState").textContent = runState;
    $("currentAction").textContent = run.current_action || "无数据";
    $("runTarget").textContent = run.scene_id || "无数据";
    $("safetyEvent").textContent = run.error_message || run.error_code || "无数据";
    $("repairAttempts").textContent =
      run.repair_attempts == null ? "无数据" : String(run.repair_attempts);

    var resultNode = $("resultSummary");
    var terminalStates = [
      "SUCCEEDED",
      "FAILED",
      "SAFE_STOPPED",
      "BLOCKED",
      "CANCELLED",
    ];
    if (terminalStates.indexOf(runState) >= 0) {
      if (!state.terminal) {
        state.terminal = true;
        setTimeout(function () {
          stopPolling();
        }, 1200);
      }
      resultNode.textContent = runState + (run.result ? " · " + run.result : "");
      resultNode.className = runState === "SUCCEEDED" ? "result-ok" : "result-bad";
      if (run.audit_eligible) {
        $("evidenceSummary").textContent = "证据审计通过";
      }
    } else {
      resultNode.textContent = runState;
      resultNode.className = "result-wait";
    }

    if (run.error_code) {
      $("safetyEvent").textContent =
        run.error_code + (run.error_message ? " · " + run.error_message : "");
    }
  }

  function renderStagesFromState(runState) {
    var order = ["A", "B", "C", "D"];
    var map = {
      UNDERSTANDING: "A",
      PLANNING: "B",
      QUEUED_C: "C",
      EXECUTING: "C",
      VERIFYING: "D",
      SUCCEEDED: "D",
    };
    var target = map[runState];
    if (!target) return;
    order.forEach(function (letter) {
      var stateNode = $("stage" + letter + "State");
      if (!stateNode) return;
      if (letter === target) {
        stateNode.textContent = "进行中";
      } else if (order.indexOf(letter) < order.indexOf(target)) {
        stateNode.textContent = "已完成";
      }
    });
  }

  function renderStagesFromEvent(event) {
    var type = event.type || "";
    var message = "";
    if (event.payload && event.payload.message) {
      message = String(event.payload.message);
    }
    var stageNode = null;
    var detailNode = null;
    if (type === "A_STARTED") {
      stageNode = $("stageAState");
      detailNode = $("stageADetail");
    } else if (type === "B_STARTED") {
      stageNode = $("stageBState");
      detailNode = $("stageBDetail");
    } else if (type === "C_EXECUTION_QUEUED") {
      stageNode = $("stageCState");
      detailNode = $("stageCDetail");
    } else if (type === "C_EVIDENCE_VERIFYING" || type === "D_REPAIR_QUEUED") {
      stageNode = $("stageDState");
      detailNode = $("stageDDetail");
    } else if (type === "RUN_SUCCEEDED") {
      ["A", "B", "C", "D"].forEach(function (letter) {
        var node = $("stage" + letter + "State");
        if (node) node.textContent = "已完成";
      });
    }
    if (stageNode) stageNode.textContent = "有事件";
    if (detailNode && message) detailNode.textContent = message;
  }

  function pollRun() {
    if (!state.runId || state.requestInFlight) return;
    state.requestInFlight = true;
    api("runs/" + encodeURIComponent(state.runId))
      .then(function (data) {
        var run = data.run || {};
        renderSnapshot(run);
        renderStagesFromState(run.state);
      })
      .catch(function (error) {
        if (error.status === 404) {
          $("runState").textContent = "运行不存在";
          stopPolling();
        }
      })
      .finally(function () {
        state.requestInFlight = false;
      });
  }

  function pollEvents() {
    if (!state.runId || state.terminal || state.requestInFlight) return;
    state.requestInFlight = true;
    api(
      "runs/" +
        encodeURIComponent(state.runId) +
        "/events?after_sequence=" +
        state.afterSequence
    )
      .then(function (data) {
        var events = data.events || [];
        events.forEach(function (event) {
          if (event.sequence > state.afterSequence) {
            state.afterSequence = event.sequence;
          }
          appendEvent(event);
          renderStagesFromEvent(event);
        });
      })
      .catch(function () {
        /* transient */
      })
      .finally(function () {
        state.requestInFlight = false;
      });
  }

  function appendEvent(event) {
    var timeline = $("eventTimeline");
    if (!timeline) return;
    var placeholder = timeline.querySelector(".muted");
    if (placeholder) timeline.innerHTML = "";
    var row = document.createElement("p");
    var time = document.createElement("time");
    time.textContent = event.created_at || "";
    row.appendChild(time);
    var text = document.createElement("span");
    var message = "";
    if (event.payload && event.payload.message) {
      message = " · " + String(event.payload.message);
    }
    text.textContent =
      "[" + (event.sequence || "?") + "] " + (event.type || "event") + message;
    row.appendChild(text);
    timeline.appendChild(row);
    timeline.scrollTop = timeline.scrollHeight;
  }

  function startPolling() {
    if (state.loopActive) return;
    state.loopActive = true;
    function tick() {
      if (!state.runId || state.terminal) {
        state.loopActive = false;
        return;
      }
      pollRun();
      pollEvents();
      setTimeout(tick, 1500);
    }
    tick();
  }

  function stopPolling() {
    state.terminal = true;
    state.loopActive = false;
  }

  // ---------------- init ----------------
  function init() {
    var loginBtn = $("loginBtn");
    var logoutBtn = $("logoutBtn");
    var runBtn = $("run");
    if (loginBtn) loginBtn.addEventListener("click", login);
    if (logoutBtn) logoutBtn.addEventListener("click", logout);
    if (runBtn) runBtn.addEventListener("click", startRun);
    renderHealth();
    renderScenarios();
    renderSession();
    api("livestream")
      .then(function (data) {
        initLivestream(data.url || DEFAULT_HLS_URL);
      })
      .catch(function () {
        markLivestream("OFFLINE");
        var message = $("livestreamMsg");
        if (message) message.textContent = "未连接";
      });
    resumeLastRun();
    setInterval(function () {
      if (!state.loopActive) renderHealth();
    }, 10000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
