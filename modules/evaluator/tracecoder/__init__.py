"""TraceCoder —— 机器人策略的『验证器 → 优化器』迭代修复模块。

本包是统一联调仓库中 modules/evaluator（D 模块：TraceCoder、TraceProbe、评测）
的核心能力：输入一份可执行策略，在轻量确定性仿真中执行并评分，对失败做
观察→分析→修改 的三角色归因修复，并通过 HLLM 经验库记住历史教训。

闭环定位（见仓库 README）：
    策略生成(strategy.v1) → Isaac Sim / 真机执行(execution.v1)
        → TraceCoder 反馈(feedback.v1) → 回归测试与策略修正

设计要点
--------
1. **轻量仿真代理**：simulator.py 是确定性状态机（无真实动力学），
   只在闭环的 Mock 阶段替代 Isaac Sim。未来接入 Isaac Sim 后，
   本模块的输入会由『execution.v1 日志』承担（见 integration/adapters/tracecoder.py），
   评分与修复逻辑保持不变——这正是把 execution 作为唯一输入的接口设计的动机。
2. **离线优先**：三角色（Observation/Analysis/Repair）默认纯规则运行，
   零 API 成本、结果确定可复现；--use-llm 时才调用 LLM，失败自动回退。
3. **三维质量分**：安全 > 平滑 > 效率（0.5/0.3/0.2），安全违规不可被任务完成抵消。
4. **HLLM 经验库**：失败签名 → 已知成功修复组合，命中直接复用、仍做仿真验证。

对外接口
--------
- `process_policy(task_data, initial_strategy, ...)`：完整 初始分→修复→最终分 闭环。
- 适配器入口见 integration/adapters/tracecoder.py 的 run()/health()。

上游来源：https://github.com/Liyu-u/Codearts-Tracecoder 的 src/robot_policy。
本目录为拷贝接入（不随上游同步），改动请保持与上游一致并注释说明。
"""

from .processor import process_policy

__all__ = ["process_policy"]
