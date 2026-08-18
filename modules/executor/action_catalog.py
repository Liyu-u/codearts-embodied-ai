ALLOWED_ACTIONS = {
    "detect_object",
    "move_to_object",
    "grasp",
    "move_to_target",
    "release",
}


def validate_action_arguments(action: str, arguments: dict) -> list[str]:
    if action not in ALLOWED_ACTIONS:
        return [f"UNKNOWN_ACTION:{action}"]
    if not isinstance(arguments, dict):
        return [f"INVALID_ARGUMENT:{action}:arguments must be an object"]
    keys = set(arguments)
    if action == "detect_object":
        allowed = {"object_id", "object_name"}
        if len(keys & allowed) != 1 or keys - allowed:
            return [
                "INVALID_ARGUMENT:detect_object:use exactly one object_id or object_name"
            ]
    elif action in {"move_to_object", "grasp"}:
        if keys != {"object_id"}:
            return [f"INVALID_ARGUMENT:{action}:object_id is required"]
    elif action == "move_to_target":
        allowed = {"destination_id", "target", "placement_mode"}
        target_keys = keys & {"destination_id", "target"}
        if len(target_keys) != 1 or keys - allowed:
            return [
                "INVALID_ARGUMENT:move_to_target:use exactly one destination_id or target"
            ]
        placement_mode = arguments.get("placement_mode", "direct")
        if placement_mode not in {"direct", "stack_on"}:
            return [
                "INVALID_ARGUMENT:move_to_target:placement_mode must be direct or stack_on"
            ]
    elif action == "release" and keys:
        return ["INVALID_ARGUMENT:release:arguments must be empty"]
    return []
