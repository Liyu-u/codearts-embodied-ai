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

from integration.config.local_env import load_codearts_env  # noqa: E402

# An intentional local codearts.env overrides the offline default. With no
# local file, the demo remains deterministic and offline.
load_codearts_env()

# A demo should be repeatable and offline.  The UI still exposes the selected
# intent engine, but strategy generation remains the local safe primitive plan.
# Keep the demo reproducible by default, while allowing a caller to opt into
# the real CodeArts provider with CODEARTS_STRATEGY_MODE=required/auto.
os.environ.setdefault("CODEARTS_STRATEGY_MODE", "off")

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
    engine = str(payload.get("engine") or "rule").strip().lower()
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


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ClosedLoopDemo/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_bytes(204, b"", "text/plain; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/scenarios":
            self._send_json(200, {"scenarios": list_scenarios()})
            return
        if parsed.path == "/api/health":
            modules = {
                "perception": _safe_health(perception_adapter),
                "intent": _safe_health(intent),
                "strategy": _safe_health(strategy),
                "tracecoder": _safe_health(tracecoder),
            }
            healthy = all(_is_healthy(value) for value in modules.values())
            self._send_json(
                200,
                {
                    "status": "ok" if healthy else "degraded",
                    "healthy": healthy,
                    "mode": "real-adapters-with-mock-executor",
                    "modules": modules,
                },
            )
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            response = _run_demo(payload)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - surfaced as UI error
            self._send_json(500, {"ok": False, "error": f"demo pipeline error: {exc}"})
            return
        self._send_json(200, response)

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
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[demo] {self.address_string()} - {format % args}")


def main() -> None:
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
