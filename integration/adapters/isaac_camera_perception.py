"""Adapter for real Isaac Sim camera observations.

The camera provider emits the formal external observation contract.  This
adapter keeps the rest of the system on its existing internal perception.v1
contract, exactly like the external observation path used by A.
"""

from __future__ import annotations

from integration.contract_validation import assert_contract
from modules.perception.isaac_camera import IsaacCameraObservationProvider
from modules.perception.observation_normalizer import normalize_observation


def observe(provider: IsaacCameraObservationProvider) -> dict:
    if not isinstance(provider, IsaacCameraObservationProvider):
        raise TypeError("provider must be IsaacCameraObservationProvider")
    output = provider.observe()
    assert_contract(output, "perception_observation.1.0.0")
    return output


def run(provider: IsaacCameraObservationProvider) -> dict:
    """Capture one camera frame and return internal perception.v1."""

    observation = observe(provider)
    scene = normalize_observation(observation)
    assert_contract(scene, "perception.v1")
    return scene


def health(provider: IsaacCameraObservationProvider) -> dict:
    if not isinstance(provider, IsaacCameraObservationProvider):
        raise TypeError("provider must be IsaacCameraObservationProvider")
    return provider.health()
