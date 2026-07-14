"""
Pydantic 数据类型验证
同学 D：HTTP 请求/响应格式定义
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    """任务执行请求"""
    raw_text: str = Field(..., description="用户自然语言指令", min_length=1)
    session_id: Optional[str] = Field(None, description="会话标识")
    timeout: float = Field(30.0, description="执行超时时间 (秒)", ge=1.0, le=300.0)


class TaskResponse(BaseModel):
    """任务执行响应"""
    task_id: str = Field(..., description="任务唯一 ID")
    status: str = Field(..., description="执行状态: success / failed / partial")
    intent_parsed: Optional[Dict[str, Any]] = Field(None, description="解析后的意图 JSON")
    generated_code: Optional[str] = Field(None, description="生成的策略代码")
    error_report: Optional[Dict[str, Any]] = Field(None, description="错误诊断报告（如果失败）")
    execution_time_ms: float = Field(0.0, description="执行耗时 (ms)")


class SceneObjectSchema(BaseModel):
    """场景物体"""
    name: str
    position: Dict[str, float]
    bbox: Dict[str, float]
    color: Optional[str] = None


class SceneQueryResponse(BaseModel):
    """场景查询响应"""
    scene_id: str
    timestamp: str
    objects: List[SceneObjectSchema]


class ErrorReportResponse(BaseModel):
    """异常报告响应"""
    error_id: str
    task_id: str
    error_type: str
    message: str
    traceback: Optional[str] = None
    suggested_fix: Optional[str] = None
