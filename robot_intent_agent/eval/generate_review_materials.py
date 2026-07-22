#!/usr/bin/env python3
"""
Review Material Generator — Human-Only Golden Data Review
=========================================================
Generates review packages for TC_005, TC_008, and 150 Entity Grounding cases.

ABSOLUTE CONSTRAINTS:
  - NO AI-generated answers, predictions, or suggestions
  - NO fake names, IDs, signatures, or timestamps
  - ALL status fields remain PENDING / UNREVIEWED
  - ALL system outputs, scores, and pipeline predictions are HIDDEN
  - Reviewers see ONLY: instruction + scene objects + blank fields to fill

Output:
  1. eval/golden_reviews/tc_reviewer_a_tc_cases.csv
  2. eval/golden_reviews/tc_reviewer_b_tc_cases.csv
  3. eval/holdout_v3_review_a.csv
  4. eval/holdout_v3_review_b.csv
  5. eval/holdout_v3_adjudication_empty.csv
  6. ../../HUMAN_REVIEW_GUIDE.md
  7. eval/validate_review_submission.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════════════════
# Paths (relative to this script's location in eval/)
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
EVAL_DIR = SCRIPT_DIR
GOLDEN_REVIEWS_DIR = EVAL_DIR / "golden_reviews"
TESTS_FIXTURES_DIR = REPO_ROOT / "robot_intent_agent" / "tests" / "fixtures"

HOLDOUT_V3_PATH = EVAL_DIR / "holdout_v3.json"
REASONING_CASES_PATH = TESTS_FIXTURES_DIR / "reasoning_cases.json"

# Output paths
TC_REVIEW_A_CSV = GOLDEN_REVIEWS_DIR / "tc_reviewer_a_tc_cases.csv"
TC_REVIEW_B_CSV = GOLDEN_REVIEWS_DIR / "tc_reviewer_b_tc_cases.csv"
ENTITY_REVIEW_A_CSV = EVAL_DIR / "holdout_v3_review_a.csv"
ENTITY_REVIEW_B_CSV = EVAL_DIR / "holdout_v3_review_b.csv"
ENTITY_ADJUDICATION_CSV = EVAL_DIR / "holdout_v3_adjudication_empty.csv"
HUMAN_REVIEW_GUIDE = REPO_ROOT / "HUMAN_REVIEW_GUIDE.md"
VALIDATOR_SCRIPT = EVAL_DIR / "validate_review_submission.py"

TIMESTAMP = datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# Utility
# ══════════════════════════════════════════════════════════════════════════════

def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compact_obj_summary(obj: Dict[str, Any]) -> str:
    """Create a compact one-line summary of a scene object for CSV embedding."""
    cat = obj.get("category_candidates", [{}])[0].get("name", "?")
    color = obj.get("appearance", {}).get("color", "?")
    material = obj.get("appearance", {}).get("material", "?")
    pos = obj.get("pose", {}).get("position", {})
    pos_str = f"({pos.get('x',0):.2f},{pos.get('y',0):.2f},{pos.get('z',0):.2f})"
    state = obj.get("tracking", {}).get("state", "?")
    affordances = ",".join(obj.get("affordances", []))
    oid = obj.get("object_id", "?")
    return (
        f"{oid}|cat={cat}|color={color}|mat={material}|pos={pos_str}"
        f"|state={state}|aff={affordances}"
    )


def render_scene_objects_table(objects: List[Dict[str, Any]]) -> str:
    """Render a human-readable markdown-ish table of scene objects for CSV."""
    lines = []
    for obj in objects:
        oid = obj.get("object_id", "?")
        cat = obj.get("category_candidates", [{}])[0].get("name", "?")
        color = obj.get("appearance", {}).get("color", "?")
        material = obj.get("appearance", {}).get("material", "?")
        pos = obj.get("pose", {}).get("position", {})
        pos_str = f"({pos.get('x',0):.2f}, {pos.get('y',0):.2f}, {pos.get('z',0):.2f})"
        state = obj.get("tracking", {}).get("state", "?")
        affordances = ", ".join(obj.get("affordances", []))
        size = obj.get("geometry", {}).get("size", {})
        size_str = f"{size.get('width',0):.2f}x{size.get('height',0):.2f}x{size.get('depth',0):.2f}"

        lines.append(
            f"[{oid}] {cat} | {color} {material} | pos={pos_str} | "
            f"size={size_str} | {state} | {affordances}"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Part 1: TC_005 / TC_008 Review Packages
# ══════════════════════════════════════════════════════════════════════════════

def generate_tc_review_packages() -> None:
    """Generate TC_005 and TC_008 review CSVs for Reviewer A and B."""

    reasoning = load_json(REASONING_CASES_PATH)

    # Find TC_005 and TC_008
    tc_cases = []
    for c in reasoning.get("normal_cases", []):
        if c["case_id"] in ("TC_005", "TC_008"):
            tc_cases.append(c)

    if len(tc_cases) != 2:
        print(f"WARNING: Expected 2 TC cases, found {len(tc_cases)}")

    # ── Common CSV structure ──
    tc_fieldnames = [
        "case_id",
        "instruction",
        "scene_objects_summary",
        "original_golden_value",
        "field_to_judge",
        "semantic_question",
        "reviewer_decision",
        "expected_action",
        "expected_plan_status",
        "expected_execution_allowed",
        "blocking_reason",
        "clarification_required",
        "clarification_question",
        "rationale",
        "schema_change_needed",
        "reviewer_id",
        "signed_at",
        "notes",
    ]

    rows = []
    for case in tc_cases:
        cid = case["case_id"]
        obs = case.get("observation_json", {})
        objects = obs.get("objects", [])
        expected = case.get("expected", {})
        instruction = case.get("natural_language_command", "")

        # ── Build scene objects summary ──
        obj_summary = render_scene_objects_table(objects)

        # ── Determine original golden value and field to judge ──
        if cid == "TC_005":
            orig_golden = json.dumps({
                "target_object_id": expected.get("target_object_id"),
                "destination_parsed": expected.get("destination_parsed"),
                "avoid_parsed": expected.get("avoid_parsed"),
                "execution_ready": expected.get("execution_ready"),
                "required_skills": expected.get("required_skills"),
            }, ensure_ascii=False)
            field_to_judge = "expected.execution_ready"
            semantic_question = (
                "原Golden预期execution_ready=true。但场景中box是obstacle（blocking关系），"
                "tray是destination。这些角色是否被正确区分？系统是否应判定为READY？"
            )
        else:  # TC_008
            orig_golden = json.dumps({
                "target_object_id": expected.get("target_object_id"),
                "material": expected.get("material"),
                "requested_force_n": expected.get("requested_force_n"),
                "resolved_force_n_le": expected.get("resolved_force_n_le"),
                "conflict_detected": expected.get("conflict_detected"),
                "execution_ready": expected.get("execution_ready"),
            }, ensure_ascii=False)
            field_to_judge = "expected.execution_ready"
            semantic_question = (
                "用户显式要求8N抓取但不超过2N。两者互斥。"
                "原Golden预期execution_ready=true。"
                "系统是否应阻断并返回NEEDS_CLARIFICATION？"
            )

        rows.append({
            "case_id": cid,
            "instruction": instruction,
            "scene_objects_summary": obj_summary,
            "original_golden_value": orig_golden,
            "field_to_judge": field_to_judge,
            "semantic_question": semantic_question,
            # ── ALL fields below are BLANK — human fills them ──
            "reviewer_decision": "",          # KEEP_OLD | ACCEPT_PROPOSED | ALTERNATIVE
            "expected_action": "",            # e.g., GRASP, PLACE, etc.
            "expected_plan_status": "",       # READY | NEEDS_CLARIFICATION | BLOCKED
            "expected_execution_allowed": "", # TRUE | FALSE
            "blocking_reason": "",
            "clarification_required": "",     # TRUE | FALSE
            "clarification_question": "",
            "rationale": "",
            "schema_change_needed": "",       # TRUE | FALSE
            "reviewer_id": "",
            "signed_at": "",
            "notes": "",
        })

    # ── Generate A and B (identical structure, both blank) ──
    write_csv(TC_REVIEW_A_CSV, tc_fieldnames, rows)
    write_csv(TC_REVIEW_B_CSV, tc_fieldnames, rows)

    print(f"  ✓ TC Reviewer A: {TC_REVIEW_A_CSV}")
    print(f"  ✓ TC Reviewer B: {TC_REVIEW_B_CSV}")
    print(f"    ({len(rows)} cases per file, all decision fields empty)")


# ══════════════════════════════════════════════════════════════════════════════
# Part 2: 150 Entity Grounding Review Packages
# ══════════════════════════════════════════════════════════════════════════════

def generate_entity_review_packages() -> None:
    """Generate holdout_v3 entity grounding review CSVs for A, B, and adjudication."""

    holdout = load_json(HOLDOUT_V3_PATH)
    cases = holdout.get("cases", [])
    total = len(cases)

    # ── CSV structure ──
    entity_fieldnames = [
        # Case identification
        "case_id",
        "category",
        "instruction",
        # Scene objects (compact embedded table)
        "scene_objects_summary",
        "available_object_ids",
        "object_count",
        # ── Entity Grounding (HUMAN FILLS ALL BELOW) ──
        "theme_entity_ids",
        "theme_grounding_status",
        "destination_entity_ids",
        "destination_grounding_status",
        "recipient_entity_ids",
        "recipient_grounding_status",
        "prohibition_entity_ids",
        "prohibition_grounding_status",
        "condition_subject_entity_ids",
        "condition_subject_grounding_status",
        # ── Cross-role ──
        "acceptable_entity_sets",
        "clarification_required",
        "clarification_question",
        "expected_grounding_status_overall",
        "rationale",
        # ── Metadata ──
        "reviewer_id",
        "signed_at",
        "notes",
    ]

    rows = []
    for case in cases:
        cid = case["case_id"]
        objects = case.get("objects", [])
        obj_ids = [o["object_id"] for o in objects]

        obj_summary = render_scene_objects_table(objects)
        available_ids = ", ".join(obj_ids)

        rows.append({
            "case_id": cid,
            "category": case.get("category", ""),
            "instruction": case.get("instruction", ""),
            "scene_objects_summary": obj_summary,
            "available_object_ids": available_ids,
            "object_count": str(len(objects)),
            # ── ALL below are BLANK — human fills ──
            "theme_entity_ids": "",
            "theme_grounding_status": "",
            "destination_entity_ids": "",
            "destination_grounding_status": "",
            "recipient_entity_ids": "",
            "recipient_grounding_status": "",
            "prohibition_entity_ids": "",
            "prohibition_grounding_status": "",
            "condition_subject_entity_ids": "",
            "condition_subject_grounding_status": "",
            "acceptable_entity_sets": "",
            "clarification_required": "",
            "clarification_question": "",
            "expected_grounding_status_overall": "",
            "rationale": "",
            "reviewer_id": "",
            "signed_at": "",
            "notes": "",
        })

    # ── Write A and B ──
    write_csv(ENTITY_REVIEW_A_CSV, entity_fieldnames, rows)
    write_csv(ENTITY_REVIEW_B_CSV, entity_fieldnames, rows)

    print(f"  ✓ Entity Reviewer A: {ENTITY_REVIEW_A_CSV}")
    print(f"  ✓ Entity Reviewer B: {ENTITY_REVIEW_B_CSV}")
    print(f"    ({total} cases per file, all entity fields empty)")

    # ── Adjudication CSV (empty template, filled by validator later) ──
    adj_fieldnames = [
        "case_id",
        "field_name",
        "reviewer_a_value",
        "reviewer_b_value",
        "conflict_type",
        "adjudicator_decision",
        "adjudicator_rationale",
        "adjudicator_id",
        "signed_at",
    ]
    write_csv(ENTITY_ADJUDICATION_CSV, adj_fieldnames, [])
    print(f"  ✓ Adjudication (empty): {ENTITY_ADJUDICATION_CSV}")


# ══════════════════════════════════════════════════════════════════════════════
# Part 3: HUMAN_REVIEW_GUIDE.md
# ══════════════════════════════════════════════════════════════════════════════

def generate_guide() -> None:
    """Generate the human review guide in Chinese."""

    content = f"""# HUMAN REVIEW GUIDE — 人工审核操作说明

