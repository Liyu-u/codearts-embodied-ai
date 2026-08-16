"""
robot_intent_agent 主入口

用法:
    python main.py                          # 交互模式
    python main.py --instruction "..."       # 单次推理
    python main.py --api                    # FastAPI 服务模式
"""

import argparse
import sys
from pathlib import Path

from robot_intent_agent import __version__
from robot_intent_agent.config.settings import get_settings


def main():
    parser = argparse.ArgumentParser(
        description="Robot Intent Agent — NL → Robot Task IR"
    )
    parser.add_argument(
        "--instruction", "-i",
        type=str,
        help="Natural language instruction",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Start as FastAPI service",
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version",
    )
    args = parser.parse_args()

    if args.version:
        print(f"robot_intent_agent v{__version__}")
        return

    settings = get_settings()
    print(f"Robot Intent Agent v{__version__}")
    print(f"Config: {settings.model_dump()}")
    print("=" * 60)

    if args.api:
        print("[TODO] FastAPI service mode — coming in future sprint")
        return

    if args.instruction:
        print(f"Instruction: {args.instruction}")
        print("[TODO] Pipeline execution — coming in subsequent steps")
        return

    print("Interactive mode — enter instructions (Ctrl+C to exit):")
    try:
        while True:
            instruction = input("\n> ").strip()
            if not instruction:
                continue
            print(f"[TODO] Processing: {instruction}")
    except KeyboardInterrupt:
        print("\nExiting.")


if __name__ == "__main__":
    main()
