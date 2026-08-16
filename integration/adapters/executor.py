from integration.contract_validation import assert_contract
from modules.executor.action_catalog import ALLOWED_ACTIONS
from modules.executor.strategy_interpreter import StrategyInterpreter


class ExecutorAdapter:
    def __init__(self, backend):
        self._backend = backend
        self._interpreter = StrategyInterpreter(backend)

    def run(self, input_json: dict) -> dict:
        assert_contract(input_json, "strategy.v1")
        output = self._interpreter.run(input_json)
        assert_contract(output, "execution.v1")
        return output

    def health(self) -> dict:
        return {
            "status": "ok",
            "module": "executor",
            "version": "1.0.0",
            "backend": self._backend.mode,
            "supported_actions": sorted(ALLOWED_ACTIONS),
        }