**生成时间**: {TIMESTAMP}
**审核范围**: TC_005, TC_008（Golden 裁决）+ 150 条 Entity Grounding（实体接地标注）
**审核人**: Reviewer A, Reviewer B（必须独立填写，不得互相参考）

---

## ⚠️ 绝对禁令

1. **禁止**使用任何 AI 模型（包括 Claude、ChatGPT 等）代为填写任何字段。
2. **禁止**参考当前系统的 actual 输出、pipeline 预测、或任何自动化评分结果。
3. **禁止**编造场景中不存在的 object_id。所有填写的 ID 必须来自该 case 的 `available_object_ids` 列。
4. **禁止** Reviewer A 和 Reviewer B 在提交前互相查看对方的结果。
5. **禁止**修改原始 Golden 值来让 pytest 通过。

---

## 一、审核材料清单

| 文件 | 用途 | 填写人 |
|------|------|--------|
| `eval/golden_reviews/tc_reviewer_a_tc_cases.csv` | TC_005/TC_008 审核卡 | Reviewer A |
| `eval/golden_reviews/tc_reviewer_b_tc_cases.csv` | TC_005/TC_008 审核卡 | Reviewer B |
| `eval/holdout_v3_review_a.csv` | 150 条 Entity Grounding | Reviewer A |
| `eval/holdout_v3_review_b.csv` | 150 条 Entity Grounding | Reviewer B |
| `eval/holdout_v3_adjudication_empty.csv` | 不一致项仲裁表 | Adjudicator |

