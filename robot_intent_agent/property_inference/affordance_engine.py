"""
Affordance Engine -- geometry-based capability computation.

Not a lookup table. Calculates graspability, movability from geometry + robot specs.
"""

from typing import Tuple


class AffordanceEngine:
    """
    Compute object affordances from geometry, not from user labels.

    Default robot: Franka Panda gripper
        max_opening: 0.08m
        min_opening: 0.01m
        max_payload:  3.0kg
    """

    MAX_GRIPPER_OPENING_M = 0.08
    MIN_GRIPPER_OPENING_M = 0.01
    MAX_PAYLOAD_KG = 3.0

    def calculate_graspable(self, bbox: Tuple[float, float, float]) -> bool:
        """
        Calculate if object is graspable based on geometry.

        Rule: min(bbox_width, bbox_depth) must be between gripper limits.
        """
        width, height, depth = bbox
        grasp_dim = min(width, depth)  # narrowest dimension for gripper
        return self.MIN_GRIPPER_OPENING_M < grasp_dim <= self.MAX_GRIPPER_OPENING_M

    def calculate_movable(self, bbox: Tuple[float, float, float], material: str = "unknown") -> bool:
        """
        Calculate if object is movable by robot.

        Rule: object must be graspable AND estimated mass <= payload.
        Heavy materials (metal, steel, iron) with large volume are likely too heavy.
        """
        if not self.calculate_graspable(bbox):
            return False

        # Rough mass estimate
        density_map = {
            "metal": 8000, "steel": 7800, "iron": 7800, "aluminum": 2700,
            "glass": 2500, "ceramic": 2500, "optical_glass": 2500,
            "plastic": 1200, "wood": 700, "acrylic": 1200,
            "silicon": 2330, "unknown": 2000,
        }
        density = density_map.get(material.lower(), 2000)
        volume = bbox[0] * bbox[1] * bbox[2]
        mass_est = density * volume

        return mass_est <= self.MAX_PAYLOAD_KG

    def get_force_recommendation(self, fragility_level: int) -> float:
        """Get recommended max force for a given fragility level."""
        force_map = {0: 10.0, 1: 5.0, 2: 3.0, 3: 2.0, 4: 1.5}
        return force_map.get(fragility_level, 10.0)
