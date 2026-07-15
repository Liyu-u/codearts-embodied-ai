"""
Intent-Understanding-Natural-Language-Context-Awareness
========================================================
主入口 — 自然语言 → 场景感知 → 意图理解 → 任务规划 全链路

架构:
    NL Input → Scene Builder → Task Planner → Constraint Compiler
                → IR Generator → Translator → CodeArts/TraceCoder Adapter
"""

__version__ = "0.1.0"


def main():
    print("=" * 60)
    print("  Intent Understanding — NL + Context Awareness")
    print(f"  v{__version__}")
    print("=" * 60)


if __name__ == "__main__":
    main()
