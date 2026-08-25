"""Import-safe wrapper for completing D feedback on camera evidence."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import complete_camera_acceptance_report as base


if __name__ == "__main__":
    raise SystemExit(base.main())
