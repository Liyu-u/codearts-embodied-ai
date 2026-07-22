# HUMAN REVIEW GUIDE — 人工审核操作说明

**生成时间**: 2026-07-22T10:22:09.690057+00:00
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
python robot_intent_agent/eval/validate_review_submission.py \
    --input YOUR_FILLED_FILE.csv \
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
   python robot_intent_agent/eval/validate_review_submission.py \
       --compare review_a.csv review_b.csv \
       --output adjudication.csv
   ```
2. 所有不一致项自动写入 `adjudication.csv`
3. Adjudicator 逐条裁决，填写 `adjudicator_decision`
4. **注意**: 比较工具不会自动决定谁对谁错——Adjudicator 必须独立判断

---

*本指南由 generate_review_materials.py 自动生成*
*所有待填写字段均为空白，等待人工填写*
