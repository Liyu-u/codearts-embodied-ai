"""
华为云 CodeArts 智能体 API 调用封装
同学 B：封装与华为云 CodeArts 的 API 通信
"""

import os
from typing import Any, Dict, Optional

import requests


class CodeArtsClient:
    """华为云 CodeArts 智能体客户端"""

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CODEARTS_API_KEY", "")
        self.endpoint = endpoint or os.environ.get(
            "CODEARTS_ENDPOINT",
            "https://codearts-api.huaweicloud.com/v1",
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    def generate_policy(
        self, intent_json: Dict[str, Any], system_prompt: str
    ) -> Dict[str, Any]:
        """
        调用 CodeArts 生成控制策略

        Args:
            intent_json: 意图解析器输出的结构化 JSON
            system_prompt: CodeArts 策略生成 System Prompt

        Returns:
            dict: 包含 generated_code, status, usage 等字段
        """
        payload = {
            "model": "codearts-policy-v1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(intent_json)},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }

        response = self.session.post(
            f"{self.endpoint}/generate", json=payload, timeout=60
        )
        response.raise_for_status()
        return response.json()

    def validate_connection(self) -> bool:
        """测试 API 连接是否正常"""
        try:
            resp = self.session.get(f"{self.endpoint}/health", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False
