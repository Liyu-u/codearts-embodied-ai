"""
标准测试场景构建器 — Isaac Sim 6.0.1
同学 C（吴昌庆）

三个标准场景用于：
- 队友 A: 意图解析消歧测试
- 队友 B: CodeArts 策略代码执行验证
- 队友 D: 监控探针异常检测
- 评测: 全链路 MVP 贯通

用法:
  Kit 模式:  isaacsim.exe --exec scene_builder.py -- --scene stacking_cubes
  Mock 模式:  python scene_builder.py   (打印场景定义, 不生成 .usd)
"""

import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# Isaac Sim 6.0.1 API 导入
# ============================================================
_KIT_MODE = False
try:
    from isaacsim.core.utils.stage import get_current_stage
    from isaacsim.core.utils.prims import create_prim
    from isaacsim.storage.native import get_assets_root_path
    from pxr import UsdGeom, Sdf, Gf
    _KIT_MODE = True
except ImportError:
    pass


# ============================================================
# 场景定义（纯数据结构，双模式通用）
# ============================================================

class SceneDef:
    """场景的完整定义"""
    def __init__(self, name: str, description: str, objects: List[Dict], table: Dict = None):
        self.name = name
        self.description = description
        self.table = table or {"pos": (0.3, 0.0, 0.0), "scale": (0.6, 1.0, 0.02)}
        self.objects = objects


