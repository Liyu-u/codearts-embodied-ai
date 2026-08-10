"""
最小化 Isaac Sim headless 测试 — 验证环境可启动、World/Franka 可加载
"""
import sys
import os
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(SRC_DIR))

print("[TEST] Step 1: Importing isaacsim.core.api...")
from isaacsim.core.api import World
from isaacsim.core.api.simulation_context import SimulationContext
print("[TEST] Step 1 OK")

print("[TEST] Step 2: Creating SimulationContext...")
sim_context = SimulationContext(stage_units_in_meters=1.0)
print("[TEST] Step 2 OK")

print("[TEST] Step 3: Creating World...")
world = World(
    sim_context=sim_context,
    physics_dt=1.0 / 60.0,
    rendering_dt=1.0 / 60.0,
    backend="numpy",
)
print("[TEST] Step 3 OK")

print("[TEST] Step 4: Adding ground plane...")
world.scene.add_default_ground_plane()
print("[TEST] Step 4 OK")

print("[TEST] Step 5: Loading Franka Panda...")
from isaacsim.core.utils.stage import get_current_stage
from isaacsim.storage.native import get_assets_root_path

stage = get_current_stage()
assets_root = get_assets_root_path()
franka_usd = f"{assets_root}/Isaac/Robots/Franka/franka.usd"
print(f"  Franka USD path: {franka_usd}")

prim = stage.DefinePrim("/World/Franka", "Xform")
prim.GetReferences().AddReference(franka_usd)
world.step(render=False)

from isaacsim.core.experimental.prims import Articulation
robot = Articulation(prim_path="/World/Franka")
robot.initialize()
print("[TEST] Step 5 OK — Franka Panda loaded and initialized")

print("\n[TEST] ALL STEPS PASSED")
print("[TEST] Isaac Sim 6.0.1 + Franka Panda — headless verified")

import omni.kit.app
omni.kit.app.get_app().post_quit()
