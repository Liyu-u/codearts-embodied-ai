"""
中央数据中转服务 — FastAPI 服务器
同学 D 上传，同学 C（吴昌庆）接入真实 Isaac Sim 执行引擎

负责：
1. 承接来自前端 (src/ui/app.py) 的 HTTP 请求
2. 调用 CodeArts LLM API 生成策略代码
3. 将策略代码下发到 Isaac Sim 仿真引擎执行 (code_loader)
4. 接收 monitor/trace_probe 的异常反馈并触发反思闭环
"""

import sys
import json
import uuid
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 将 src 加入 path
SRC_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SRC_DIR))

# ============================================================
# App 初始化
# ============================================================
app = FastAPI(
    title="具身智能机械臂操作系统 · 中转服务器",
    description="华为揭榜挂帅 — 连通 UI、CodeArts、Isaac Sim、Monitor 的全链路中枢",
    version="0.2.0",
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


class CodeExecuteRequest(BaseModel):
    """队友 B 直接提交 Python 策略代码执行"""
    code: str = Field(..., description="CodeArts 生成的 Python 策略代码", min_length=1)
    task_id: Optional[str] = Field(None, description="任务 ID")


class TaskResponse(BaseModel):
    task_id: str
    status: str  # success | failed | partial
    intent_parsed: Optional[Dict[str, Any]] = None
    generated_code: Optional[str] = None
    execution_log: Optional[str] = None
    error_report: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    elapsed_ms: float = 0.0


class CodeExecuteResponse(BaseModel):
    """策略代码执行结果"""
    task_id: str
    success: bool
    message: str
    result: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None


class PerceptionObservationResponse(BaseModel):
    """perception_observation v1.0.0 格式响应"""
    schema_version: str = "1.0.0"
    message_type: str = "perception_observation"
    observation_id: str = ""
    scene_id: str = ""
    timestamp: int = 0
    clock_domain: str = "unix_utc"
    coordinate_system: str = "robot_base"
    source: Dict[str, Any] = {}
    objects: list = []
    simulation_metadata: Dict[str, Any] = {}


# ============================================================
# 懒加载 — 首次调用时才初始化 robot（避免启动时依赖 Isaac Sim）
# ============================================================
_robot = None


def _get_robot():
    """获取 ExecutionWrapper 单例（Mock 模式，Isaac Sim 不可用时降级）"""
    global _robot
    if _robot is None:
        from isaac.exec_wrapper import ExecutionWrapper
        _robot = ExecutionWrapper()
    return _robot


# ============================================================
# API 端点
# ============================================================
@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy", "version": "0.2.0", "service": "backend-server"}


@app.post("/api/code/execute", response_model=CodeExecuteResponse)
async def execute_code(request: CodeExecuteRequest):
    """
    【队友 B 专用】直接提交 Python 策略代码，在 Isaac Sim 中执行。

    用法（队友 B 的 CodeArts 调用）:
        POST /api/code/execute
        Body: {"code": "def task_main():\n    robot.move_to_pose(...)\n    ..."}

    流水线:
        1. code_validator 三层安全校验 (语法/安全/物理断言)
        2. 注入元 API 命名空间 (move_to_pose, open_gripper, ...)
        3. exec() 执行代码
        4. 调用 task_main() 入口函数
        5. 返回执行结果
    """
    task_id = request.task_id or f"task-{uuid.uuid4().hex[:8]}"
    t_start = time.time()

    from isaac.code_loader import execute_strategy_code
    from isaac.get_scene_json import get_scene_objects

    robot = _get_robot()
    result = execute_strategy_code(request.code, robot, get_scene_objects)

    return CodeExecuteResponse(
        task_id=task_id,
        success=result["success"],
        message=result["message"],
        result=result.get("result"),
        validation=result.get("validation"),
    )


@app.post("/api/task/execute", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """
    【核心端点】全链路任务执行

    流水线:
        1. 意图解析   (调用 intent_parser prompt -> LLM)   — 待 A 接入
        2. 策略生成   (调用 CodeArts -> 生成代码)            — 待 B 接入
        3. 安全校验   (调用 code_validator -> 拦截危险)     — 已接入
        4. 仿真执行   (下发至 Isaac Sim exec_wrapper)      — 已接入
        5. 异常监控   (trace_probe 旁路监听)                — 已接入
        6. 失败时反思 (reflexion -> 重试最多 3 次)          — 待 A+B 完成后接入
    """
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    t_start = time.time()

    # TODO Sprint 2: 接入 A 的意图解析 + B 的 CodeArts 生成
    # 当前阶段：直接返回 Mock 全链路结果供联调测试
    return TaskResponse(
        task_id=task_id,
        status="success",
        intent_parsed={
            "intent_id": task_id,
            "action": "pick_and_place",
            "target_object": "red_cube",
            "confidence": 0.95,
        },
        generated_code="""def task_main():
    objects = get_scene_objects()
    for obj in objects:
        if "red" in obj.name.lower():
            px, py, pz = obj.position
            safe_z = max(pz + 0.15, 0.02)
            move_to_pose(px, py, safe_z)
            open_gripper(0.08)
            move_to_pose(px, py, pz + 0.003)
            close_gripper(5.0)
            move_to_pose(px, py, safe_z)
            move_to_pose(0.2, 0.0, safe_z)
            move_to_pose(0.2, 0.0, 0.03)
            open_gripper(0.08)
            return {"status": "success"}
    return {"status": "failed", "reason": "no red object"}
""",
        execution_log="[IK] 策略执行成功",
        retry_count=0,
        elapsed_ms=(time.time() - t_start) * 1000,
    )


@app.post("/api/task/retry")
async def retry_task(error_report: Dict[str, Any]):
    """
    【闭环关键】收到 trace_probe 的 error_report.json 后，
    触发 CodeArts 反思重写策略代码，并重新执行。
    """
    return {
        "status": "retry_initiated",
        "original_error": error_report.get("error_type", "unknown"),
        "message": "Reflexion 闭环已触发 — 生成修正策略中...",
    }


@app.get("/api/scene/current", response_model=PerceptionObservationResponse)
async def get_current_scene():
    """
    【队友 A 专用】获取当前场景感知结果 (perception_observation v1.0.0)。

    队友 A 的意图解析器调用此接口，获取：
    - 场景中所有可抓取物体的 6D 位姿 + 四元数朝向
    - 每个物体的分类/颜色/形状候选列表（带置信度）
    - 追踪信息（速度、帧数）
    - 仿真真值（用于评测对比）
    """
    from isaac.get_scene_json import get_scene_objects
    import uuid as _uuid

    objects_raw = get_scene_objects()
    objects = [obj.to_dict() for obj in objects_raw]
    ts_ms = int(time.time() * 1000)

    return PerceptionObservationResponse(
        observation_id=f"obs_{ts_ms}_{_uuid.uuid4().hex[:4]}",
        scene_id="table_scene_001",
        timestamp=ts_ms,
        source={
            "module": "perception_pipeline",
            "pipeline_version": "1.0.0",
            "sensor_ids": ["camera_front", "depth_front"],
        },
        objects=objects,
        simulation_metadata={
            "evaluation_only": True,
            "ground_truth_objects": [
                {
                    "object_id": obj.object_id,
                    "prim_path": f"/World/Table/{obj.name.replace(' ', '_')}",
                    "mass_kg": 0.15 if "方块" in obj.name else 0.2,
                    "material": "plastic" if "方块" in obj.name else "glass",
                    "friction": 0.4,
                    "rigid_body": True,
                    "collision_enabled": True,
                }
                for obj in objects_raw
            ],
        },
    )


@app.get("/api/monitor/errors")
async def get_error_history():
    """获取异常报告历史 (从 logs/ 目录下的 error_report_*.json 读取)"""
    log_dir = Path("logs")
    if not log_dir.exists():
        return {"errors": [], "count": 0}

    error_files = sorted(log_dir.glob("error_report_*.json"))
    errors = []
    for ef in error_files[-10:]:
        with open(ef, "r", encoding="utf-8") as f:
            errors.append(json.load(f))

    return {"errors": errors, "count": len(errors)}


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("  具身智能机械臂操作系统中转服务器 v0.2.0")
    print("=" * 55)
    print()
    print("  队友 A (意图解析) -> GET  /api/scene/current")
    print("  队友 B (策略代码) -> POST /api/code/execute")
    print("  队友 D (异常监控) -> GET  /api/monitor/errors")
    print("  全链路          -> POST /api/task/execute")
    print()
    print("  API 文档: http://localhost:8000/docs")
    print("  健康检查: http://localhost:8000/health")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000)
