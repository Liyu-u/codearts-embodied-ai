from modules.perception.mock_scene import get_mock_scene


def observe_scene(request: dict) -> dict:
    if not isinstance(request, dict):
        raise TypeError("perception request must be an object")
    if request.get("backend", "mock") != "mock":
        raise ValueError("phase-one backend must be mock")
    raw = get_mock_scene(request.get("scene_id", ""))
    return {
        "schema_version": "perception.v1",
        "scene_id": raw["scene_id"],
        "coordinate_frame": raw["coordinate_frame"],
        "objects": raw["objects"],
        "execution_context": {"backend": "mock", "scene_revision": "1"},
    }
