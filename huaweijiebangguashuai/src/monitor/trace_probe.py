"""
异常捕获旁路探针
同学 D：监听运行时异常 — 碰撞、抓空、超限等事件
"""

import time
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class TraceEvent:
    """运行时事件记录"""
    timestamp: float
    event_type: str  # COLLISION, GRASP_FAILED, OBJECT_NOT_FOUND, IK_FAILED, JOINT_LIMIT
    task_id: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)


class TraceProbe:
    """运行时探针 — 旁路监控执行过程"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.events: List[TraceEvent] = []
        self.start_time: float = time.time()

    def record(self, event_type: str, message: str, **context) -> TraceEvent:
        """记录一个运行时事件"""
        event = TraceEvent(
            timestamp=time.time(),
            event_type=event_type,
            task_id=self.task_id,
            message=message,
            context=context,
        )
        self.events.append(event)
        return event

    def has_errors(self) -> bool:
        """检查是否有错误级别事件"""
        error_types = {
            "COLLISION_DETECTED",
            "GRASP_FAILED",
            "OBJECT_NOT_FOUND",
            "IK_SOLVE_FAILED",
            "JOINT_LIMIT_EXCEEDED",
        }
        return any(e.event_type in error_types for e in self.events)

    def get_errors(self) -> List[TraceEvent]:
        """获取所有错误事件"""
        return [e for e in self.events if e.event_type.startswith(("COLLISION", "GRASP", "IK", "JOINT", "OBJECT"))]

    def elapsed_ms(self) -> float:
        """任务已执行耗时 (ms)"""
        return (time.time() - self.start_time) * 1000


def wrap_with_probe(func: Callable, probe: TraceProbe) -> Callable:
    """装饰器：为函数包裹探针监控"""
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            return result
        except AssertionError as e:
            probe.record("SAFETY_ASSERTION_FAILED", str(e))
            raise
        except Exception as e:
            probe.record("UNKNOWN_ERROR", str(e), traceback=str(e))
            raise
    return wrapper
