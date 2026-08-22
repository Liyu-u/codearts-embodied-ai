"""Explicit Isaac Sim perception adapter.

Kept separate from the default adapter so existing Mock and external-wire
tests remain deterministic.  The remote Isaac entrypoint binds an
``IsaacGroundTruthProvider`` here and then hands the returned perception.v1
scene to the ordinary executor backend.
"""

from __future__ import annotations

from integration.contract_validation import assert_contract
from modules.perception.isaac_ground_truth import IsaacGroundTruthProvider


def run(provider: IsaacGroundTruthProvider) -> dict:
    if not isinstance(provider, IsaacGroundTruthProvider):
        raise TypeError("provider must be IsaacGroundTruthProvider")
    output = provider.observe()
    assert_contract(output, "perception.v1")
    return output


def health(provider: IsaacGroundTruthProvider) -> dict:
    return provider.health()