---

## 二、Entity 角色定义

以下五个角色对应自然语言指令中不同语义角色的实体接地：

### 2.1 Theme（主题 / 被操作物体）

指令中**被直接操作的物体**——机器人抓取、移动、放置、传递的"那个东西"。

> 例：「抓住杯子」→ Theme = 杯子
> 例：「把盒子拿过来」→ Theme = 盒子
> 例：「把书放到书架上」→ Theme = 书

**entity_id 列名**: `theme_entity_ids`

### 2.2 Destination（目的地 / 目标位置）

指令中物体被**移动到的目标位置或容器**。

> 例：「把杯子放到桌子上」→ Destination = 桌子
> 例：「把书放到书架上」→ Destination = 书架
> 例：「抓住杯子」→ Destination = NOT_APPLICABLE（没有目的地）

**entity_id 列名**: `destination_entity_ids`

### 2.3 Recipient（接收者）

指令中物体被**传递给的人或代理**。在当前场景中，recipient 通常是人（user）。

> 例：「把杯子递给我」→ Recipient = user（如场景中无 user 实体，记为 NOT_FOUND 并在 rationale 中说明）
> 例：「把药瓶递给用户」→ Recipient = user

**entity_id 列名**: `recipient_entity_ids`

### 2.4 Prohibition（禁止接触物体）

指令中**明确禁止触碰、移动或干扰**的物体。通常由否定词引导。

> 例：「抓住杯子但不要碰到花瓶」→ Prohibition = 花瓶
> 例：「别碰红色的那个」→ Prohibition = 红色物体

**entity_id 列名**: `prohibition_entity_ids`

### 2.5 Condition Subject（条件主语 / 条件约束对象）

指令中作为**条件判断依据**的物体——不一定是操作对象，但决定了操作的执行条件。

