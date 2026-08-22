"""Container-safe Ground Truth entrypoint with corrected support semantics."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_ground_truth_executor_acceptance as base


_BASE_MANIFEST = base._manifest


def _corrected_manifest():
    manifest = _BASE_MANIFEST()
    for item in manifest:
        if item.get("id") == "zone_unstack_target":
            item["category"] = "桌子"
            attributes = item.setdefault("attributes", {})
            attributes["display_name"] = "桌子"
            attributes["support_surface"] = True
            attributes["purpose"] = "safe_placement"
    return manifest


def main(argv=None):
    original = base._manifest
    base._manifest = _corrected_manifest
    try:
        return base.main(argv)
    finally:
        base._manifest = original


if __name__ == "__main__":
    raise SystemExit(main())