# ============================================================
# 场景 1: 方块堆叠 (stacking_cubes)
# ============================================================
SCENE_STACKING_CUBES = SceneDef(
    name="stacking_cubes",
    description="三个彩色方块堆叠在一起，用于测试 pick_and_place + stack 动作",
    table={"pos": (0.3, 0.0, 0.0), "scale": (0.8, 1.0, 0.02)},
    objects=[
        {
            "name": "红色方块",
            "prim_type": "Cube",
            "position": (0.25, 0.0, 0.04),
            "scale": (0.04, 0.04, 0.04),
            "color": (1.0, 0.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "蓝色方块",
            "prim_type": "Cube",
            "position": (0.25, 0.0, 0.08),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 0.0, 1.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "绿色方块",
            "prim_type": "Cube",
            "position": (0.25, 0.0, 0.12),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 1.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
    ],
)

# ============================================================
# 场景 2: 杯子排列 (cup_lineup)
# ============================================================
SCENE_CUP_LINEUP = SceneDef(
    name="cup_lineup",
    description="5 个不同颜色的杯子散放在桌面上，用于测试 find_object + pick_and_place",
    table={"pos": (0.3, 0.0, 0.0), "scale": (0.8, 1.0, 0.02)},
    objects=[
        {
            "name": "红色杯子",
            "prim_type": "Cylinder",
            "position": (0.15, 0.10, 0.06),
            "scale": (0.025, 0.05, 0.025),
            "color": (1.0, 0.0, 0.0),
            "label": "cup",
            "mass_kg": 0.20,
            "material": "glass",
        },
        {
            "name": "蓝色杯子",
            "prim_type": "Cylinder",
            "position": (0.30, -0.08, 0.06),
            "scale": (0.025, 0.05, 0.025),
            "color": (0.0, 0.0, 1.0),
            "label": "cup",
            "mass_kg": 0.20,
            "material": "glass",
        },
        {
            "name": "绿色杯子",
            "prim_type": "Cylinder",
            "position": (0.45, 0.05, 0.06),
            "scale": (0.025, 0.05, 0.025),
            "color": (0.0, 1.0, 0.0),
            "label": "cup",
            "mass_kg": 0.20,
            "material": "glass",
        },
        {
            "name": "黄色杯子",
            "prim_type": "Cylinder",
            "position": (0.20, -0.15, 0.06),
            "scale": (0.025, 0.05, 0.025),
            "color": (1.0, 1.0, 0.0),
            "label": "cup",
            "mass_kg": 0.20,
            "material": "glass",
        },
        {
            "name": "白色杯子",
            "prim_type": "Cylinder",
            "position": (0.40, 0.18, 0.06),
            "scale": (0.025, 0.05, 0.025),
            "color": (0.9, 0.9, 0.9),
            "label": "cup",
            "mass_kg": 0.20,
            "material": "glass",
        },
    ],
)

# ============================================================
# 场景 3: 颜色分类 (color_sorting)
# ============================================================
SCENE_COLOR_SORTING = SceneDef(
    name="color_sorting",
    description="红/蓝/绿方块混放桌面，用于测试 sort_by_color + push 动作",
    table={"pos": (0.3, 0.0, 0.0), "scale": (0.8, 1.0, 0.02)},
    objects=[
        {
            "name": "红色方块A",
            "prim_type": "Cube",
            "position": (0.10, 0.05, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (1.0, 0.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "蓝色方块A",
            "prim_type": "Cube",
            "position": (0.25, -0.10, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 0.0, 1.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "绿色方块A",
            "prim_type": "Cube",
            "position": (0.40, 0.08, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 1.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "红色方块B",
            "prim_type": "Cube",
            "position": (0.15, -0.15, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (1.0, 0.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "蓝色方块B",
            "prim_type": "Cube",
            "position": (0.50, -0.05, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 0.0, 1.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
        {
            "name": "绿色方块B",
            "prim_type": "Cube",
            "position": (0.35, 0.15, 0.03),
            "scale": (0.04, 0.04, 0.04),
            "color": (0.0, 1.0, 0.0),
            "label": "cube",
            "mass_kg": 0.15,
            "material": "plastic",
        },
    ],
)

# 场景注册表
ALL_SCENES: Dict[str, SceneDef] = {
    "stacking_cubes": SCENE_STACKING_CUBES,
    "cup_lineup": SCENE_CUP_LINEUP,
    "color_sorting": SCENE_COLOR_SORTING,
}

# 分类区坐标（供 sort_by_color 策略使用）
DROP_ZONES = {
    "stacking_cubes": {},
    "cup_lineup": {},
    "color_sorting": {
        "red":   (0.10, -0.20, 0.03),
        "blue":  (0.30, -0.20, 0.03),
        "green": (0.50, -0.20, 0.03),
    },
}


# ============================================================
# Kit 模式：构建真实 USD 场景
# ============================================================
def _build_scene_in_kit(scene_def: SceneDef):
    """在 Isaac Sim Kit 运行时内创建场景 Prim"""
    stage = get_current_stage()

    # 1. 创建桌面
    table_path = "/World/Table"
    create_prim(
        prim_path=table_path,
        prim_type="Cube",
        position=scene_def.table["pos"],
        scale=scene_def.table["scale"],
    )
    print(f"  [SCENE] 桌面: {table_path}")

    # 2. 创建每个物体
    for obj in scene_def.objects:
        path = f"/World/{obj['name'].replace(' ', '_')}"
        create_prim(
            prim_path=path,
            prim_type=obj["prim_type"],
            position=obj["position"],
            scale=obj["scale"],
        )

        # 设置颜色
        prim = stage.GetPrimAtPath(path)
        if prim:
            geom = UsdGeom.Gprim(prim)
            geom.GetDisplayColorAttr().Set([Gf.Vec3f(*obj["color"])])

        # 设置语义标签
        try:
            from omni.usd.schema.semantics import SemanticsAPI
            semantics = SemanticsAPI(prim)
            if semantics:
                semantics.SetLabel(obj["label"])
        except Exception:
            pass

        print(f"  [SCENE] {obj['name']}: {path} ({obj['prim_type']}) "
              f"at {obj['position']}")

    # 3. 添加物理属性（PhysX rigid body + collision）
    _add_physics(scene_def)

    return True


def _add_physics(scene_def: SceneDef):
    """为场景物体添加 PhysX 刚体 + 碰撞属性"""
    try:
        from pxr import PhysxSchema
        stage = get_current_stage()

        for obj in scene_def.objects:
            path = f"/World/{obj['name'].replace(' ', '_')}"
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue

            # 刚体
            PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            rigid_api = PhysxSchema.PhysxRigidBodyAPI(prim)
            rigid_api.GetRigidBodyEnabledAttr().Set(True)
            rigid_api.GetMassAttr().Set(obj.get("mass_kg", 0.15))

            # 碰撞
            collision_api = UsdGeom.Gprim(prim)
            collision_api.GetCollisionEnabledAttr().Set(True) if hasattr(
                collision_api, "GetCollisionEnabledAttr"
            ) else None
    except Exception as e:
        print(f"  [WARN] Physics setup skipped (non-critical): {e}")


# ============================================================
# 导出 .usd 文件
# ============================================================
def export_scene_to_usd(scene_name: str, output_dir: str = None) -> Optional[Path]:
    """
    将当前 Stage 导出为 .usd 文件。

    Args:
        scene_name: 场景名 (stacking_cubes / cup_lineup / color_sorting)
        output_dir: 输出目录，默认 src/isaac/scenes/

    Returns:
        导出的 .usd 文件路径
    """
    if not _KIT_MODE:
        print("[MOCK] USD 导出仅在 Kit 模式下可用")
        return None

    if output_dir is None:
        output_dir = Path(__file__).parent / "scenes"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stage = get_current_stage()
    output_path = output_dir / f"{scene_name}.usd"
    stage.Export(str(output_path), False)
    print(f"[EXPORT] 场景已导出: {output_path}")
    return output_path


# ============================================================
# 场景信息（Mock/双模式通用）
# ============================================================
def get_scene_info(scene_name: str) -> Dict[str, Any]:
    """获取场景的元信息（Mock 模式可用）"""
    scene = ALL_SCENES.get(scene_name)
    if not scene:
        return {"error": f"未知场景: {scene_name}", "available": list(ALL_SCENES.keys())}
    return {
        "name": scene.name,
        "description": scene.description,
        "object_count": len(scene.objects),
        "objects": [
            {
                "name": obj["name"],
                "type": obj["prim_type"],
                "position": list(obj["position"]),
                "color_rgb": list(obj["color"]),
                "label": obj["label"],
            }
            for obj in scene.objects
        ],
        "drop_zones": DROP_ZONES.get(scene_name, {}),
    }


def list_scenes() -> List[str]:
    """列出所有可用场景"""
    return list(ALL_SCENES.keys())


def get_scene_def(scene_name: str) -> Optional[SceneDef]:
    """获取场景定义对象"""
    return ALL_SCENES.get(scene_name)


# ============================================================
# 命令行入口
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="标准测试场景构建器")
    parser.add_argument(
        "--scene", type=str, default=None,
        choices=list(ALL_SCENES.keys()) + ["all"],
        help="要构建的场景名",
    )
    parser.add_argument(
        "--export", action="store_true", default=True,
        help="构建后导出 .usd 文件",
    )
    parser.add_argument("--list", action="store_true", help="列出所有场景")
    args = parser.parse_args()

    if args.list:
        print("\n可用标准场景:")
        for name, scene in ALL_SCENES.items():
            print(f"  {name}: {scene.description} ({len(scene.objects)} objects)")
        sys.exit(0)

    if not _KIT_MODE:
        # Mock 模式：打印场景信息
        print("=" * 60)
        print("  场景构建器 (Mock 模式 — 仅打印场景定义)")
        print("  在 Isaac Sim 中运行可生成 .usd 文件:")
        print("    isaacsim.exe --exec scene_builder.py -- --scene stacking_cubes")
        print("=" * 60)

        scenes_to_show = [args.scene] if args.scene and args.scene != "all" else list(ALL_SCENES.keys())
        for name in scenes_to_show:
            info = get_scene_info(name)
            print(f"\n--- {name} ---")
            print(f"  {info['description']}")
            print(f"  物体数: {info['object_count']}")
            for obj in info["objects"]:
                print(f"    - {obj['name']}: {obj['type']} @ {obj['position']}, "
                      f"color=({obj['color_rgb'][0]:.1f},{obj['color_rgb'][1]:.1f},{obj['color_rgb'][2]:.1f})")
            if info["drop_zones"]:
                print(f"  分类区: {info['drop_zones']}")
    else:
        # Kit 模式：真实构建
        print("=" * 60)
        print("  Isaac Sim 场景构建器 (Kit 模式)")
        print("=" * 60)

        scenes_to_build = [args.scene] if args.scene and args.scene != "all" else list(ALL_SCENES.keys())
        for name in scenes_to_build:
            scene = ALL_SCENES[name]
            print(f"\n[BUILD] {name}: {scene.description}")
            _build_scene_in_kit(scene)

            if args.export:
                export_scene_to_usd(name)

        print("\n所有场景构建完成!")