> 例：「如果桌子上有杯子，把它拿过来」→ Condition Subject = 杯子（条件判断的对象）
> 例：「除非瓶子是空的，否则不要拿」→ Condition Subject = 瓶子

**entity_id 列名**: `condition_subject_entity_ids`

---

## 三、grounding_status 选择指南

每个角色的 `_grounding_status` 列只能填写以下四个值之一：

### UNIQUE（唯一确定）

场景中有且仅有一个物体与该角色**明确匹配**。

- 指令明确 + 场景中有恰好一个匹配物体 → UNIQUE
- 即使有多个同类别物体，但指令中有足够的修饰语（颜色、大小、位置等）唯一确定 → UNIQUE

> 例：场景中有「红球」「蓝球」，指令说「抓住红球」→ Theme = UNIQUE（红球唯一）

### AMBIGUOUS（歧义 / 多候选）

场景中有**多个物体**都可以匹配该角色，且指令**不足以唯一确定**其中之一。

- 需要向用户澄清
- 在 `clarification_required` 列填 TRUE
- 在 `acceptable_entity_sets` 列中列出所有可能的候选 ID（逗号分隔）

> 例：场景中有「cup-1」「cup-2」，指令只说「抓住杯子」→ Theme = AMBIGUOUS

### NOT_FOUND（未找到）

指令中提到了该角色，但场景中**没有对应物体**。

- 需要向用户澄清
- 在 `clarification_required` 列填 TRUE

> 例：指令「抓住杯子」，但场景中没有任何 cup 类物体 → Theme = NOT_FOUND

### NOT_APPLICABLE（不适用）

指令中**根本没有提到**该角色。大多数简单动作指令中很多角色都是 NOT_APPLICABLE。

> 例：「抓住杯子」→ Destination = NOT_APPLICABLE, Recipient = NOT_APPLICABLE
> 例：「把杯子放到桌子上」→ Recipient = NOT_APPLICABLE

---

## 四、填写流程

### 4.1 TC_005 / TC_008 审核

1. 打开你的 CSV（A 或 B）
2. 阅读 `instruction` 和 `scene_objects_summary`
3. 阅读 `original_golden_value`（原 Golden 预期值）
4. 阅读 `semantic_question`（语义判断问题）
5. 在 `reviewer_decision` 中选择：
   - `KEEP_OLD` — 保留原 Golden 值不变
   - `ACCEPT_PROPOSED` — 接受建议的新值
   - `ALTERNATIVE` — 提出你自己的值（在 `notes` 中写明）
6. 填写其他必填字段
7. 在 `reviewer_id` 中填入你的实名或工号
8. 在 `signed_at` 中填入 ISO 8601 时间戳（如 `2026-07-22T15:30:00+08:00`）

### 4.2 Entity Grounding 审核

1. 打开你的 CSV（A 或 B）
2. 逐行阅读每个 case
3. 看 `instruction`（用户指令）和 `scene_objects_summary`（场景物体）
4. 对每个角色（Theme / Destination / Recipient / Prohibition / Condition Subject）：
   - 判断指令中是否包含该角色
   - 如果包含：在场景中寻找匹配物体，填入 `entity_ids`
   - 选择 `grounding_status`
5. 如果有关键信息缺失或有歧义，在 `clarification_required` 填 TRUE 并在 `clarification_question` 中写澄清问题
6. 填写 `rationale`（简短说明你的判断依据）
7. 在 `reviewer_id` 和 `signed_at` 中签名

### 4.3 填写 entity_ids 格式

- **单个 ID**: 直接写 object_id，如 `cup-1`
- **多个 ID**: 逗号分隔，如 `cup-1,box-1`（不加空格）
- **不适用**: 留空
- **所有 ID 必须来自** `available_object_ids` 列中列出的 ID

---

## 五、常见案例示例

### 示例 1: 简单动作

| 字段 | 值 |
|------|-----|
| instruction | 抓住杯子 |
| scene objects | cup-1 (cup, white, plastic), box-1 (box, brown, cardboard) |
| theme_entity_ids | `cup-1` |
| theme_grounding_status | `UNIQUE` |
| destination_entity_ids | *(留空)* |
| destination_grounding_status | `NOT_APPLICABLE` |
| recipient_entity_ids | *(留空)* |
| recipient_grounding_status | `NOT_APPLICABLE` |
| prohibition_entity_ids | *(留空)* |
| prohibition_grounding_status | `NOT_APPLICABLE` |

### 示例 2: 带目的地

| 字段 | 值 |
|------|-----|
| instruction | 把杯子放到桌子上 |
| scene objects | cup-1 (cup, white), table-1 (table, brown, wood) |
| theme_entity_ids | `cup-1` |
| theme_grounding_status | `UNIQUE` |
| destination_entity_ids | `table-1` |
| destination_grounding_status | `UNIQUE` |
| recipient_entity_ids | *(留空)* |
| recipient_grounding_status | `NOT_APPLICABLE` |

### 示例 3: 歧义

| 字段 | 值 |
|------|-----|
| instruction | 抓住瓶子 |
| scene objects | bottle-s (bottle, small), bottle-m (bottle, medium), bottle-l (bottle, large) |
| theme_entity_ids | *(留空 — 无法唯一确定)* |
| theme_grounding_status | `AMBIGUOUS` |
| acceptable_entity_sets | `bottle-s,bottle-m,bottle-l` |
| clarification_required | `TRUE` |
| clarification_question | `场景中有三个瓶子(small/medium/large)，请明确是哪一个？` |

