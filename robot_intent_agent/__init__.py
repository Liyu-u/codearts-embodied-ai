"""
robot_intent_agent — 机器人意图理解 Agent 模块

架构管道:
    NL Input
        → Knowledge & Skill Memory
        → Semantic Scene Builder
        → Semantic Task Planner
        → Hybrid Constraint Compiler
        → Robot Task IR Generator
        → 标准 Robot Task IR JSON

下游模块:
    - CodeArts Adapter (策略代码生成)
    - TraceCoder Adapter (异常自愈闭环)
"""

__version__ = "0.1.0"
