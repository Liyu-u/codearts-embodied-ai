"""Load optional local environment files without mixing module namespaces.

The repository intentionally keeps A, B and D configuration in separate files:

* ``.env``               -> RIA / A settings (loaded by pydantic-settings)
* ``codearts.env``       -> B CodeArts CLI settings
* ``tracecoder_llm.env`` -> D TraceCoder settings

This helper only loads the requested local file into ``os.environ``. Missing
files and missing python-dotenv remain non-fatal so offline CI keeps working.
"""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_local_env(filename: str, *, override: bool = False) -> bool:
    """Load one ignored, repository-local env file if it exists.

    Returns ``True`` when python-dotenv loaded the file and ``False`` when the
    file is absent or the optional dependency is unavailable.
    """

    path = REPO_ROOT / filename
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return False
    return bool(load_dotenv(dotenv_path=path, override=override))


def load_codearts_env(*, override: bool = False) -> bool:
    """Load the local B/CodeArts configuration file."""

    return load_local_env("codearts.env", override=override)


__all__ = ["REPO_ROOT", "load_codearts_env", "load_local_env"]