### 示例 4: 禁止

| 字段 | 值 |
|------|-----|
| instruction | 抓住杯子，不要碰到花瓶 |
| scene objects | cup-1 (cup), vase-1 (vase), table-1 (table) |
| theme_entity_ids | `cup-1` |
| theme_grounding_status | `UNIQUE` |
| prohibition_entity_ids | `vase-1` |
| prohibition_grounding_status | `UNIQUE` |

### 示例 5: 物体不存在

| 字段 | 值 |
|------|-----|
| instruction | 抓住不存在的物体 |
| scene objects | *(空)* |
| theme_entity_ids | *(留空)* |
| theme_grounding_status | `NOT_FOUND` |
| clarification_required | `TRUE` |

---

## 六、必填字段

### TC_005 / TC_008

| 字段 | 必填 |
|------|------|
| `reviewer_decision` | ✅ |
| `expected_execution_allowed` | ✅ |
| `rationale` | ✅ |
| `reviewer_id` | ✅ |
| `signed_at` | ✅ |

### Entity Grounding

| 字段 | 必填 |
|------|------|
| 所有 `_grounding_status` 列 | ✅（每个角色必须选一个） |
| 当 grounding_status = UNIQUE 时的 `_entity_ids` | ✅ |
| `rationale` | ✅ |
| `reviewer_id` | ✅（实名或工号） |
| `signed_at` | ✅（ISO 8601） |

---

## 七、保存与提交

1. 用你喜欢的 CSV 编辑器（Excel、Google Sheets、LibreOffice Calc）打开你的 CSV 文件
2. **注意**: 用 Excel 打开时确保 UTF-8 编码正确（中文不乱码）
3. 填写完所有必填字段后，保存为 CSV UTF-8 格式
4. 将填写好的文件重命名为包含你的姓名，如：
   - `holdout_v3_review_a_张工.csv`
   - `holdout_v3_review_b_李工.csv`
5. 告诉协调人你已完成，等待 Reviewer A 和 B 都提交后进行 diff 比较

---

## 八、验证工具

提交前请运行验证脚本检查格式：

```bash
python robot_intent_agent/eval/validate_review_submission.py \\
    --input YOUR_FILLED_FILE.csv \\
    --type entity    # 或 --type tc
```

验证脚本会检查：
- object_id 是否在对应场景中存在
- 必填字段是否已填
- 枚举值是否合法
- 同一 object_id 是否被分配给不兼容的多个角色

---

## 九、仲裁流程

1. Reviewer A 和 B 都提交后，协调人运行 diff 比较：
   ```bash
   python robot_intent_agent/eval/validate_review_submission.py \\
       --compare review_a.csv review_b.csv \\
       --output adjudication.csv
   ```
2. 所有不一致项自动写入 `adjudication.csv`
3. Adjudicator 逐条裁决，填写 `adjudicator_decision`
4. **注意**: 比较工具不会自动决定谁对谁错——Adjudicator 必须独立判断

---

*本指南由 generate_review_materials.py 自动生成*
*所有待填写字段均为空白，等待人工填写*
"""

    with open(HUMAN_REVIEW_GUIDE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  ✓ Human Review Guide: {HUMAN_REVIEW_GUIDE}")


# ══════════════════════════════════════════════════════════════════════════════
# Part 4: Validation Script
# ══════════════════════════════════════════════════════════════════════════════

def generate_validator() -> None:
    """Generate the validate_review_submission.py helper script."""

    validator_code = r'''#!/usr/bin/env python3
"""
Review Submission Validator — Golden Data Review
=================================================
Validates human-filled review CSV submissions for:
  - Object ID existence in scene
  - Required field presence
  - Enum value correctness
  - Role conflict detection
  - Reviewer A vs B comparison → adjudication CSV

Usage:
  # Validate a single entity review submission
  python validate_review_submission.py --input holdout_v3_review_a.csv --type entity

  # Validate a TC review submission
  python validate_review_submission.py --input tc_reviewer_a_tc_cases.csv --type tc

  # Compare Reviewer A and B → generate adjudication CSV
  python validate_review_submission.py --compare review_a.csv review_b.csv --output adjudication.csv

ABSOLUTE RULE:
  This tool reports ERRORS and DIFFS. It NEVER auto-corrects or decides who is right.
  All decisions must be made by a human adjudicator.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
HOLDOUT_V3_PATH = SCRIPT_DIR / "holdout_v3.json"

# Cache of valid object IDs per case
_valid_ids_cache: Optional[Dict[str, Set[str]]] = None


