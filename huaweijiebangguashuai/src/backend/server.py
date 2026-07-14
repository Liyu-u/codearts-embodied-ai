"""
中央数据中转服务 — FastAPI 服务器
同学 D 上传：连接前端 UI → CodeArts 智能体 → Isaac Sim 仿真引擎

负责：
1. 承接来自前端 (src/ui/app.py) 的 HTTP 请求
2. 调用 CodeArts LLM API 生成策略代码
3. 通过 Socket/HTTP 向 Isaac Sim 仿真引擎下发执行指令
4. 接收 monitor/trace_probe 的异常反馈并触发反思闭环
"""

import sys
import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ============================================================
# App 初始化
# ============================================================
app = FastAPI(
    title="具身智能机械臂操作系统 · 中转服务器",
    description="华为揭榜挂帅 — 连通 UI、CodeArts、Isaac Sim、Monitor 的全链路中枢",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 数据模型 (Pydantic 契约)
# ============================================================
class TaskRequest(BaseModel):
    raw_text: str = Field(..., description="用户自然语言指令", min_length=1)
    session_id: Optional[str] = Field(None, description="会话 ID")
    timeout: float = Field(30.0, description="超时时间 (秒)", ge=1.0, le=300.0)


class TaskResponse(BaseModel):
    task_id: str
    status: str  # success | failed | partial
    intent_parsed: Optional[Dict[str, Any]] = None
    generated_code: Optional[str] = None
    execution_log: Optional[str] = None
    error_report: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    elapsed_ms: float = 0.0


class SceneQueryResponse(BaseModel):
    scene_id: str
    timestamp: str
    objects: list = []


# ============================================================
# API 端点
# ============================================================
@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy", "version": "0.1.0", "service": "backend-server"}


@app.post("/api/task/execute", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """
    【核心端点】全链路任务执行

    流水线:
        1. 意图解析   (调用 intent_parser prompt → LLM)
        2. 策略生成   (调用 CodeArts → 生成代码)
        3. 安全校验   (调用 code_validator → 拦截危险)
        4. 仿真执行   (下发至 Isaac Sim exec_wrapper)
        5. 异常监控   (trace_probe 旁路监听)
        6. 失败时反思 (reflexion → 重试最多 3 次)
    """
    # TODO: Sprint 1 期间 — 返回 Mock 流水线结果
    return TaskResponse(
        task_id="task-mock-001",
        status="success",
        intent_parsed={
            "intent_id": "task-mock-001",
            "action": "pick_and_place",
            "target_object": "红色方块",
            "confidence": 0.95,
        },
        generated_code="""def task_mock():
    robot.move_to_pose(0.15, 0.05, 0.15, 0, 0, 0)
    robot.close_gripper(5.0)
""",
        execution_log="[IK] 移动到 (0.15, 0.05, 0.15)... [GRIPPER] 闭合 5.0N... OK",
        retry_count=0,
        elapsed_ms=1234.5,
    )


@app.post("/api/task/retry")
async def retry_task(error_report: Dict[str, Any]):
    """
    【闭环关键】收到 trace_probe 的 error_report.json 后，
    触发 CodeArts 反思重写策略代码，并重新执行。
    """
    # TODO: 实现 Reflexion 闭环
    return {
        "status": "retry_initiated",
        "original_error": error_report.get("error_type", "unknown"),
        "message": "Reflexion 闭环已触发 — 生成修正策略中...",
    }


@app.get("/api/scene/current", response_model=SceneQueryResponse)
async def get_current_scene():
    """获取最新场景状态 (从 logs/scene_state.json 读取)"""
    log_file = Path("logs/scene_state.json")
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return SceneQueryResponse(
        scene_id="unknown",
        timestamp="N/A",
        objects=[],
    )


@app.get("/api/monitor/errors")
async def get_error_history():
    """获取异常报告历史 (从 logs/ 目录下的 error_report_*.json 读取)"""
    log_dir = Path("logs")
    if not log_dir.exists():
        return {"errors": [], "count": 0}

    error_files = sorted(log_dir.glob("error_report_*.json"))
    errors = []
    for ef in error_files[-10:]:  # 最近 10 条
        with open(ef, "r", encoding="utf-8") as f:
            errors.append(json.load(f))

    return {"errors": errors, "count": len(errors)}


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("🚀 中央中转服务器启动中...")
    print("   📡 API 文档: http://localhost:8000/docs")
    print("   🏥 健康检查: http://localhost:8000/health")
    uvicorn.run(app, host="0.0.0.0", port=8000)
