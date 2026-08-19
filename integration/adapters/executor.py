from integration.contract_validation import assert_contract
from integration.strategy_policy import normalize_capabilities, validate_strategy
from modules.executor.action_catalog import ALLOWED_ACTIONS
from modules.executor.strategy_interpreter import StrategyInterpreter


class ExecutorAdapter:
    def __init__(self, backend):
        self._backend = backend
        self._interpreter = StrategyInterpreter(backend)
        self._capabilities = normalize_capabilities({
            "allowed_actions": sorted(ALLOWED_ACTIONS),
            "max_recovery_attempts": self._interpreter.limits.max_recovery_attempts,
            "max_retries": 2,
        })

    @classmethod
    def from_profile(cls, profile, perception: dict, driver=None) -> "ExecutorAdapter":
        """按 local/sim/real 配置构造执行后端，再包装为适配器。"""
        from integration.config.loader import build_backend

        backend = build_backend(profile, perception, driver=driver)
        return cls(backend)

    def run(self, input_json: dict) -> dict:
        assert_contract(input_json, "strategy.v1")
        validation = validate_strategy(input_json, capabilities=self._capabilities)
        if not validation["passed"]:
            raise ValueError("strategy safety validation failed: " + "; ".join(validation["errors"]))
        output = self._interpreter.run(input_json)
        output["provenance"] = {
            "source": getattr(self._backend, "mode", "unknown"),
            "backend": getattr(self._backend, "mode", "unknown"),
            "agent": "executor",
            "validation": {"passed": True, "errors": []},
        }
        assert_contract(output, "execution.v1")
        return output

    def capabilities(self) -> dict:
        return dict(self._capabilities)

    def health(self) -> dict:
        return {
            "status": "ok",
            "module": "executor",
            "version": "1.0.0",
            "backend": self._backend.mode,
            "supported_actions": sorted(ALLOWED_ACTIONS),
            "capabilities": self.capabilities(),
        }