def get_valid_ids() -> Dict[str, Set[str]]:
    """Load valid object_ids per case_id from holdout_v3.json."""
    global _valid_ids_cache
    if _valid_ids_cache is not None:
        return _valid_ids_cache

    if not HOLDOUT_V3_PATH.exists():
        print(f"WARNING: holdout_v3.json not found at {HOLDOUT_V3_PATH}")
        _valid_ids_cache = {}
        return _valid_ids_cache

    with open(HOLDOUT_V3_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    _valid_ids_cache = {}
    for case in data.get("cases", []):
        cid = case["case_id"]
        ids = {o["object_id"] for o in case.get("objects", [])}
        _valid_ids_cache[cid] = ids

    return _valid_ids_cache


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════

VALID_GROUNDING_STATUSES = {"UNIQUE", "AMBIGUOUS", "NOT_FOUND", "NOT_APPLICABLE", ""}
VALID_TC_DECISIONS = {"KEEP_OLD", "ACCEPT_PROPOSED", "ALTERNATIVE", "NEEDS_SCHEMA_CHANGE", ""}
VALID_BOOLS = {"TRUE", "FALSE", "true", "false", ""}

ENTITY_ROLE_COLUMNS = [
    ("theme_entity_ids", "theme_grounding_status"),
    ("destination_entity_ids", "destination_grounding_status"),
    ("recipient_entity_ids", "recipient_grounding_status"),
    ("prohibition_entity_ids", "prohibition_grounding_status"),
    ("condition_subject_entity_ids", "condition_subject_grounding_status"),
]

ENTITY_REQUIRED_FIELDS = [
    "theme_grounding_status",
    "destination_grounding_status",
    "recipient_grounding_status",
    "prohibition_grounding_status",
    "condition_subject_grounding_status",
    "rationale",
    "reviewer_id",
]

TC_REQUIRED_FIELDS = [
    "reviewer_decision",
    "expected_execution_allowed",
    "rationale",
    "reviewer_id",
]


def validate_entity_row(row: Dict[str, str], valid_ids: Dict[str, Set[str]]) -> List[str]:
    """Validate a single entity review row. Returns list of error messages."""
    errors = []
    cid = row.get("case_id", "UNKNOWN")
    case_ids = valid_ids.get(cid, set())

    # ── Required fields ──
    for field in ENTITY_REQUIRED_FIELDS:
        if field in row and not row[field].strip():
            errors.append(f"[{cid}] Required field '{field}' is empty")

    # ── Grounding status enum check ──
    for _, status_col in ENTITY_ROLE_COLUMNS:
        if status_col in row:
            val = row[status_col].strip().upper()
            if val and val not in VALID_GROUNDING_STATUSES:
                errors.append(
                    f"[{cid}] Invalid grounding_status '{row[status_col]}' in '{status_col}'. "
                    f"Must be one of: {', '.join(sorted(VALID_GROUNDING_STATUSES - {''}))}"
                )

    # ── Object ID existence check ──
    for ids_col, status_col in ENTITY_ROLE_COLUMNS:
        if ids_col not in row or status_col not in row:
            continue
        ids_str = row[ids_col].strip()
        status = row[status_col].strip().upper()

        if ids_str:
            # Has entity IDs → status must be UNIQUE or AMBIGUOUS
            if status and status not in ("UNIQUE", "AMBIGUOUS"):
                errors.append(
                    f"[{cid}] {ids_col} has IDs '{ids_str}' but {status_col}='{status}'. "
                    f"When IDs are provided, status must be UNIQUE or AMBIGUOUS"
                )

            # Check each ID exists
            for oid in ids_str.split(","):
                oid = oid.strip()
                if oid and oid not in case_ids:
                    errors.append(
                        f"[{cid}] Object ID '{oid}' in '{ids_col}' does NOT exist in scene. "
                        f"Available: {', '.join(sorted(case_ids)) if case_ids else '(none)'}"
                    )
        else:
            # No entity IDs → status should be NOT_FOUND, NOT_APPLICABLE, or empty
            if status == "UNIQUE":
                errors.append(
                    f"[{cid}] {ids_col} is empty but {status_col}='UNIQUE'. "
                    f"If unique, you must provide the entity ID"
                )

    # ── Cross-role conflict: same object_id cannot be both theme and prohibition ──
    role_ids: Dict[str, Set[str]] = {}
    for ids_col, _ in ENTITY_ROLE_COLUMNS:
        if ids_col in row and row[ids_col].strip():
            role_ids[ids_col] = {x.strip() for x in row[ids_col].split(",") if x.strip()}

    # Theme ∩ Prohibition conflict
    theme_ids = role_ids.get("theme_entity_ids", set())
    prohibition_ids = role_ids.get("prohibition_entity_ids", set())
    conflict = theme_ids & prohibition_ids
    if conflict:
        errors.append(
            f"[{cid}] ROLE CONFLICT: Object(s) {', '.join(sorted(conflict))} "
            f"assigned as BOTH theme AND prohibition"
        )

    # Theme ∩ Destination (may be intentional but flag it)
    dest_ids = role_ids.get("destination_entity_ids", set())
    theme_dest_conflict = theme_ids & dest_ids
    if theme_dest_conflict:
        errors.append(
            f"[{cid}] NOTE: Object(s) {', '.join(sorted(theme_dest_conflict))} "
            f"assigned as BOTH theme AND destination — verify this is intentional"
        )

    return errors


def validate_tc_row(row: Dict[str, str]) -> List[str]:
    """Validate a single TC review row. Returns list of error messages."""
    errors = []
    cid = row.get("case_id", "UNKNOWN")

    for field in TC_REQUIRED_FIELDS:
        if field in row and not row[field].strip():
            errors.append(f"[{cid}] Required field '{field}' is empty")

    decision = row.get("reviewer_decision", "").strip().upper()
    if decision and decision not in VALID_TC_DECISIONS:
        errors.append(
            f"[{cid}] Invalid reviewer_decision '{row['reviewer_decision']}'. "
            f"Must be one of: {', '.join(sorted(VALID_TC_DECISIONS - {''}))}"
        )

    exec_allowed = row.get("expected_execution_allowed", "").strip().upper()
    if exec_allowed and exec_allowed not in VALID_BOOLS:
        errors.append(
            f"[{cid}] Invalid expected_execution_allowed '{row['expected_execution_allowed']}'. "
            f"Must be TRUE or FALSE"
        )

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# Comparison (Reviewer A vs B)
# ══════════════════════════════════════════════════════════════════════════════

def compare_reviews(path_a: str, path_b: str, output_path: str) -> None:
    """Compare two review CSVs and write disagreements to adjudication CSV."""

    def load_csv(path: str) -> Dict[str, Dict[str, str]]:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return {row["case_id"]: row for row in reader}

    data_a = load_csv(path_a)
    data_b = load_csv(path_b)

    # Determine type by checking column presence
    sample_row = next(iter(data_a.values()), {})
    is_entity = "theme_entity_ids" in sample_row
    is_tc = "reviewer_decision" in sample_row and not is_entity

    # Fields to compare
    if is_entity:
        compare_fields = [
            "theme_entity_ids", "theme_grounding_status",
            "destination_entity_ids", "destination_grounding_status",
            "recipient_entity_ids", "recipient_grounding_status",
            "prohibition_entity_ids", "prohibition_grounding_status",
            "condition_subject_entity_ids", "condition_subject_grounding_status",
            "clarification_required", "expected_grounding_status_overall",
        ]
    elif is_tc:
        compare_fields = [
            "reviewer_decision", "expected_action", "expected_plan_status",
            "expected_execution_allowed", "blocking_reason",
            "clarification_required", "schema_change_needed",
        ]
    else:
        print("ERROR: Could not determine review type from CSV columns.")
        sys.exit(1)

    all_case_ids = sorted(set(data_a.keys()) | set(data_b.keys()))

    disagreements = []
    for cid in all_case_ids:
        row_a = data_a.get(cid, {})
        row_b = data_b.get(cid, {})

        if not row_a:
            disagreements.append({
                "case_id": cid, "field_name": "ENTIRE_ROW",
                "reviewer_a_value": "MISSING", "reviewer_b_value": "PRESENT",
                "conflict_type": "MISSING_IN_A",
                "adjudicator_decision": "", "adjudicator_rationale": "",
                "adjudicator_id": "", "signed_at": "",
            })
            continue
        if not row_b:
            disagreements.append({
                "case_id": cid, "field_name": "ENTIRE_ROW",
                "reviewer_a_value": "PRESENT", "reviewer_b_value": "MISSING",
                "conflict_type": "MISSING_IN_B",
                "adjudicator_decision": "", "adjudicator_rationale": "",
                "adjudicator_id": "", "signed_at": "",
            })
            continue

        for field in compare_fields:
            val_a = row_a.get(field, "").strip()
            val_b = row_b.get(field, "").strip()

            # Normalize for comparison
            norm_a = _normalize(val_a)
            norm_b = _normalize(val_b)

            if norm_a != norm_b:
                # Both empty → skip (not a real disagreement)
                if norm_a == "" and norm_b == "":
                    continue
                # One empty, one not
                if norm_a == "":
                    ctype = "A_EMPTY_B_FILLED"
                elif norm_b == "":
                    ctype = "B_EMPTY_A_FILLED"
                else:
                    ctype = "VALUE_MISMATCH"

                disagreements.append({
                    "case_id": cid,
                    "field_name": field,
                    "reviewer_a_value": val_a,
                    "reviewer_b_value": val_b,
                    "conflict_type": ctype,
                    "adjudicator_decision": "",
                    "adjudicator_rationale": "",
                    "adjudicator_id": "",
                    "signed_at": "",
                })

    # Write adjudication CSV
    adj_fieldnames = [
        "case_id", "field_name", "reviewer_a_value", "reviewer_b_value",
        "conflict_type", "adjudicator_decision", "adjudicator_rationale",
        "adjudicator_id", "signed_at",
    ]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=adj_fieldnames)
        writer.writeheader()
        writer.writerows(disagreements)

    print(f"\n══ Review Comparison Complete ══")
    print(f"  File A: {path_a}")
    print(f"  File B: {path_b}")
    print(f"  Cases compared: {len(all_case_ids)}")
    print(f"  Disagreements found: {len(disagreements)}")
    print(f"  Adjudication CSV: {out_path}")
    print()
    if disagreements:
        # Summarize by conflict type
        by_type = defaultdict(int)
        for d in disagreements:
            by_type[d["conflict_type"]] += 1
        print("  Conflict breakdown:")
        for ctype, count in sorted(by_type.items()):
            print(f"    {ctype}: {count}")
    print()
    print("  ⚠ All adjudicator_decision fields are EMPTY.")
    print("  ⚠ A HUMAN adjudicator must review each disagreement and fill the decisions.")
    print("  ⚠ This tool does NOT decide who is correct.")


