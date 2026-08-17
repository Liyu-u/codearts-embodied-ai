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
        if keys != {"object_id"}:
            return ["INVALID_ARGUMENT:detect_object:object_id is required"]
    elif action in {"move_to_object", "grasp"}:
        if keys != {"object_id"}:
            return [f"INVALID_ARGUMENT:{action}:object_id is required"]
    elif action == "move_to_target":
        if keys != {"destination_id"}:
            return ["INVALID_ARGUMENT:move_to_target:destination_id is required"]
    elif action == "release" and keys:
        return ["INVALID_ARGUMENT:release:arguments must be empty"]
    return []
