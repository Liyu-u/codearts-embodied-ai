"""
报告生成器
同学 D：标准化输出 error_report.json — 供反思闭环使用
"""

import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

from .trace_probe import TraceProbe, TraceEvent


@dataclass
class ErrorReport:
    """标准化错误报告"""
    error_id: str
    task_id: str
    error_type: str
    message: str
    traceback: Optional[str] = None
    suggested_fix: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self, filepath: str) -> None:
        """导出为标准 error_report.json"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)


class ErrorReportGenerator:
    """根据 TraceProbe 采集的事件生成标准化错误报告"""

    FIX_SUGGESTIONS = {
        "COLLISION_DETECTED": {
            "message": "检测到碰撞风险",
            "fix": "提高路径安全高度，添加中间路径点绕开障碍物",
        },
        "GRASP_FAILED": {
            "message": "抓取失败",
            "fix": "微调末端执行器位姿，检查夹爪力度是否足够",
        },
        "OBJECT_NOT_FOUND": {
            "message": "目标物体未找到",
            "fix": "刷新场景感知，确认物体未被遮挡或移走",
        },
        "IK_SOLVE_FAILED": {
            "message": "逆运动学求解失败",
            "fix": "调整末端姿态角度，或改用关节空间运动",
        },
        "JOINT_LIMIT_EXCEEDED": {
            "message": "关节限位超出",
            "fix": "调整基座位置或重新规划运动路径",
        },
        "SAFETY_ASSERTION_FAILED": {
            "message": "安全断言失败",
            "fix": "检查 Z 轴高度是否满足安全约束 (z >= 0.02m)",
        },
    }

    @classmethod
    def generate(cls, probe: TraceProbe, task_id: str) -> Optional[ErrorReport]:
        """根据探针采集的事件生成错误报告"""
        errors = probe.get_errors()
        if not errors:
            return None

        # 取第一个错误作为主错误
        main_error = errors[0]
        suggestion = cls.FIX_SUGGESTIONS.get(
            main_error.event_type,
            {"message": "未知错误", "fix": "请人工介入检查"},
        )

        return ErrorReport(
            error_id=f"err-{task_id}-{len(errors)}",
            task_id=task_id,
            error_type=main_error.event_type,
            message=suggestion["message"],
            suggested_fix=suggestion["fix"],
            context=main_error.context,
            events=[{
                "timestamp": e.timestamp,
                "type": e.event_type,
                "message": e.message,
            } for e in errors],
        )