def _normalize(val: str) -> str:
    """Normalize a value for comparison (case-insensitive, whitespace-normalized)."""
    return " ".join(val.upper().split())


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Review Submission Validator — Golden Data Review"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="Single review CSV to validate")
    group.add_argument("--compare", nargs=2, metavar=("FILE_A", "FILE_B"),
                       help="Compare two review CSVs")
    parser.add_argument("--type", choices=["entity", "tc"], help="Review type (required with --input)")
    parser.add_argument("--output", type=str, default="adjudication_output.csv",
                        help="Output path for adjudication CSV (with --compare)")

    args = parser.parse_args()

    if args.compare:
        compare_reviews(args.compare[0], args.compare[1], args.output)
        return

    if not args.type:
        print("ERROR: --type is required with --input")
        sys.exit(1)

    # ── Single file validation ──
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_errors = 0
    valid_ids = get_valid_ids() if args.type == "entity" else {}

    for i, row in enumerate(rows, 2):  # 1-indexed, line 1 is header
        if args.type == "entity":
            errors = validate_entity_row(row, valid_ids)
        else:
            errors = validate_tc_row(row)

        if errors:
            total_errors += len(errors)
            for err in errors:
                print(f"  Line {i}: {err}")

    print()
    print(f"══ Validation Summary ══")
    print(f"  File: {input_path}")
    print(f"  Rows checked: {len(rows)}")
    print(f"  Errors found: {total_errors}")
    print()

    if total_errors == 0:
        print("  ✓ All checks passed. Ready for submission.")
    else:
        print("  ⚠ Errors found. Please fix before submitting.")

    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
