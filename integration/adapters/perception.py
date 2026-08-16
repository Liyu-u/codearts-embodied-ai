from integration.contract_validation import assert_contract
from modules.perception.service import observe_scene


def run(input_json: dict) -> dict:
    output = observe_scene(input_json)
    assert_contract(output, "perception.v1")
    return output


def health() -> dict:
    return {
        "status": "ok",
        "module": "perception",
        "version": "1.0.0",
        "backend": "mock",
    }
