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
        # Formal B→C requires destination_id. placement_mode is an explicit
        # execution modifier and is only meaningful for stack_on.
        allowed = {"destination_id", "placement_mode"}
        if keys - allowed or "destination_id" not in keys:
            return ["INVALID_ARGUMENT:move_to_target:destination_id is required"]
        placement_mode = arguments.get("placement_mode", "direct")
        if placement_mode not in {"direct", "stack_on"}:
            return [
                "INVALID_ARGUMENT:move_to_target:placement_mode must be direct or stack_on"
            ]
    elif action == "release" and keys:
        return ["INVALID_ARGUMENT:release:arguments must be empty"]
    return []
