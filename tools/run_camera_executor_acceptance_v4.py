"""Import-safe entry point for the camera-semantics-enriched C runner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_camera_executor_acceptance_v3 as enriched


if __name__ == "__main__":
    raise SystemExit(enriched.target.main(sys.argv[1:]))
