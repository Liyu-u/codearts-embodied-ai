"""Local HTTP server for the closed-loop frontend demo.

Run from the repository root:

    python demo/server.py

The HTTP serving layer uses only the Python standard library.  The real
repository adapters still require the dependencies in ``requirements.txt``.
The server exposes the pipeline behind a small JSON API and serves the static
UI from ``demo/frontend``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(__file__).resolve().parent / "frontend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.config.local_env import load_codearts_env, load_local_env  # noqa: E402

MODEL_CONFIG_PATH = ROOT / ".model_config.local.json"
_MODEL_IDS = ("A", "B", "C", "D")
_MODEL_MODES = {"rule", "mock", "smart"}


def _model_mode(value: str, smart: set[str], mock: set[str] = set()) -> str:
    value = str(value or "").strip().lower()
    if value in smart:
        return "smart"
    if value in mock:
        return "mock"
    return "rule"


def _model_config_defaults() -> dict:
    c_backend = os.getenv("EXECUTOR_BACKEND", "mock")
    return {
        "version": 1,
        "source": "local-env",
        "updated_at": None,
        "modules": {
            "A": {
                "id": "A", "name": "意图理解", "contract": "task.v1",
                "mode": _model_mode(os.getenv("RIA_PLANNER_ENGINE", "rule"), {"llm", "hybrid"}),
                "provider": "DeepSeek",
                "model": os.getenv("RIA_DEEPSEEK_MODEL", "deepseek-v4-flash"),
                "base_url": os.getenv("RIA_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                "api_key": os.getenv("RIA_DEEPSEEK_API_KEY", ""),
                "backend": "RIA Planner",
            },
            "B": {
                "id": "B", "name": "任务规划", "contract": "strategy.v1",
                "mode": _model_mode(os.getenv("CODEARTS_STRATEGY_MODE", "off"), {"auto", "optional", "required"}),
                "provider": "CodeArts CLI",
                "model": os.getenv("CODEARTS_STRATEGY_MODEL", ""),
                "base_url": "",
                "api_key": os.getenv("CODEARTS_CLI_AK", ""),
                "backend": "CodeArts Agent",
            },
            "C": {
                "id": "C", "name": "动作执行", "contract": "execution.v1",
                "mode": _model_mode(c_backend, {"isaac", "real"}, {"mock"}),
                "provider": "MockBackend" if c_backend not in {"isaac", "real"} else c_backend,
                "model": "", "base_url": "", "api_key": "",
                "backend": c_backend,
            },
            "D": {
                "id": "D", "name": "结果验证", "contract": "feedback.v1",
                "mode": _model_mode(os.getenv("TRACECODER_LLM_MODE", "off"), {"optional", "required"}),
                "provider": "DeepSeek / TraceCoder",
                "model": os.getenv("TRACECODER_LLM_MODEL", "deepseek-v4-flash"),
                "base_url": os.getenv("TRACECODER_LLM_BASE_URL", "https://api.deepseek.com"),
                "api_key": os.getenv("TRACECODER_LLM_API_KEY", ""),
                "backend": "TraceCoder",
            },
        },
    }


def _load_model_config() -> dict:
    config = _model_config_defaults()
    if not MODEL_CONFIG_PATH.is_file():
        return config
    try:
        saved = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return config
    if not isinstance(saved, dict) or not isinstance(saved.get("modules"), dict):
        return config
    for module_id in _MODEL_IDS:
        values = saved["modules"].get(module_id)
        if not isinstance(values, dict):
            continue
        for key in ("mode", "provider", "model", "base_url", "backend"):
            if values.get(key) is not None:
                config["modules"][module_id][key] = str(values[key]).strip()
        if values.get("api_key") is not None:
            config["modules"][module_id]["api_key"] = str(values["api_key"]).strip()
        if config["modules"][module_id]["mode"] not in _MODEL_MODES:
            config["modules"][module_id]["mode"] = "rule"
    config["source"] = "persistent"
    config["updated_at"] = saved.get("updated_at")
    return config


def _public_model_config(config: dict) -> dict:
    output = deepcopy(config)
    for module in output.get("modules", {}).values():
        key = str(module.pop("api_key", "") or "")
        module["api_key_configured"] = bool(key)
        module["api_key_masked"] = "••••••••" if key else "未配置"
    return output


def _persist_model_config(config: dict) -> None:
    payload = deepcopy(config)
    payload["source"] = "persistent"
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    temp_path = MODEL_CONFIG_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, MODEL_CONFIG_PATH)
    config["source"] = payload["source"]
    config["updated_at"] = payload["updated_at"]


def _apply_model_config(config: dict) -> None:
    modules = config.get("modules", {})
    a, b, c, d = (modules.get(key, {}) for key in _MODEL_IDS)
    os.environ["RIA_PLANNER_ENGINE"] = "rule" if a.get("mode") in {"rule", "mock"} else "llm"
    for key, env_name in (("model", "RIA_DEEPSEEK_MODEL"), ("base_url", "RIA_DEEPSEEK_BASE_URL"), ("api_key", "RIA_DEEPSEEK_API_KEY")):
        if a.get(key):
            os.environ[env_name] = str(a[key])
    os.environ["CODEARTS_STRATEGY_MODE"] = "off" if b.get("mode") in {"rule", "mock"} else "required"
    if b.get("model"):
        os.environ["CODEARTS_STRATEGY_MODEL"] = str(b["model"])
    if b.get("api_key"):
        os.environ["CODEARTS_CLI_AK"] = str(b["api_key"])
    os.environ["EXECUTOR_BACKEND"] = str(c.get("backend") or "mock")
    os.environ["TRACECODER_LLM_MODE"] = "off" if d.get("mode") in {"rule", "mock"} else "required"
    for key, env_name in (("model", "TRACECODER_LLM_MODEL"), ("base_url", "TRACECODER_LLM_BASE_URL"), ("api_key", "TRACECODER_LLM_API_KEY")):
        if d.get(key):
            os.environ[env_name] = str(d[key])
    settings_module = sys.modules.get("modules.intent_understanding.robot_intent_agent.config.settings")
    if settings_module is not None:
        settings_module.get_settings.cache_clear()
    intent_module = sys.modules.get("modules.intent_understanding.adapter")
    if intent_module is not None:
        intent_module._LLM_PLANNER_CACHE.clear()
    tracecoder_module = sys.modules.get("integration.adapters.tracecoder")
    if tracecoder_module is not None:
        try:
            tracecoder_module.configure_llm(
                mode=os.environ["TRACECODER_LLM_MODE"],
                provider=tracecoder_module.LLMProvider(tracecoder_module.LLMConfig.from_env()),
            )
        except (AttributeError, TypeError, ValueError):
            pass


def configure_runtime_environment() -> dict:
    """Load local runtime configuration explicitly for the HTTP entrypoint.

    Importing ``demo.server`` is also used by unit/e2e tests and benchmark
    helpers.  Environment loading must therefore happen at process startup,
    not as an import side effect that changes unrelated test configuration.
    """
    load_codearts_env()
    load_local_env(".env")
    load_local_env("tracecoder_llm.env")
    os.environ.setdefault("CODEARTS_STRATEGY_MODE", "off")
    config = _load_model_config()
    _apply_model_config(config)
    return config


def _merge_model_config(payload: dict, current: dict) -> dict:
    incoming = payload.get("modules", payload) if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        raise ValueError("modules 必须是 JSON 对象")
    merged = deepcopy(current)
    for module_id in _MODEL_IDS:
        values = incoming.get(module_id)
        if not isinstance(values, dict):
            continue
        mode = str(values.get("mode", merged["modules"][module_id]["mode"])).strip().lower()
        if mode not in _MODEL_MODES:
            raise ValueError(f"{module_id} 模式不受支持：{mode}")
        merged["modules"][module_id]["mode"] = mode
        for key in ("provider", "model", "base_url", "backend"):
            if key in values:
                merged["modules"][module_id][key] = str(values.get(key) or "").strip()
        if values.get("clear_api_key") is True:
            merged["modules"][module_id]["api_key"] = ""
        elif "api_key" in values:
            key = str(values.get("api_key") or "").strip()
            if key and not key.startswith("••"):
                merged["modules"][module_id]["api_key"] = key
    return merged


_MODEL_CONFIG = _load_model_config()
_CLOUD_SERVICE = configure_cloud_service()


from demo.cloud.service import configure_cloud_service, get_cloud_service  # noqa: E402
from demo.scenarios import get_scenario, list_scenarios  # noqa: E402
from integration.adapters import intent, strategy, tracecoder  # noqa: E402
from integration.adapters import perception as perception_adapter  # noqa: E402
from integration.adapters.executor import ExecutorAdapter  # noqa: E402
from integration.pipeline import run_pipeline  # noqa: E402
from integration.acceptance_metrics import compute_metrics  # noqa: E402
from modules.evaluator.tracecoder.experience import ExperienceStore  # noqa: E402
from modules.executor.mock_backend import MockBackend  # noqa: E402


class _DemoStrategyAdapter:
    """Use B's local strategy while removing recovery for the D-repair demo."""

    def run(self, task: dict) -> dict:
        output = deepcopy(strategy.run(task))
        for step in output.get("steps", []):
            step.pop("on_failure", None)
        output["mode"] = "tracecoder_demo_baseline"
        output["message"] = "TraceCoder 修复演示：故意移除动作级恢复，等待 D 生成 patch"
        return output

    def health(self) -> dict:
        return strategy.health()


