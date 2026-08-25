"""tools/orchestrate/validate.py —— 执行证据校验器。

对远程回传的 perception.v1 / execution.v1 调用存量契约校验器
``integration.contract_validation.assert_contract``，并按失败归类
``contract`` 或 ``safety_or_execution``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from integration.contract_validation import assert_contract, ContractValidationError


@dataclass
class EvidenceVerdict:
    passed: bool
    errors: list[str] = field(default_factory=list)
    failure_class: str | None = None
    execution_status: str | None = None


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class EvidenceValidator:
    """校验下载的感知与执行证据，产出可机器读的结论。"""

    def validate_execution(self, path: Path) -> EvidenceVerdict:
        try:
            execution = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            return EvidenceVerdict(
                passed=False,
                errors=[f"execution.json 解析失败: {exc}"],
                failure_class="contract",
            )
        try:
            assert_contract(execution, "execution.v1")
        except ContractValidationError as exc:
            return EvidenceVerdict(
                passed=False,
                errors=[str(exc)],
                failure_class="contract",
                execution_status=execution.get("status"),
            )
        status = execution.get("status")
        if status != "SUCCEEDED":
            return EvidenceVerdict(
                passed=False,
                errors=[f"execution 状态非 SUCCEEDED: {status}"],
                failure_class="safety_or_execution",
                execution_status=status,
            )
        return EvidenceVerdict(
            passed=True,
            errors=[],
            execution_status=status,
        )

    def validate_perception(self, path: Path) -> EvidenceVerdict:
        try:
            perception = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            return EvidenceVerdict(
                passed=False,
                errors=[f"perception.json 解析失败: {exc}"],
                failure_class="contract",
            )
        try:
            assert_contract(perception, "perception.v1")
        except ContractValidationError as exc:
            return EvidenceVerdict(
                passed=False,
                errors=[str(exc)],
                failure_class="contract",
            )
        return EvidenceVerdict(passed=True, errors=[])

    def validate_all(
        self, perception_path: Path | None, execution_path: Path
    ) -> tuple[EvidenceVerdict, EvidenceVerdict | None]:
        execution_verdict = self.validate_execution(execution_path)
        perception_verdict = (
            self.validate_perception(perception_path)
            if perception_path is not None
            else None
        )
        return execution_verdict, perception_verdict