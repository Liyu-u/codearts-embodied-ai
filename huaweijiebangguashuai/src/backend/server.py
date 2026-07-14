"""
FastAPI 中转服务器主入口
同学 D：中央数据路由 — 连接 UI → Agent → Isaac → Monitor 全链路
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .schemas import (
    TaskRequest,
    TaskResponse,
    SceneQueryResponse,
    ErrorReportResponse,
)

app = FastAPI(
    title="具身智能机械臂操作系统 API",
    description="华为揭榜挂帅 — 多模块协作中转服务器",
    version="0.1.0",
)

# CORS: 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "version": "0.1.0"}


@app.post("/api/task/execute", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """
    核心任务执行端点
    接收用户自然语言指令 → 返回执行结果
    """
    # TODO: 串联完整流水线
    # 1. intent_parser: 口语 → JSON
    # 2. codearts_client: JSON → 策略代码
    # 3. code_validator: 安全校验
    # 4. exec_wrapper: 物理执行
    # 5. trace_probe: 异常监控
    raise HTTPException(status_code=501, detail="流水线尚未接通")


@app.get("/api/scene/current", response_model=SceneQueryResponse)
async def get_current_scene():
    """获取当前场景状态"""
    # TODO: 调用 get_scene_json.export_scene_json()
    raise HTTPException(status_code=501, detail="待实现")


@app.get("/api/monitor/errors", response_model=list[ErrorReportResponse])
async def get_error_reports():
    """获取异常报告历史"""
    # TODO: 返回 trace_probe 收集的异常
    return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
