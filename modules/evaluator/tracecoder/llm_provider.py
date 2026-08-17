"""统一 LLM Provider —— 把 TraceCoder 的模型调用与外部实现彻底解耦。

原版代码在 src.generation 里用 openai 包 + 顶层 import torch/transformers，
在统一联调仓库中该依赖不存在，LLM 增强因此『必然静默回退到规则』。本模块
重写为 **零第三方依赖** 的 OpenAI 兼容 REST 客户端（stdlib urllib）：

- 本地与 CI 都不用安装 openai / torch / transformers；
- 未配置 API Key 时只返回明确的错误结果（不构造假请求、不炸进程）；
- 结构化 JSON 输出：请求带 response_format=json_object，响应做容错解析。

环境变量（TRACECODER_LLM_*，API Key 显式留空占位、用时再填，禁止入库）：
    TRACECODER_LLM_MODE        off | optional | required（默认 off）
    TRACECODER_LLM_MODEL       模型名（默认空 = 未配置）
    TRACECODER_LLM_BASE_URL    OpenAI 兼容端点（默认 https://api.deepseek.com）
    TRACECODER_LLM_API_KEY     API Key（默认空）
    TRACECODER_LLM_TIMEOUT_S   请求超时秒（默认 45）
    TRACECODER_LLM_MAX_RETRIES 失败重试次数（默认 2）
    TRACECODER_LLM_TEMPERATURE 采样温度（默认 0.2）
    TRACECODER_LLM_MAX_TOKENS  最大输出 token（默认 2000）
    TRACECODER_LLM_JSON_MODE   是否请求结构化 JSON 输出（默认 true）

支持在本地 .env 填 TRACECODER_LLM_API_KEY（python-dotenv 已装，CI 无该包时
通过 try/except 跳过，零依赖原则不被破坏）。
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass
from typing import Optional

import urllib.error
import urllib.request

_ENV_PREFIX = "TRACECODER_LLM_"
_DEFAULT_BASE_URL = "https://api.deepseek.com"

VALID_MODES = ("off", "optional", "required")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_json_output(text: str) -> Optional[dict]:
    """从模型输出里抽取一个 JSON 对象（容忍 ```json 代码块与前后噪声）。

    等价于原 agents._extract_json：先整体解析，失败再取第一个 {...}。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class LLMConfig:
    """LLM 调用的静态配置。所有字段都允许留空/默认，缺配置时 only 报错不炸。"""

    model: str = ""
    base_url: str = _DEFAULT_BASE_URL
    api_key: str = ""
    timeout_s: float = 45.0
    max_retries: int = 2
    temperature: float = 0.2
    max_tokens: int = 2000
    json_mode: bool = True
    mode: str = "off"  # off | optional | required

    @classmethod
    def from_env(cls) -> "LLMConfig":
        mode = os.getenv(_ENV_PREFIX + "MODE", "off").strip().lower()
        if mode not in VALID_MODES:
            mode = "off"
        return cls(
            mode=mode,
            model=os.getenv(_ENV_PREFIX + "MODEL", "").strip(),
            base_url=os.getenv(_ENV_PREFIX + "BASE_URL", _DEFAULT_BASE_URL).strip(),
            api_key=os.getenv(_ENV_PREFIX + "API_KEY", "").strip(),
            timeout_s=_env_float(_ENV_PREFIX + "TIMEOUT_S", 45.0),
            max_retries=_env_int(_ENV_PREFIX + "MAX_RETRIES", 2),
            temperature=_env_float(_ENV_PREFIX + "TEMPERATURE", 0.2),
            max_tokens=_env_int(_ENV_PREFIX + "MAX_TOKENS", 2000),
            json_mode=_env_bool(_ENV_PREFIX + "JSON_MODE", True),
        )

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

    def health_info(self) -> dict:
        return {
            "mode": self.mode,
            "model": self.model or "未配置",
            "base_url": self.base_url,
            "key_configured": self.key_configured,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
        }


@dataclass
class LLMResult:
    """一次模型调用的结果与证据（含耗时/用量/请求号）。"""

    ok: bool
    text: str = ""
    json: Optional[dict] = None
    model: str = ""
    request_id: str = ""
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    def to_record(
        self,
        seq: int,
        role: str,
        mode: str,
        status: str = "ok",
        used_fallback: bool = False,
        round_: Optional[int] = None,
    ) -> dict:
        """转成调用证据记录（模型名/请求编号/耗时/是否回退）。"""
        return {
            "seq": seq,
            "round": round_,
            "role": role,
            "mode": mode,
            "model": self.model,
            "request_id": self.request_id,
            "status": status,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "used_fallback": used_fallback,
        }


class LLMProvider:
    """OpenAI 兼容 REST 客户端（stdlib urllib，零第三方依赖）。"""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig.from_env()

    # ------------------------------------------------------------------
    # 统一调用入口
    # ------------------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        json_mode: Optional[bool] = None,
    ) -> LLMResult:
        cfg = self.config
        if not cfg.api_key:
            return LLMResult(
                ok=False,
                model=cfg.model,
                error="未配置 TRACECODER_LLM_API_KEY（本地 .env 或环境变量填好后启用）",
            )
        if not cfg.model:
            return LLMResult(
                ok=False,
                model=cfg.model,
                error="未配置 TRACECODER_LLM_MODEL",
            )

        want_json = cfg.json_mode if json_mode is None else json_mode
        payload = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }
        if want_json:
            payload["response_format"] = {"type": "json_object"}

        endpoint = cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg.api_key,
        }
        body = json.dumps(payload).encode("utf-8")

        last_error = ""
        for attempt in range(cfg.max_retries + 1):
            started = time.time()
            retryable = False
            try:
                request = urllib.request.Request(
                    endpoint, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(request, timeout=cfg.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                latency_ms = (time.time() - started) * 1000.0
                data = json.loads(raw)
                choice = (data.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                usage = data.get("usage") or {}
                return LLMResult(
                    ok=True,
                    text=content,
                    json=parse_json_output(content) if want_json else None,
                    model=cfg.model,
                    request_id=data.get("id", ""),
                    latency_ms=latency_ms,
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                )
            except urllib.error.HTTPError as exc:
                last_error = "HTTP {}: {}".format(exc.code, exc.reason)
                retryable = exc.code in (408, 429) or exc.code >= 500
            except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
                last_error = "{}: {}".format(type(exc).__name__, exc)
                retryable = True
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                last_error = "响应解析失败: {}".format(exc)
                retryable = False

            if not retryable or attempt >= cfg.max_retries:
                break
            time.sleep(0.5 * (2 ** attempt))

        return LLMResult(
            ok=False,
            model=cfg.model,
            error="模型调用失败（已重试 {} 次）：{}".format(cfg.max_retries, last_error),
        )


def try_load_dotenv() -> None:
    """可选加载本地 .env（python-dotenv 未安装时静默跳过，保持零依赖）。"""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except ImportError:
        pass


# 模块加载即尝试读一次 .env，保证 from_env() 能拿到本地配置。
try_load_dotenv()
