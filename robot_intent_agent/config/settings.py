"""
全局配置管理 — 基于 Pydantic Settings

用法:
    from robot_intent_agent.config.settings import get_settings
    settings = get_settings()
"""

from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """机器人意图理解 Agent 全局配置"""

    # --- 应用 ---
    app_name: str = Field(default="robot_intent_agent", description="应用名")
    app_version: str = Field(default="0.1.0", description="版本号")
    debug: bool = Field(default=False, description="调试模式")

    # --- Memory ---
    memory_backend: str = Field(
        default="mock",
        description="记忆后端: mock | faiss | chroma",
    )
    memory_top_k: int = Field(default=5, description="记忆检索返回数")

    # --- 安全默认约束 ---
    default_max_force_n: float = Field(default=10.0, description="默认最大力 (N)")
    default_max_velocity_ms: float = Field(default=0.3, description="默认最大速度 (m/s)")
    default_min_z_m: float = Field(default=0.02, description="默认最低 Z 高度 (m)")
    default_collision_margin_m: float = Field(default=0.05, description="默认碰撞边距 (m)")

    # --- LLM (预留) ---
    llm_model: str = Field(default="gpt-4o", description="LLM 模型名")
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    model_config = {
        "env_prefix": "RIA_",
        "env_file": ".env",
        "extra": "forbid",
    }


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
