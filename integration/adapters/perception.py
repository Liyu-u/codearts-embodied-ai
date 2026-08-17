from integration.contract_validation import assert_contract
from modules.perception.observation_normalizer import normalize_observation
from modules.perception.service import observe_scene


def run(input_json: dict) -> dict:
    if (
        input_json.get("schema_version") == "1.0.0"
        or input_json.get("message_type") == "perception_observation"
    ):
        output = normalize_observation(input_json)
    else:
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
