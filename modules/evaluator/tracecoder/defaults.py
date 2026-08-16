"""Default meta-API descriptions used by the lightweight simulator."""

DEFAULT_API_CATALOG = {
    "detect_object": {
        "required_arguments": {"object_name": "string"},
        "description": "Find a visible object and return its id and position.",
        "duration_ms": 400,
    },
    "move_to_object": {
        "required_arguments": {"object_id": "string"},
        "description": "Move the robot close enough to interact with an object.",
        "duration_ms": 0,  # 0 表示按 距离/速度 动态计算
    },
    "move_to_target": {
        "required_arguments": {"target": "string"},
        "description": "Move the robot to a named target.",
        "duration_ms": 0,  # 0 表示按 距离/速度 动态计算
    },
    "grasp": {
        "required_arguments": {"object_id": "string"},
        "description": "Close the gripper around a reachable object.",
        "duration_ms": 800,
    },
    "release": {
        "required_arguments": {},
        "description": "Release the currently held object.",
        "duration_ms": 400,
    },
    "rotate": {
        "required_arguments": {"object_id": "string", "angle": "number"},
        "description": "Rotate the currently grasped object by a given angle (degrees).",
        "duration_ms": 600,
    },
    "sweep": {
        "required_arguments": {"object_id": "string", "target_area": "string"},
        "description": "Sweep an object into a target area with the spatula.",
        "duration_ms": 900,
    },
    "stop": {
        "required_arguments": {},
        "description": "Stop policy execution safely.",
        "duration_ms": 50,
    },
}

# 移动基准速度（米/秒），用于把移动距离折算成执行时长。
MOVE_SPEED = 0.8

DEFAULT_SAFETY_RULES = {
    "max_speed": 1.0,
    "max_api_calls": 50,
    "forbid_collision": True,
    "forbid_human_zone": True,
}