class _IsolatedTraceCoderAdapter:
    """Keep each browser run independent from the process-level HLLM memory."""

    def run(self, input_json: dict) -> dict:
        return tracecoder.run(
            input_json,
            experience_store=ExperienceStore(),
        )

    def health(self) -> dict:
        return tracecoder.health()


def _safe_health(adapter: object) -> dict:
    """Normalize adapter health without allowing one module to hide failures."""
    try:
        value = adapter.health()
        if isinstance(value, dict):
            normalized = dict(value)
            # Adapters historically used either {status: ...} or
            # {healthy: ...}; expose one stable aggregate field to the UI.
            normalized.setdefault(
                "status",
                "ok" if normalized.get("healthy", True) is not False else "degraded",
            )
            return normalized
        return {
            "healthy": False,
            "status": "degraded",
            "message": "health() did not return an object",
        }
    except Exception as exc:  # pragma: no cover - defensive HTTP boundary
        return {
            "healthy": False,
            "status": "degraded",
            "message": f"health check failed: {type(exc).__name__}: {exc}",
        }


def _is_healthy(value: dict) -> bool:
    if "healthy" in value:
        return bool(value["healthy"])
    return value.get("status") in {"ok", "healthy"}


def _acceptance_summary(
    scenario: dict,
    result: dict,
    instruction: str | None = None,
) -> dict:
    """Compare the scenario expectation with the actual pipeline result."""

    expected = scenario.get("expected")
    # A workcell can expose several preset commands with different expected
    # outcomes. Compare the command currently being run, not the default
    # outcome of the containing scene.
    for command in scenario.get("commands", []):
        if instruction and command.get("instruction") == instruction:
            expected = command.get("expected", expected)
            break
    actual = result.get("status")
    return {
        "expected_status": expected,
        "actual_status": actual,
        "passed": expected == actual,
        "message": "实际结果符合场景预期" if expected == actual else "实际结果与场景预期不一致",
    }


