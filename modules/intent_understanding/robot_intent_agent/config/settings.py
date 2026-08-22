"""
全局配置管理 — 基于 Pydantic Settings

用法:
    from robot_intent_agent.config.settings import get_settings
    settings = get_settings()
"""

from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _REPO_ROOT / ".env"


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

    # ============================================================
    # DeepSeek API 配置
    # ============================================================
    deepseek_api_key: str = Field(
        default="",
        description="DeepSeek API Key (环境变量: RIA_DEEPSEEK_API_KEY)",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        description="DeepSeek API 地址",
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        description="DeepSeek 模型（正式运行建议使用 deepseek-v4-flash）",
    )
    deepseek_temperature: float = Field(
        default=0.0, ge=0.0, le=2.0,
        description="LLM 温度参数",
    )
    deepseek_max_tokens: int = Field(
        default=2400,
        description="最大输出 token 数",
    )
    deepseek_timeout_s: float = Field(
        default=20.0,
        description="API 调用超时 (秒)",
    )
    deepseek_max_retries: int = Field(
        default=1,
        description="API 调用失败重试次数",
    )
    deepseek_thinking: str = Field(
        default="disabled",
        description="思考模式: enabled | disabled；语义 JSON 快路径默认关闭",
    )
    deepseek_reasoning_effort: str = Field(
        default="low",
        description="思考强度: low | high | max（仅 thinking=enabled 生效）",
    )
    llm_cache_enabled: bool = Field(
        default=True,
        description="是否缓存不含物理实体ID的语义候选",
    )
    llm_cache_max_entries: int = Field(
        default=128,
        ge=0,
        le=4096,
        description="进程内语义候选缓存上限",
    )
    llm_failure_policy: str = Field(
        default="fallback",
        description="LLM 传输/配置失败策略: fallback | block（正式 llm 模式建议 block）",
    )

    # --- 混合路由配置 ---
    planner_engine: str = Field(
        default="rule",
        description="规划引擎: rule (纯规则) | llm (纯LLM) | hybrid (规则优先+LLM兜底)",
    )
    llm_fallback_on_low_confidence: bool = Field(
        default=True,
        description="规则引擎低置信度时是否回退到 LLM",
    )
    rule_confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="规则引擎置信度阈值（低于此值启用 LLM）",
    )

    # --- 执行安全配置（供 integration/config 覆盖 local/sim/real profile） ---
    deployment_domain: str = Field(
        default="daily",
        description="执行安全域: daily | industrial",
    )
    daily_max_force_n: float = Field(default=10.0, description="日常域最大夹爪力 (N)")
    daily_max_velocity_ms: float = Field(default=0.30, description="日常域最大线速度 (m/s)")
    industrial_max_force_n: float = Field(default=8.0, description="工业域最大夹爪力 (N)")
    industrial_max_velocity_ms: float = Field(default=0.15, description="工业域最大线速度 (m/s)")

    model_config = {
        "env_prefix": "RIA_",
        # Resolve from the repository, not the process working directory.
        # This keeps the frontend demo and IDE/CLI launches consistent.
        "env_file": str(_ENV_FILE),
        "extra": "forbid",
    }

    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key)


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()


def resolve_deepseek_api_key(key_override: str = "") -> str:
    """Resolve a DeepSeek key consistently without exposing it in source code."""
    return key_override.strip() or get_settings().deepseek_api_key.strip()
