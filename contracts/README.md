# 联调接口契约

所有模块只依赖这里定义的协议，不直接依赖其他模块的内部类或目录。协议当前版本为 `v1`。

## 数据流

`perception.json` → `task.json` → `strategy.json` → `execution.json` → `feedback.json`

## 文件约定

- `v1/perception.schema.json`：感知模块输出，包含物体 `id`、类别、位姿、尺寸和可选执行能力信息。
- `v1/task.schema.json`：意图理解模块输出，包含动作、目标 ID、目的地、约束和阻断状态。
- `v1/strategy.schema.json`：策略模块输出，包含动作序列、参数和 `task_main()` 入口约定。
- `v1/execution.schema.json`：仿真/真机输出，包含每步结果、轨迹、耗时和安全事件。
- `v1/feedback.schema.json`：TraceCoder 使用的失败诊断和改进建议。

提交接口变更时必须增加版本号或提供向后兼容字段，并同步更新示例和契约测试。