def _run_demo(payload: dict) -> dict:
    scene_id = payload.get("scene_id", "stacking_cubes")
    instruction = str(payload.get("instruction") or "").strip()
    configured_engine = "llm" if _MODEL_CONFIG.get("modules", {}).get("A", {}).get("mode") == "smart" else "rule"
    engine = str(payload.get("engine") or configured_engine).strip().lower()
    if engine not in {"rule", "hybrid", "llm"}:
        engine = "rule"
    if not instruction:
        raise ValueError("instruction 不能为空")
    request_id = str(payload.get("request_id") or f"demo-{uuid4().hex}")

    scenario = get_scenario(scene_id)
    scene = scenario["scene"]
    # Keep the actual perception adapter in the loop for the canonical scene;
    # custom demo fixtures enter at the same perception.v1 boundary.
    if scene_id in {"stacking_cubes", "sorting_workcell"}:
        scene = perception_adapter.run({"scene_id": scene_id, "backend": "mock"})
    backend = MockBackend.from_perception(
        scene,
        failures=scenario.get("executor_failures"),
    )
    strategy_adapter = (
        _DemoStrategyAdapter()
        if scenario.get("tracecoder_repair")
        else strategy
    )
    adapters = {
        "intent": intent,
        "strategy": strategy_adapter,
        "executor": ExecutorAdapter(backend),
        "tracecoder": _IsolatedTraceCoderAdapter(),
    }

    started = time.perf_counter()
    result = run_pipeline(
        scene,
        instruction,
        adapters,
        engine=engine,
        request_id=request_id,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    backend_snapshot = backend.snapshot()
    # Keep the snapshot both beside and inside the result for callers that
    # consume the pipeline object directly rather than the HTTP envelope.
    result["backend_snapshot"] = backend_snapshot
    scenario_summary = {
        "id": scene_id,
        **{key: value for key, value in scenario.items() if key != "scene"},
    }
    acceptance = _acceptance_summary(scenario, result, instruction)
    metrics = compute_metrics([{
        "expected": {"pipeline_status": acceptance["expected_status"]},
        "actual": result,
    }])
    return {
        "ok": True,
        "server_mode": "real-adapters-with-mock-executor",
        "request_id": request_id,
        "elapsed_ms": elapsed_ms,
        "scenario": scenario_summary,
        "scene": scene,
        "result": result,
        "backend_snapshot": backend_snapshot,
        "acceptance": acceptance,
        "metrics": metrics,
    }



def _task_catalog() -> list[dict]:
    rows = []
    for index, scenario in enumerate(list_scenarios()):
        expected = scenario.get("expected", "SUCCEEDED")
        status = "BLOCKED" if expected == "BLOCKED" else "RUNNING" if index == 0 else "SUCCEEDED"
        rows.append({
            "id": scenario["id"],
            "name": scenario.get("name", scenario["id"]),
            "instruction": scenario.get("instruction", ""),
            "focus": scenario.get("focus", "闭环演示"),
            "status": status,
            "progress": 65 if status == "RUNNING" else 100 if status == "SUCCEEDED" else 42,
            "expected": expected,
            "step": "动作执行" if status == "RUNNING" else "结果验证" if status == "SUCCEEDED" else "安全门禁",
            "updated_at": "刚刚" if index == 0 else "12 分钟前",
        })
    return rows


def _robot_catalog() -> list[dict]:
    return [
        {"id": "RBT-001", "name": "RBT-001", "model": "AUBO i10", "status": "READY", "ip": "192.168.1.10", "load": "2.1 / 10.0 kg", "scene_id": "stacking_cubes"},
        {"id": "RBT-002", "name": "RBT-002", "model": "AUBO i5", "status": "IDLE", "ip": "192.168.1.11", "load": "0.8 / 5.0 kg", "scene_id": "sorting_workcell"},
        {"id": "RBT-003", "name": "RBT-003", "model": "UR5e", "status": "MAINTENANCE", "ip": "192.168.1.12", "load": "0.0 / 5.0 kg", "scene_id": "inspection_cell"},
    ]


def _robot_telemetry(robot_id: str) -> dict:
    robot = next((item for item in _robot_catalog() if item["id"] == robot_id), None)
    if robot is None:
        return {}
    return {"robot": robot, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "joints": [
        {"name": "J1", "position": -45.12, "velocity": 12.5, "temperature": 45, "load": 18},
        {"name": "J2", "position": -30.45, "velocity": 8.3, "temperature": 42, "load": 12},
        {"name": "J3", "position": 78.23, "velocity": 15.2, "temperature": 48, "load": 20},
        {"name": "J4", "position": 15.67, "velocity": 6.1, "temperature": 44, "load": 15},
        {"name": "J5", "position": 95.21, "velocity": 10.0, "temperature": 41, "load": 10},
        {"name": "J6", "position": -120.33, "velocity": 9.7, "temperature": 46, "load": 17},
    ], "safety": {"estop": False, "door": "CLOSED", "collision": "NORMAL", "speed_ratio": 100}}


def _scene_catalog() -> list[dict]:
    rows = []
    for scenario in list_scenarios():
        scene = scenario.get("scene", {})
        rows.append({"id": scenario["id"], "name": scenario.get("name", scenario["id"]), "status": "ONLINE", "objects": len(scene.get("objects", [])), "revision": scene.get("execution_context", {}).get("scene_revision", "1"), "focus": scenario.get("focus", "闭环演示")})
    return rows


def _dataset_catalog() -> list[dict]:
    return [
        {"id": "ds-perception-001", "name": "工作站感知样本", "type": "RGB-D / 目标检测", "records": 12840, "quality": 96, "status": "READY", "updated_at": "今天 18:40"},
        {"id": "ds-trajectory-002", "name": "抓取轨迹数据", "type": "轨迹 / 力控", "records": 4860, "quality": 91, "status": "PROCESSING", "updated_at": "今天 17:25"},
        {"id": "ds-task-003", "name": "任务执行日志", "type": "任务 / 反馈", "records": 22460, "quality": 98, "status": "READY", "updated_at": "昨天 22:10"},
        {"id": "ds-label-004", "name": "场景标注集", "type": "3D / 语义标注", "records": 3180, "quality": 87, "status": "REVIEW", "updated_at": "昨天 16:08"},
    ]


def _model_catalog() -> list[dict]:
    return [
        {"id": "perception-v3", "name": "Perception-V3", "type": "感知与检测", "version": "v3.2.1", "status": "DEPLOYED", "accuracy": 96.8, "updated_at": "今天 16:20"},
        {"id": "intent-v2", "name": "Intent Parser", "type": "任务理解", "version": "v2.4.0", "status": "DEPLOYED", "accuracy": 94.2, "updated_at": "今天 15:42"},
        {"id": "planner-v5", "name": "Trace Planner", "type": "任务规划", "version": "v5.1.3", "status": "CANARY", "accuracy": 91.7, "updated_at": "昨天 21:05"},
        {"id": "policy-v1", "name": "Safe Policy", "type": "动作策略", "version": "v1.8.2", "status": "STAGING", "accuracy": 98.1, "updated_at": "昨天 18:30"},
    ]


def _log_catalog() -> list[dict]:
    return [
        {"id": "evt-001", "time": "20:09:17", "level": "INFO", "source": "system", "title": "服务连接正常", "detail": "四模块接口健康检查通过"},
        {"id": "evt-002", "time": "20:08:42", "level": "SUCCESS", "source": "task", "title": "任务执行完成", "detail": "叠放方块（成功）返回 SUCCEEDED"},
        {"id": "evt-003", "time": "20:07:31", "level": "INFO", "source": "scene", "title": "场景加载完成", "detail": "stacking_cubes revision 1"},
        {"id": "evt-004", "time": "19:58:11", "level": "WARNING", "source": "robot", "title": "速度倍率调整", "detail": "机器人 RBT-001 当前速度倍率 100%"},
        {"id": "evt-005", "time": "19:45:36", "level": "INFO", "source": "model", "title": "模型版本发布", "detail": "Perception-V3 v3.2.1 已部署"},
    ]


def _settings_catalog() -> dict:
    return {"runtime_mode": "AUTO", "default_robot": "RBT-001", "default_scene": "stacking_cubes", "event_stream": "WS /api/events", "api_base": "/api", "session_timeout": 30, "safe_control": True, "audit_enabled": True}


def _livestream_catalog() -> dict:
    """Expose only the playback URL; publisher credentials stay server-side."""

    url = os.environ.get("LIVESTREAM_HLS_URL", "").strip()
    return {
        "enabled": bool(url),
        "protocol": "HLS",
        "url": url,
        "source": "mediamtx" if url else None,
    }

class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ClosedLoopDemo/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_bytes(204, b"", "text/plain; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            self._send_json(200, {"tasks": _task_catalog()})
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.rsplit("/", 1)[-1]
            task = next((item for item in _task_catalog() if item["id"] == task_id), None)
            if task is None:
                self._send_json(404, {"ok": False, "error": "task not found"})
            else:
                self._send_json(200, {"task": task, "scenario": get_scenario(task_id)})
            return
        if parsed.path == "/api/robots":
            self._send_json(200, {"robots": _robot_catalog()})
            return
        if parsed.path.startswith("/api/robots/") and parsed.path.endswith("/telemetry"):
            robot_id = parsed.path.split("/")[3]
            telemetry = _robot_telemetry(robot_id)
            self._send_json(200 if telemetry else 404, {"telemetry": telemetry} if telemetry else {"ok": False, "error": "robot not found"})
            return
        if parsed.path == "/api/scenes":
            self._send_json(200, {"scenes": _scene_catalog()})
            return
        if parsed.path.startswith("/api/scenes/"):
            scene_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self._send_json(200, {"scene": get_scenario(scene_id)["scene"]})
            except (KeyError, ValueError):
                self._send_json(404, {"ok": False, "error": "scene not found"})
            return
        if parsed.path == "/api/datasets":
            self._send_json(200, {"datasets": _dataset_catalog()})
            return
        if parsed.path == "/api/models":
            self._send_json(200, {"models": _model_catalog()})
            return
        if parsed.path == "/api/logs":
            self._send_json(200, {"logs": _log_catalog()})
            return
        if parsed.path == "/api/audit":
            self._send_json(200, {"records": _log_catalog()[:4]})
            return
        if parsed.path == "/api/settings":
            self._send_json(200, {"settings": _settings_catalog()})
            return
        if parsed.path == "/api/livestream":
            self._send_json(200, _livestream_catalog())
            return

        if parsed.path == "/api/model-config":
            self._send_json(200, {"config": _public_model_config(_MODEL_CONFIG)})
            return
        if parsed.path == "/api/permissions":
            self._send_json(200, {"roles": [{"id": "admin", "name": "平台管理员", "permissions": 18}, {"id": "operator", "name": "任务操作员", "permissions": 11}, {"id": "viewer", "name": "只读用户", "permissions": 5}]})
            return
        if parsed.path == "/api/scenarios":
            self._send_json(200, {"scenarios": get_cloud_service().scenarios()})
            return
        if parsed.path == "/api/health":
            self._send_json(200, get_cloud_service().health())
            return
        if parsed.path == "/api/runs":
            runs = [get_cloud_service().get_run(row["run_id"]) for row in get_cloud_service().store.list_runs()]
            self._send_json(200, {"runs": runs})
            return
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/events"):
            run_id = parsed.path.split("/")[3]
            try:
                self._send_json(200, {"events": get_cloud_service().get_events(run_id)})
            except KeyError:
                self._send_json(404, {"ok": False, "error": "run not found"})
            return
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.split("/")[3]
            try:
                self._send_json(200, {"run": get_cloud_service().get_run(run_id)})
            except KeyError:
                self._send_json(404, {"ok": False, "error": "run not found"})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                task = {"id": str(payload.get("id") or f"task-{uuid4().hex[:8]}"), "name": str(payload.get("name") or "未命名任务"), "instruction": str(payload.get("instruction") or ""), "scene_id": str(payload.get("scene_id") or "stacking_cubes"), "status": "QUEUED", "progress": 0, "step": "等待执行", "updated_at": "刚刚", "marker": "task-created"}
                self._send_json(201, {"ok": True, "task": task})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path.startswith("/api/robots/") and parsed.path.endswith("/commands"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(202, {"ok": True, "accepted": True, "robot_id": parsed.path.split("/")[3], "command": payload, "message": "命令已进入演示控制队列"})
            return
        if parsed.path == "/api/scenes":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            scene_id = str(payload.get("id") or f"scene-{uuid4().hex[:8]}")
            self._send_json(201, {"ok": True, "scene": {"id": scene_id, "name": payload.get("name", "新建场景"), "status": "DRAFT", "objects": 0, "revision": "1"}})
            return
        if parsed.path == "/api/runs":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                run = get_cloud_service().create_run(str(payload.get("scene_id") or ""), str(payload.get("instruction") or ""))
                self._send_json(202, {"ok": True, "run": run})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": f"cloud run error: {exc}"})
            return
        if parsed.path == "/api/run":
            self._send_json(410, {"ok": False, "error": "legacy /api/run is retired; use POST /api/runs"})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            settings = _settings_catalog()
            settings.update(payload)
            self._send_json(200, {"ok": True, "settings": settings})
            return

        if parsed.path == "/api/model-config":
            global _MODEL_CONFIG
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                updated = _merge_model_config(payload, _MODEL_CONFIG)
                _persist_model_config(updated)
                _MODEL_CONFIG = updated
                _apply_model_config(_MODEL_CONFIG)
                self._send_json(200, {"ok": True, "config": _public_model_config(_MODEL_CONFIG)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except OSError as exc:
                self._send_json(500, {"ok": False, "error": f"模型配置写入失败: {exc}"})
            return
        if parsed.path.startswith("/api/scenes/"):
            scene_id = parsed.path.rsplit("/", 1)[-1]
            self._send_json(200, {"ok": True, "scene": {"id": scene_id, "status": "UPDATED", "revision": "2"}})
            return
        self._send_json(404, {"ok": False, "error": "not found"})
    def _serve_static(self, request_path: str) -> None:
        relative = unquote(request_path.lstrip("/")) or "index.html"
        candidate = (FRONTEND_ROOT / relative).resolve()
        try:
            candidate.relative_to(FRONTEND_ROOT.resolve())
        except ValueError:
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        if not candidate.is_file():
            self._send_json(404, {"ok": False, "error": "static file not found"})
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(200, candidate.read_bytes(), content_type)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[demo] {self.address_string()} - {format % args}")


def main() -> None:
    global _MODEL_CONFIG
    _MODEL_CONFIG = configure_runtime_environment()
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"Closed-loop demo: http://{host}:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
