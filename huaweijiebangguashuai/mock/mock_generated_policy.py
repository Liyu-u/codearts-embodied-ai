"""
模拟同学 B (CodeArts) 生成的静态 Python 策略脚本
用于独立调试 — 无需实际调用 LLM API
"""


def task_pick_red_block():
    """模拟任务：抓取红色方块并放置到目标位置"""
    print("[MOCK] 开始执行: 抓取红色方块")

    # 1. 感知场景
    objects = [
        {"name": "红色方块", "x": 0.15, "y": 0.05, "z": 0.03},
        {"name": "蓝色杯子", "x": -0.10, "y": -0.08, "z": 0.06},
    ]

    target = next(o for o in objects if o["name"] == "红色方块")

    # 2. 移动到安全高度
    safe_z = 0.15
    print(f"[MOCK] 移动至安全高度 z={safe_z}")

    # 3. 下降抓取
    print(f"[MOCK] 下降至 z={target['z'] + 0.01}")
    print(f"[MOCK] 闭合夹爪，力=5.0N")

    # 4. 抬升
    print(f"[MOCK] 抬升至安全高度 z={safe_z}")

    # 5. 放置
    dest = {"x": -0.10, "y": 0.02, "z": 0.03}
    print(f"[MOCK] 移动至目标位置 x={dest['x']}, y={dest['y']}, z={dest['z']}")
    print("[MOCK] 张开夹爪")

    return {"status": "success", "task_id": "mock-task-001"}


def task_sort_by_color():
    """模拟任务：按颜色分堆"""
    print("[MOCK] 开始执行: 按颜色分类")
    piles = {"red": [], "blue": [], "green": []}

    for color in piles:
        print(f"[MOCK] 移动 {color} 方块到对应堆")

    print("[MOCK] 分类完成")
    return {"status": "success", "task_id": "mock-task-002"}


# 危险代码样例 (用于测试 code_validator)
DANGEROUS_CODE_EXAMPLE = '''
import os
os.system("rm -rf /")
eval("print('unsafe')")
'''