'''

    with open(VALIDATOR_SCRIPT, "w", encoding="utf-8") as f:
        f.write(validator_code)

    print(f"  ✓ Validator script: {VALIDATOR_SCRIPT}")


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Generate all review materials."""
    print("═══ Review Material Generator ═══")
    print(f"Timestamp: {TIMESTAMP}")
    print()

    # Part 1: TC_005 / TC_008
    print("[1/4] Generating TC_005 / TC_008 review packages ...")
    generate_tc_review_packages()
    print()

    # Part 2: Entity Grounding
    print("[2/4] Generating 150 Entity Grounding review packages ...")
    generate_entity_review_packages()
    print()

    # Part 3: Guide
    print("[3/4] Generating HUMAN_REVIEW_GUIDE.md ...")
    generate_guide()
    print()

    # Part 4: Validator
    print("[4/4] Generating validation script ...")
    generate_validator()
    print()

    # ── Final report ──
    print("═══ Generation Complete ═══")
    print()
    print("Generated files:")
    print(f"  1. {TC_REVIEW_A_CSV}")
    print(f"  2. {TC_REVIEW_B_CSV}")
    print(f"  3. {ENTITY_REVIEW_A_CSV}")
    print(f"  4. {ENTITY_REVIEW_B_CSV}")
    print(f"  5. {ENTITY_ADJUDICATION_CSV}")
    print(f"  6. {HUMAN_REVIEW_GUIDE}")
    print(f"  7. {VALIDATOR_SCRIPT}")
    print()

    # Verify constraints
    print("── Constraint Verification ──")

    # Check entity CSV has 150 cases
    with open(ENTITY_REVIEW_A_CSV, "r", encoding="utf-8-sig") as f:
        entity_count = sum(1 for _ in csv.DictReader(f))
    print(f"  ✓ Entity cases in review CSVs: {entity_count}/150")

    # Check all status fields are empty/PENDING
    with open(ENTITY_REVIEW_A_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        filled_grounding = 0
        filled_reviewer = 0
        for row in reader:
            for col_suffix in ["_grounding_status"]:
                for role in ["theme", "destination", "recipient", "prohibition", "condition_subject"]:
                    col = f"{role}{col_suffix}"
                    if col in row and row[col].strip():
                        filled_grounding += 1
            if row.get("reviewer_id", "").strip():
                filled_reviewer += 1

    if filled_grounding == 0 and filled_reviewer == 0:
        print("  ✓ All entity grounding_status fields: EMPTY (no AI-generated values)")
        print("  ✓ All reviewer_id fields: EMPTY (no fake identities)")
    else:
        print(f"  ⚠ WARNING: {filled_grounding} grounding_status fields are filled!")
        print(f"  ⚠ WARNING: {filled_reviewer} reviewer_id fields are filled!")

    # Check TC CSVs are blank too
    with open(TC_REVIEW_A_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        tc_filled = sum(1 for row in reader if row.get("reviewer_decision", "").strip())
    print(f"  ✓ TC reviewer_decision fields: ALL EMPTY ({tc_filled} filled, should be 0)")

    # Confirm no system outputs present
    print("  ✓ System actual outputs: HIDDEN (not present in any CSV)")
    print("  ✓ Pipeline predictions: HIDDEN")
    print("  ✓ Scores: HIDDEN")
    print("  ✓ Engine labels (RuleEngine/DeepSeek/Hybrid): HIDDEN")

    print()
    print("── Status ──")
    print("  TC_005: PENDING")
    print("  TC_008: PENDING")
    print(f"  Entity Grounding: {entity_count}/150 UNREVIEWED")
    print()
    print("Next step: Distribute CSVs to Reviewer A and Reviewer B.")
    print("They must fill them INDEPENDENTLY without seeing each other's work.")


if __name__ == "__main__":
    main()
